"""The paper execution engine: ONE authoritative paper portfolio.

Components and ownership:
- `Ledger` (ledger.py) owns cash, holdings and history. It is the source of truth.
- ai-hedge-fund's `PaperBroker` (vendored) produces fills and keeps a second,
  independent book file, reconciled against the ledger on every start.
- ai-hedge-fund's `TradingCosts` prices half-spread + square-root impact; this
  module adds a per-order minimum commission (`PaperCosts`).
- TradingAgents keeps no portfolio here: it only receives a read-only
  `PortfolioContext` built from the ledger (see quant.py).

Write-ahead execution: ORDER_INTENT is appended before the broker fills, FILL
after. A crash between the two is recovered on the next start by comparing the
broker book with the ledger: if the book shows the fill, the deterministic fill
is re-derived and recorded; if not, the intent is abandoned and can be retried.

No code path here talks to a network or a real broker. `ALLOWED_BROKERS` is the
whole list of broker classes this engine will construct.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from merlin_quant.calendar import is_trading_day, next_trading_day
from merlin_quant.config import Config
from merlin_quant.contracts import (Action, ExecutionRecord, Mode, PortfolioSnapshot, PositionView,
                                    RiskGateResult, TradeProposal, stable_id)
from merlin_quant.data import MarketData
from merlin_quant.ledger import Ledger, LedgerError
from merlin_quant.vendor.aihf_brokers import Order, PaperBroker, SimBroker, TradingCosts

ALLOWED_BROKERS = (PaperBroker, SimBroker)


class EngineError(RuntimeError):
    pass


class PaperCosts(TradingCosts):
    """ai-hedge-fund's cost model plus a minimum commission per order."""
    min_commission: float = 0.0

    def commission(self, quantity: int) -> float:
        return max(super().commission(quantity), self.min_commission)


def costs_from(cfg: Config) -> PaperCosts:
    return PaperCosts(**cfg.costs.model_dump())


