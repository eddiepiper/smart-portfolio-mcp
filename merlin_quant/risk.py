"""The single deterministic portfolio risk gate.

Every paper order passes here; nothing else can authorise execution. Inputs are
the typed proposal, the ledger state, market data and the config file. No LLM
text is read. Outcomes:

- PASS     every mandatory check passed;
- REJECT   the proposal itself breaks a limit (size, cash, exposure, duplicate...);
- BLOCKED  the system cannot trade right now (halted, research-only mode, stale
           or missing data, closed calendar, drawdown brake, instrument halt).

A failed check is recorded with its reason and the trade is not executed.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from merlin_quant.calendar import CalendarError, is_trading_day, next_trading_day
from merlin_quant.config import Config
from merlin_quant.contracts import Action, Mode, RiskCheck, RiskGateResult, TradeProposal, stable_id
from merlin_quant.data import DataUnavailable, MarketData, StaleData
from merlin_quant.ledger import State

BLOCKING = {"engine_not_halted", "mode_allows_execution", "data_available", "data_freshness",
            "trading_calendar", "drawdown_threshold", "instrument_not_halted",
            "checks_before_buying"}


def evaluate(proposal: TradeProposal, st: State, cfg: Config, md: MarketData, *,
             last_completed_session: date, executed_proposals: set[str], orders_this_session: int,
             now: datetime | None = None) -> RiskGateResult:
    r, checks = cfg.risk, []

    def check(name: str, ok: bool, detail: str):
        checks.append(RiskCheck(name=name, passed=bool(ok), detail=detail))

    sym, act = proposal.instrument, proposal.action
    trade = act in (Action.BUY, Action.SELL, Action.REDUCE)

    check("engine_not_halted", not st.halted, "halted: " + st.halt_reason if st.halted else "engine running")
    mode = Mode(st.mode)
    check("mode_allows_execution", mode != Mode.RESEARCH_ONLY, f"mode {mode.value}")

    # 1 eligibility
    check("instrument_eligibility", sym in cfg.portfolio.universe,
          f"{sym} {'is' if sym in cfg.portfolio.universe else 'is NOT'} in the configured universe")
    check("action_executable", trade, f"{act.value} {'is' if trade else 'is not'} an executable action")

    # 8/9 data availability and freshness, 9 stale or missing price
    price, price_date, view = None, None, None
    try:
        view = md.view(proposal.data_cutoff)
        price, price_date = view.close(sym, r.max_price_staleness_sessions)
        check("data_available", True, f"source {md.source_name}")
    except DataUnavailable as exc:
        check("data_available", False, str(exc))
    except StaleData as exc:
        check("data_available", True, f"source {md.source_name}")
        check("price_not_stale", False, str(exc))
    fresh = proposal.data_cutoff == last_completed_session
    check("data_freshness", fresh, f"proposal data cutoff {proposal.data_cutoff}; last completed session "
          f"{last_completed_session}")
    if price is not None:
        check("price_not_stale", True, f"close {price} on {price_date}")
        dev = abs(price - proposal.reference_price) / price
        check("reference_price_matches_data", dev <= r.reference_price_tolerance and price_date == proposal.reference_price_date,
              f"proposal {proposal.reference_price} on {proposal.reference_price_date} vs data {price} on {price_date}")

    # 10 calendar
    try:
        fill_session = next_trading_day(proposal.data_cutoff)
        ok = is_trading_day(fill_session) and fill_session > proposal.data_cutoff
        check("trading_calendar", ok, f"fill at the {fill_session} open (next NYSE session after the cutoff)")
    except CalendarError as exc:
        fill_session = None
        check("trading_calendar", False, str(exc))

    # 12 instrument halt on the fill session
    halted_sym = bool(fill_session and md.halts.get(sym) and fill_session.isoformat() in md.halts[sym])
    check("instrument_not_halted", not halted_sym, f"{sym} {'HALTED' if halted_sym else 'not halted'} on {fill_session}")

    # valuation of the book at the cutoff (raw closes)
    nav, mv, sector_mv = st.cash, {}, {}
    for s, h in st.holdings.items():
        if h.quantity:
            m = md.raw_close(s, proposal.data_cutoff)
            mv[s] = h.quantity * (m[0] if m else 0.0)
            nav += mv[s]
            sec = cfg.portfolio.sectors.get(s, s)
            sector_mv[sec] = sector_mv.get(sec, 0.0) + mv[s]
    held = st.holdings.get(sym).quantity if st.holdings.get(sym) else 0
    px = proposal.reference_price
    notional = proposal.quantity * px
    est_fee = max(proposal.quantity * cfg.costs.commission_per_share, cfg.costs.min_commission)
    est_cost = notional * (1 + cfg.costs.spread_bps / 10_000) + est_fee

    # 13 order size
    size_ok = (proposal.quantity > 0 and proposal.quantity <= r.max_order_quantity
               and notional <= r.max_order_value and notional >= r.min_order_value) if trade else False
    check("order_size_valid", size_ok, f"{proposal.quantity} sh x {px} = {notional:,.2f}; limits qty<="
          f"{r.max_order_quantity}, {r.min_order_value:,.0f}<=value<={r.max_order_value:,.0f}")

    # 3 existing positions
    if act == Action.BUY:
        check("existing_position", True, f"holding {held}; buy adds {proposal.quantity}")
        new_qty = held + proposal.quantity
    else:
        ok = proposal.quantity <= held
        check("existing_position", ok, f"holding {held}; {act.value} of {proposal.quantity} "
              f"{'ok' if ok else 'exceeds holding (no shorting)'}")
        new_qty = held - proposal.quantity

    # 2 cash
    if act == Action.BUY:
        avail = st.cash - r.cash_buffer_pct * nav
        check("available_cash", est_cost <= avail, f"needs {est_cost:,.2f}; available after "
              f"{r.cash_buffer_pct:.0%} buffer {avail:,.2f}")
    else:
        check("available_cash", True, "sell/reduce releases cash")

    # 4/5/6 exposure and concentration (post-trade)
    post_pos = new_qty * px
    post_mv_total = sum(v for s, v in mv.items() if s != sym) + post_pos
    w = post_pos / nav if nav else math.inf
    check("max_position_exposure", act != Action.BUY or w <= r.max_position_weight + 1e-12,
          f"post-trade {sym} weight {w:.2%} vs limit {r.max_position_weight:.0%}")
    g = post_mv_total / nav if nav else math.inf
    check("max_gross_exposure", act != Action.BUY or g <= r.max_gross_exposure + 1e-12,
          f"post-trade gross {g:.2%} vs limit {r.max_gross_exposure:.0%}")
    sec = cfg.portfolio.sectors.get(sym, sym)
    sw = (sector_mv.get(sec, 0.0) - mv.get(sym, 0.0) + post_pos) / nav if nav else math.inf
    check("concentration_limit", act != Action.BUY or sw <= r.max_sector_weight + 1e-12,
          f"post-trade sector '{sec}' weight {sw:.2%} vs limit {r.max_sector_weight:.0%}")

    # 7 duplicates and order count
    dup = proposal.proposal_id in executed_proposals
    check("duplicate_order", not dup, "already executed" if dup else "no prior execution of this proposal")
    check("orders_per_session", orders_this_session < r.max_orders_per_session,
          f"{orders_this_session} order(s) already this session; limit {r.max_orders_per_session}")

    # 11 drawdown (blocks adding risk only)
    peak = max([st.initial_capital] + [n["nav"] for n in st.nav_history])
    dd = 1 - nav / peak if peak else 0.0
    check("drawdown_threshold", act != Action.BUY or dd < r.max_drawdown_halt,
          f"drawdown {dd:.2%} from peak {peak:,.2f}; new buys stop at {r.max_drawdown_halt:.0%}")

    # 14 checks before buying (merlin_quant.guards), re-run at execution so an older BUY proposal made before a
    # report, before new results reached the SEC data, or before a sell-off cannot slip through. Buys only.
    if act == Action.BUY and view is not None:
        from merlin_quant import guards
        try:
            gr = guards.evaluate(view, sym, cfg, now=now)
            check("checks_before_buying", not gr.blocking, gr.wait_reason() if gr.blocking else
                  "no blocking check (" + "; ".join(f"{x.name} {x.outcome}" for x in gr.results) + ")")
        except DataUnavailable as exc:
            check("checks_before_buying", False, f"could not run the checks: {exc}")

    failed = [c for c in checks if not c.passed]
    if not failed:
        outcome = "PASS"
    elif any(c.name in BLOCKING for c in failed):
        outcome = "BLOCKED"
    else:
        outcome = "REJECT"
    now = now or datetime.now(timezone.utc)
    return RiskGateResult(
        result_id=stable_id("risk", {"p": proposal.proposal_id, "cfg": cfg.version, "checks": [c.model_dump() for c in checks]}),
        proposal_id=proposal.proposal_id, outcome=outcome, checks=checks,
        reasons=[f"{c.name}: {c.detail}" for c in failed], config_version=cfg.version, evaluated_at=now)
