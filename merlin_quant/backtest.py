"""Limited historical backtest (separate from the forward paper ledger).

Strategy under test: Quant's deterministic technical rule (`quant.technical_stance`)
— BULLISH names held at the policy's full target weight, everything else flat —
evaluated every `every` sessions on a point-in-time view, filled at the next
session's open through ai-hedge-fund's SimBroker with the configured cost model,
under the same position and gross caps as the risk gate. LLM leads are not run
per backtest date (cost); scoring their live recommendations is
`performance.score_research`.

Why not TradingAgents' `backtest.run_backtest`: it scores the rating of one LLM
run per (ticker, date) cell and, by its own docstring, is not a portfolio
simulator. It answers "research recommendation scoring", not "portfolio
backtest", and costs one full graph run per cell.
"""

from __future__ import annotations

import math
from datetime import date

from merlin_quant.calendar import next_trading_day, trading_days
from merlin_quant.config import Config
from merlin_quant.contracts import Stance
from merlin_quant.data import MarketData
from merlin_quant.engine import costs_from
from merlin_quant.performance import LIMITATIONS, series_metrics, trade_stats
from merlin_quant.quant import indicators, technical_stance
from merlin_quant.vendor.aihf_brokers import Order, SimBroker


def run_backtest(md: MarketData, cfg: Config, start: date, end: date, every: int = 5) -> dict:
    bench = cfg.portfolio.benchmark
    w_target = min(cfg.decision_policy.full_target_weight, cfg.risk.max_position_weight)
    broker = SimBroker(cfg.portfolio.initial_capital, costs_from(cfg))
    sessions = trading_days(start, end)
    decisions, trades, navs, bvals, dates = [], [], [], [], []
    cost_basis: dict[str, float] = {}
    closed: list[dict] = []
    pending: list[tuple[date, list[Order]]] = []

    def mark(d):
        nav = broker.cash()
        for s, p in broker.positions().items():
            nav += p.shares * md.raw_close(s, d)[0]
        return nav

    for i, d in enumerate(sessions):
        # 1) fill orders decided on the previous session, at today's open
        for when, orders in [x for x in pending if x[0] == d]:
            for o in orders:
                bar = md.execution_bar(o.ticker, d, decision_cutoff=next_prev(d, sessions))
                o = Order(ticker=o.ticker, side=o.side, quantity=o.quantity, price=bar["open"])
                fill = broker.place_order(o)
                fee = broker._costs.commission(o.quantity)
                if o.side == "buy":
                    cost_basis[o.ticker] = cost_basis.get(o.ticker, 0.0) + fill.price * o.quantity + fee
                else:
                    held_before = (broker.positions().get(o.ticker).shares if broker.positions().get(o.ticker) else 0) + o.quantity
                    basis = cost_basis.get(o.ticker, 0.0) * o.quantity / held_before
                    cost_basis[o.ticker] = cost_basis.get(o.ticker, 0.0) - basis
                    closed.append({"instrument": o.ticker, "pnl": fill.price * o.quantity - fee - basis, "session": d.isoformat()})
                trades.append({"session": d.isoformat(), "instrument": o.ticker, "side": o.side,
                               "quantity": o.quantity, "fill_price": round(fill.price, 4), "commission": fee})
        pending = [x for x in pending if x[0] != d]
        # 2) apply splits on their ex-date to the simulated book (shares x ratio)
        for s, acts in md.actions.items():
            for a in acts:
                if a["type"] == "split" and a["ex_date"] == d.isoformat() and s in broker.positions():
                    broker._shares[s] = int(round(broker._shares[s] * a["ratio"]))
        navs.append(mark(d))
        bvals.append(md.raw_close(bench, d)[0])
        dates.append(d.isoformat())
        # 3) decide on this session's close (point-in-time), every `every` sessions
        if i % every or i == len(sessions) - 1:
            continue
        view = md.view(d)
        nav = navs[-1]
        orders = []
        for s in cfg.portfolio.universe:
            try:
                stance, _ = technical_stance(indicators(view, s, bench))
            except Exception:
                continue
            px = view.close(s, 5)[0]
            want = math.floor(w_target * nav / px) if stance == Stance.BULLISH else 0
            have = broker.positions().get(s).shares if broker.positions().get(s) else 0
            decisions.append({"date": d.isoformat(), "instrument": s, "stance": stance.value, "target_qty": want})
            if want > have:
                orders.append(Order(ticker=s, side="buy", quantity=want - have, price=px))
            elif want < have:
                orders.append(Order(ticker=s, side="sell", quantity=have - want, price=px))
        orders.sort(key=lambda o: o.side != "sell")  # sells before buys
        if orders:
            pending.append((next_trading_day(d), orders))
    # split-adjust the benchmark? SPY has no actions in the fixture; adjust generally via view at end
    adj_bench = {b["date"]: b["close"] for b in md.view(sessions[-1]).bars(bench)}
    bvals = [adj_bench[x] for x in dates]
    return {"kind": "HISTORICAL_BACKTEST", "data_source": md.source_name, "start": start.isoformat(),
            "end": end.isoformat(), "rebalance_every_sessions": every, "strategy": "quant technical rule",
            "metrics": series_metrics(navs, bvals), "trade_stats": trade_stats(closed), "trades": trades,
            "nav": list(zip(dates, [round(v, 2) for v in navs])), "decisions": decisions,
            "limitations": LIMITATIONS}


def next_prev(d: date, sessions: list[date]) -> date:
    i = sessions.index(d)
    return sessions[i - 1] if i else d