class RecordStore:
    """Machine-readable records, one JSON file per record, write-once."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, kind: str, rec_id: str, obj) -> Path:
        path = self.root / kind / f"{rec_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = obj.model_dump_json(indent=2) if hasattr(obj, "model_dump_json") else json.dumps(obj, indent=2, default=str)
        if path.exists() and kind not in ("proposals",):
            if path.read_text() != text:
                # A record id is a content hash of its inputs: a different body means a bug or a rerun with new
                # outputs (LLM variability). Keep the first and store the rerun beside it.
                alt = path.with_name(f"{rec_id}.rerun-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.json")
                alt.write_text(text)
                return alt
            return path
        path.write_text(text)
        return path

    def get(self, kind: str, rec_id: str) -> dict:
        path = self.root / kind / f"{rec_id}.json"
        if not path.exists():
            raise EngineError(f"no {kind} record {rec_id}")
        return json.loads(path.read_text())

    def list(self, kind: str) -> list[dict]:
        d = self.root / kind
        return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []


class PaperEngine:
    def __init__(self, home: str | Path, cfg: Config, md: MarketData):
        self.home = Path(home)
        self.cfg, self.md = cfg, md
        self.ledger = Ledger(self.home / "ledger" / "events.jsonl")
        self.book_path = self.home / "broker" / "book.json"
        self.records = RecordStore(self.home / "records")
        self.costs = costs_from(cfg)
        self.broker: PaperBroker | None = None
        self.recovery_notes: list[str] = []

    # ------------------------------------------------------------ lifecycle
    def init(self) -> None:
        """Create a new paper portfolio. Refuses to reset an existing one."""
        if self.ledger.events():
            raise EngineError(f"{self.ledger.path} already holds a portfolio; refusing to reset it")
        p = self.cfg.portfolio
        self.ledger.append("INIT", "init", {
            "initial_capital": p.initial_capital, "base_currency": p.base_currency, "mode": self.cfg.mode.value,
            "config_version": self.cfg.version, "universe": p.universe, "benchmark": p.benchmark,
            "data_source": self.md.source_name})
        self.broker = PaperBroker.create(self.book_path, p.initial_capital, self.costs)
        self.records.put("config", self.cfg.version, self.cfg.model_dump(mode="json", by_alias=True))

    def open(self) -> "PaperEngine":
        """Load the ledger and the broker book, recover an interrupted fill, reconcile."""
        st = self.ledger.state()
        if not st.initialized:
            raise EngineError(f"no paper portfolio at {self.home}; run `init` first")
        if not self.book_path.exists():
            raise EngineError(f"broker book {self.book_path} is missing while the ledger exists; refusing to "
                              "recreate it silently")
        self.broker = PaperBroker(self.book_path, self.costs)
        if st.config_version != self.cfg.version:
            self.ledger.append("CONFIG", f"config:{self.cfg.version}", {"config_version": self.cfg.version})
            self.records.put("config", self.cfg.version, self.cfg.model_dump(mode="json", by_alias=True))
        self._recover(st)
        self._ensure_execution_records()
        self.reconcile()
        return self

    def _recover(self, st) -> None:
        for exe_id, intent in list(st.open_intents.items()):
            ledger_shares = st.shares().get(intent["instrument"], 0)
            book_shares = self.broker.positions().get(intent["instrument"])
            book_shares = book_shares.shares if book_shares else 0
            delta = intent["quantity"] if intent["side"] == "buy" else -intent["quantity"]
            if book_shares == ledger_shares + delta:
                fill = self._price(intent)
                self.ledger.append("FILL", f"fill:{exe_id}", {**fill, "recovered": True})
                ref = self.records.get("proposals", intent["proposal_id"])["reference_price"]
                self._write_record(intent, fill, ref)
                self.recovery_notes.append(f"recovered interrupted fill {exe_id} from the broker book")
            elif book_shares == ledger_shares:
                self.ledger.append("ORDER_ABANDONED", f"abandon:{exe_id}",
                                   {"execution_id": exe_id, "reason": "interrupted before the broker filled"})
                self.recovery_notes.append(f"abandoned interrupted order {exe_id} (never filled)")
            else:
                raise EngineError(f"cannot recover {exe_id}: broker book and ledger disagree by an unknown amount")

    def reconcile(self) -> None:
        st = self.ledger.state()
        book = {s: p.shares for s, p in self.broker.positions().items()}
        if book != st.shares():
            raise EngineError(f"RECONCILIATION FAILED: ledger holds {st.shares()} but broker book holds {book}")
        if abs(self.broker.cash() - st.cash) > 0.01:
            raise EngineError(f"RECONCILIATION FAILED: ledger cash {st.cash:.2f} != broker cash {self.broker.cash():.2f}")

    # ------------------------------------------------------------ controls
    def halt(self, reason: str) -> None:
        self.ledger.append("HALT", f"halt:{datetime.now(timezone.utc).isoformat()}", {"reason": reason})

    def resume(self, authorised_by: str) -> None:
        if not authorised_by.strip():
            raise EngineError("resume needs a named authoriser")
        self.ledger.append("RESUME", f"resume:{datetime.now(timezone.utc).isoformat()}", {"authorised_by": authorised_by})

    def set_mode(self, mode: Mode, validation_passed: bool = False) -> None:
        if mode == Mode.PAPER_AUTO:
            if self.cfg.config_status != "OWNER_CONFIGURED":
                raise EngineError("PAPER_AUTO refused: risk config is still EXAMPLE values, not owner-configured")
            if not validation_passed:
                raise EngineError("PAPER_AUTO refused: end-to-end and persistence validation has not passed")
        self.ledger.append("MODE", f"mode:{mode.value}:{datetime.now(timezone.utc).isoformat()}", {"mode": mode.value})

    # ------------------------------------------------------------ valuation
    def snapshot(self, as_of: date) -> PortfolioSnapshot:
        st = self.ledger.state()
        positions, stale, mv_total, unreal = [], [], 0.0, 0.0
        for sym, h in sorted(st.holdings.items()):
            if not h.quantity:
                continue
            mark = self.md.raw_close(sym, as_of)
            if mark is None or (mark[1] < as_of and is_trading_day(as_of)):
                stale.append(sym)
            px, d = mark if mark else (None, None)
            mv = px * h.quantity if px is not None else None
            if mv is not None:
                mv_total += mv
                unreal += mv - h.cost
            positions.append(PositionView(instrument=sym, quantity=h.quantity, avg_cost=round(h.avg_cost, 4),
                                          mark=px, mark_date=d, market_value=mv,
                                          unrealized_pnl=None if mv is None else round(mv - h.cost, 2), weight=None))
        nav = st.cash + mv_total
        for p in positions:
            p.weight = None if p.market_value is None else round(p.market_value / nav, 4)
        return PortfolioSnapshot(
            as_of=as_of, base_currency=st.base_currency, cash=round(st.cash, 2), positions=positions,
            nav=round(nav, 2), realized_pnl=round(st.realized_pnl, 2), unrealized_pnl=round(unreal, 2),
            fees_paid=round(st.fees, 2), dividends_received=round(st.dividends, 2),
            gross_exposure=round(mv_total / nav, 4) if nav else 0.0, initial_capital=st.initial_capital,
            halted=st.halted, mode=Mode(st.mode), ledger_head=self.ledger.head(), stale_marks=stale)

    def record_nav(self, as_of: date) -> dict:
        snap = self.snapshot(as_of)
        bench = self.md.raw_close(self.cfg.portfolio.benchmark, as_of)
        payload = {"date": as_of.isoformat(), "nav": snap.nav, "cash": snap.cash,
                   "benchmark": self.cfg.portfolio.benchmark, "benchmark_close": bench[0] if bench else None,
                   "stale_marks": snap.stale_marks}
        ev, _ = self.ledger.append("NAV", f"nav:{as_of.isoformat()}", payload)
        return ev["payload"]

    def apply_corporate_actions(self, through: date) -> list[str]:
        """Apply splits (ex-date) and dividends (pay date) up to *through*, in ex-date order.

        Each action applies only to the shares held at the open of its ex-date (fills on earlier
        sessions, after earlier splits). Shares bought on or after the ex-date already trade on the
        new basis, so a past split never touches them. A split with no eligible shares is still
        recorded (adding 0), so it can never apply to a later purchase.
        """
        applied = []
        todo = sorted(((a["ex_date"], sym, a) for sym, acts in self.md.actions.items() for a in acts),
                      key=lambda x: (x[0], x[1]))
        for ex, sym, a in todo:
            aid = f"{sym}:{a['type']}:{ex}"
            if aid in self.ledger.state().applied_actions:
                continue
            if a["type"] == "split" and ex <= through.isoformat():
                eligible = self._shares_at(sym, date.fromisoformat(ex))
                added = int(round(eligible * a["ratio"])) - eligible
                self.ledger.append("CORPORATE_ACTION", f"ca:{aid}", {"action_id": aid, "instrument": sym,
                                   "type": "split", "ratio": a["ratio"], "ex_date": ex,
                                   "eligible_shares": eligible, "added_shares": added})
                if added:
                    book = {s_: p.shares for s_, p in self.broker.positions().items()}
                    book[sym] = book.get(sym, 0) + added
                    self.broker = PaperBroker.restore(self.book_path, self.broker.cash(), book, self.costs)
                applied.append(aid)
            elif a["type"] == "dividend" and a["pay_date"] <= through.isoformat():
                held = self._shares_at(sym, date.fromisoformat(ex))
                cash = round(held * a["amount"], 2)
                self.ledger.append("CORPORATE_ACTION", f"ca:{aid}", {"action_id": aid, "instrument": sym,
                                   "type": "dividend", "per_share": a["amount"], "shares": held, "cash": cash,
                                   "ex_date": ex, "pay_date": a["pay_date"]})
                if cash:
                    book = {s_: p.shares for s_, p in self.broker.positions().items()}
                    self.broker = PaperBroker.restore(self.book_path, self.broker.cash() + cash, book, self.costs)
                applied.append(aid)
        return applied

    def _shares_at(self, sym: str, ex_date: date) -> int:
        """Shares held at the open of *ex_date*: fills on earlier sessions plus earlier splits' additions."""
        q, ex = 0, ex_date.isoformat()
        for e in self.ledger.events():
            p = e["payload"]
            if e["type"] == "FILL" and p["instrument"] == sym and p["fill_session"] < ex:
                q += p["quantity"] if p["side"] == "buy" else -p["quantity"]
            elif (e["type"] == "CORPORATE_ACTION" and p["instrument"] == sym and p["type"] == "split"
                  and p["ex_date"] < ex):
                q += p["added_shares"]
        return q

    def _ensure_execution_records(self) -> None:
        """Rebuild any execution record missing for a FILL already committed to the ledger
        (a stop after the FILL append but before the record was written)."""
        intents = {e["payload"]["execution_id"]: e["payload"] for e in self.ledger.events() if e["type"] == "ORDER_INTENT"}
        for e in self.ledger.events():
            if e["type"] != "FILL":
                continue
            fill = e["payload"]
            if (self.records.root / "executions" / f"{fill['execution_id']}.json").exists():
                continue
            intent = intents.get(fill["execution_id"])
            if intent is None:
                raise EngineError(f"FILL {fill['execution_id']} has no ORDER_INTENT in the ledger")
            ref = self.records.get("proposals", fill["proposal_id"])["reference_price"]
            self._write_record(intent, fill, ref)
            self.recovery_notes.append(f"rebuilt missing execution record {fill['execution_id']}")

    # ------------------------------------------------------------ execution
    def fill_session_for(self, data_cutoff: date) -> date:
        return next_trading_day(data_cutoff)

    def _liquidity(self, sym: str, cutoff: date) -> tuple[float | None, float | None]:
        rows = self.md.view(cutoff).bars(sym)[-21:]
        if len(rows) < 21:
            return None, None
        adv = sum(r["volume"] for r in rows[1:]) / 20
        rets = [math.log(rows[i]["close"] / rows[i - 1]["close"]) for i in range(1, len(rows))]
        mu = sum(rets) / len(rets)
        sigma = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        return adv, sigma

    def _price(self, intent: dict) -> dict:
        """The deterministic fill for an intent (same inputs, same fill)."""
        order = Order(ticker=intent["instrument"], side=intent["side"], quantity=intent["quantity"],
                      price=intent["open_price"], adv=intent.get("adv"), volatility=intent.get("volatility"))
        px = self.costs.fill_price(order.side, order.price, order.participation, order.volatility or 0.0)
        fee = self.costs.commission(order.quantity)
        return {"execution_id": intent["execution_id"], "proposal_id": intent["proposal_id"],
                "instrument": intent["instrument"], "side": intent["side"], "quantity": intent["quantity"],
                "fill_price": px, "commission": fee, "fill_session": intent["fill_session"],
                "open_price": intent["open_price"]}

    def execute(self, proposal: TradeProposal, risk: RiskGateResult, *, crash_after_intent: bool = False) -> ExecutionRecord:
        if risk.proposal_id != proposal.proposal_id or risk.outcome != "PASS":
            raise EngineError(f"proposal {proposal.proposal_id} has no passing risk result; not executed")
        exe_id = stable_id("exe", {"proposal": proposal.proposal_id})
        if proposal.proposal_id in self.ledger.state().filled:  # idempotent rerun: no second transaction
            self._ensure_execution_records()
            return ExecutionRecord.model_validate(self.records.get("executions", exe_id))
        if proposal.status != "APPROVED":
            raise EngineError(f"proposal {proposal.proposal_id} is {proposal.status}, not APPROVED")
        if proposal.action not in (Action.BUY, Action.SELL, Action.REDUCE):
            raise EngineError(f"{proposal.action.value} is not an executable action")
        st = self.ledger.state()
        if st.halted:
            raise EngineError(f"paper engine is halted: {st.halt_reason}")
        if not isinstance(self.broker, ALLOWED_BROKERS):
            raise EngineError("refusing to execute through a non-paper broker")

        session = self.fill_session_for(proposal.data_cutoff)
        bar = self.md.execution_bar(proposal.instrument, session, proposal.data_cutoff)
        adv, vol = self._liquidity(proposal.instrument, proposal.data_cutoff)
        side = "buy" if proposal.action == Action.BUY else "sell"
        intent = {"execution_id": exe_id, "proposal_id": proposal.proposal_id, "risk_result_id": risk.result_id,
                  "instrument": proposal.instrument, "side": side, "quantity": proposal.quantity,
                  "fill_session": session.isoformat(), "open_price": bar["open"], "adv": adv, "volatility": vol}
        self.ledger.append("ORDER_INTENT", f"intent:{exe_id}", intent)

        order = Order(ticker=proposal.instrument, side=side, quantity=proposal.quantity, price=bar["open"],
                      adv=adv, volatility=vol)
        fill = self.broker.place_order(order)  # PaperBroker: persisted atomically
        if crash_after_intent:
            raise EngineError("SIMULATED CRASH after the broker filled and before the ledger recorded the fill")
        priced = self._price(intent)
        if abs(priced["fill_price"] - fill.price) > 1e-9:
            raise LedgerError("broker fill price differs from the deterministic fill model")
        self.ledger.append("FILL", f"fill:{exe_id}", priced)
        self.reconcile()

        return self._write_record(intent, priced, proposal.reference_price)

    def _write_record(self, intent: dict, priced: dict, reference_price: float) -> ExecutionRecord:
        q, px, fee, side = intent["quantity"], priced["fill_price"], priced["commission"], intent["side"]
        gross = q * px
        rec = ExecutionRecord(
            execution_id=intent["execution_id"], proposal_id=intent["proposal_id"],
            risk_result_id=intent["risk_result_id"], instrument=intent["instrument"], side=side, quantity=q,
            reference_price=reference_price, fill_session=date.fromisoformat(intent["fill_session"]),
            fill_basis=f"{intent['fill_session']} session open ({intent['open_price']}) + {self.costs.spread_bps} bps "
                       f"half-spread + square-root impact; commission max({self.costs.commission_per_share}/sh, "
                       f"{self.costs.min_commission})",
            fill_price=round(px, 6), commission=round(fee, 4),
            slippage_cost=round(abs(px - intent["open_price"]) * q, 4), gross_value=round(gross, 2),
            net_cash_change=round(-(gross + fee) if side == "buy" else gross - fee, 2))
        self.records.put("executions", intent["execution_id"], rec)
        return rec
