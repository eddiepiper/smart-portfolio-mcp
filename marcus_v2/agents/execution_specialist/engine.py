"""Deterministic paper-execution engine (daily bars, LEAN-compatible fill semantics).

Pure functions: given an order, the validated bars that came AFTER the decision,
cash and the mandate, return exactly one outcome. No price is ever substituted:
if the bar needed to prove a fill is missing, rejected or provisional, the
outcome is UNVERIFIABLE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ...core.contracts import (FillStatus, InvestmentMandate, MarketObservation, OrderType, PaperFill, PaperOrder,
                               PriceType, QualityStatus)
from ...core.sessions import is_trading_day

CENT = Decimal("0.01")


@dataclass
class CostModel:
    commission_pct: Decimal = Decimal("0.0008")     # 0.08% (the user's broker schedule)
    min_commission: Decimal = Decimal("15")          # USD 15 minimum per US trade
    slippage_pct: Decimal = Decimal("0.0005")        # applied to market and stop fills only
    currency: str = "USD"

    def commission(self, notional: Decimal) -> Decimal:
        return max(self.min_commission, (notional * self.commission_pct)).quantize(CENT, ROUND_HALF_UP)


@dataclass
class Context:
    cash_available: Decimal                 # in the order currency
    portfolio_equity: Decimal               # in the order currency, for limit checks
    position_quantity: Decimal = Decimal("0")
    processed_order_ids: set[str] = field(default_factory=set)
    approved: bool = False                  # owner approval of the originating recommendation
    active_mandate: Optional[InvestmentMandate] = None


def _usable(o: MarketObservation) -> bool:
    return (o.validation_status in (QualityStatus.VALID, QualityStatus.WARNING)
            and o.price_type in (PriceType.OFFICIAL_CLOSE, PriceType.HISTORICAL)
            and None not in (o.open, o.high, o.low, o.close))


def simulate(order: PaperOrder, bars: list[MarketObservation], ctx: Context, costs: CostModel) -> PaperFill:
    tested: list[str] = []

    def out(status: FillStatus, reason: str, **kw) -> PaperFill:
        return PaperFill(order_id=order.order_id, status=status, reason=reason, constraints_tested=tested, **kw)

    # 1. order validity ------------------------------------------------------------------
    tested.append("paper_only")
    if order.paper is not True:
        return out(FillStatus.REJECTED, "not a paper order")
    tested.append("duplicate_order_id")
    if order.order_id in ctx.processed_order_ids:
        return out(FillStatus.REJECTED, f"duplicate order id {order.order_id}: already processed")
    tested.append("owner_approval")
    if not ctx.approved:
        return out(FillStatus.REJECTED, "originating recommendation is not owner-approved")
    tested.append("mandate_version")
    m = ctx.active_mandate
    if m is None or (m.mandate_id, m.version) != (order.mandate_id, order.mandate_version):
        return out(FillStatus.REJECTED, "order does not reference the active approved mandate version")
    tested.append("quantity")
    if order.quantity <= 0 or order.quantity != order.quantity.to_integral_value():
        return out(FillStatus.REJECTED, "quantity must be a positive whole number of shares")
    tested.append("order_fields")
    if order.order_type == OrderType.LIMIT and not order.limit_price:
        return out(FillStatus.REJECTED, "limit order without a limit price")
    if order.order_type == OrderType.STOP and not order.stop_price:
        return out(FillStatus.REJECTED, "stop order without a stop price")
    if order.side == "sell":
        tested.append("no_short")
        if order.quantity > ctx.position_quantity and not m.allow_short:
            return out(FillStatus.REJECTED, f"sell {order.quantity} exceeds position {ctx.position_quantity}; shorting not allowed")
    if order.currency != costs.currency:
        return out(FillStatus.REJECTED, f"order currency {order.currency} != cost model currency {costs.currency}")

    # 2. bar timeline: only bars strictly after the decision date, validated and final ---------
    tested.append("timestamps")
    decision_day = order.decision_at.date()
    for b in bars:
        if b.instrument_id != order.instrument_id:
            return out(FillStatus.UNVERIFIABLE, f"bar for {b.instrument_id} supplied for {order.instrument_id}")
    later = sorted((b for b in bars if b.bar_date and b.bar_date > decision_day), key=lambda b: b.bar_date)
    if any(b.price_type == PriceType.PROVISIONAL_INTRADAY for b in later):
        later = [b for b in later if b.price_type != PriceType.PROVISIONAL_INTRADAY]
    expires = order.expires_on

    def check_continuity(seq: list[MarketObservation]) -> Optional[str]:
        """Every trading day between decision and the bar used must be present and usable."""
        d = decision_day + timedelta(days=1)
        have = {b.bar_date: b for b in seq}
        last = seq[-1].bar_date if seq else d
        while d <= last:
            if is_trading_day(d) and (d not in have or not _usable(have[d])):
                return f"bar for trading day {d} missing or not validated"
            d += timedelta(days=1)
        return None

    # 3. trigger (e.g. daily close above a level): evaluated on closes, executes next session ---
    start_idx = 0
    if order.trigger:
        tested.append("trigger_condition")
        t = order.trigger
        level = Decimal(str(t["level"]))
        hit = None
        for i, b in enumerate(later):
            if expires and b.bar_date > expires:
                break
            if not _usable(b):
                return out(FillStatus.UNVERIFIABLE, f"trigger cannot be evaluated: bar {b.bar_date} not usable")
            if (t["type"] == "daily_close_above" and b.close > level) or (t["type"] == "daily_close_below" and b.close < level):
                hit = i
                break
        if hit is None:
            if expires and (not later or later[-1].bar_date >= expires):
                return out(FillStatus.EXPIRED, f"trigger {t['type']} {level} not met by {expires}")
            return out(FillStatus.NO_FILL, f"trigger {t['type']} {level} not met in available data")
        start_idx = hit + 1                      # trigger observed at the close; earliest fill is the next session
        tested.append(f"trigger_met_on_{later[hit].bar_date}")

    # 4. walk sessions for a fill ------------------------------------------------------------
    for b in later[start_idx:]:
        if expires and b.bar_date > expires:
            return out(FillStatus.EXPIRED, f"order expired {expires} unfilled")
        if not _usable(b):
            return out(FillStatus.UNVERIFIABLE, f"execution bar {b.bar_date} is not validated final data; no price substituted")
        gap = check_continuity([x for x in later if x.bar_date <= b.bar_date])
        if gap:
            return out(FillStatus.UNVERIFIABLE, gap)
        price, slip = _fill_price(order, b, costs)
        if price is None:
            continue
        # 5. no-chase ----------------------------------------------------------------------------
        if order.side == "buy" and order.planned_entry:
            tested.append("no_chase")
            ceiling = (order.planned_entry * (1 + m.no_chase_pct / 100)).quantize(CENT)
            if price > ceiling:
                return out(FillStatus.NO_FILL, f"would fill at {price} > no-chase ceiling {ceiling} (planned {order.planned_entry})",
                           bar_date=b.bar_date)
        notional = (price * order.quantity).quantize(CENT, ROUND_HALF_UP)
        fee = costs.commission(notional)
        # 6. cash and portfolio limits ------------------------------------------------------------
        if order.side == "buy":
            tested.append("available_cash")
            buffer = (ctx.portfolio_equity * m.cash_buffer_pct / 100).quantize(CENT)
            if notional + fee > ctx.cash_available - buffer:
                return out(FillStatus.REJECTED, f"insufficient paper cash: need {notional + fee} + buffer {buffer}, have {ctx.cash_available}",
                           bar_date=b.bar_date)
            tested.append("position_limit")
            if notional > ctx.portfolio_equity * m.max_position_pct / 100:
                return out(FillStatus.REJECTED, f"position {notional} exceeds {m.max_position_pct}% of equity {ctx.portfolio_equity}")
        return out(FillStatus.FILLED, f"{order.order_type.value} {order.side} filled on {b.bar_date}",
                   quantity=order.quantity, price=price, fees=fee, slippage=slip, bar_date=b.bar_date,
                   price_source=b.source_provider)
    if expires and later and later[-1].bar_date >= expires:
        return out(FillStatus.EXPIRED, f"order expired {expires} unfilled")
    return out(FillStatus.NO_FILL, "no session in the available data satisfied the order")


def _fill_price(order: PaperOrder, b: MarketObservation, costs: CostModel) -> tuple[Optional[Decimal], Decimal]:
    o, h, l = b.open, b.high, b.low
    if order.order_type == OrderType.MARKET:
        slip = (o * costs.slippage_pct).quantize(CENT, ROUND_HALF_UP)
        return (o + slip if order.side == "buy" else o - slip), slip
    if order.order_type == OrderType.LIMIT:
        lim = order.limit_price
        if order.side == "buy" and l <= lim:
            return min(o, lim), Decimal("0")
        if order.side == "sell" and h >= lim:
            return max(o, lim), Decimal("0")
        return None, Decimal("0")
    if order.order_type == OrderType.STOP:
        st = order.stop_price
        if order.side == "sell" and l <= st:
            base = min(o, st)                     # gap through the stop fills at the open, not the stop
            slip = (base * costs.slippage_pct).quantize(CENT, ROUND_HALF_UP)
            return base - slip, slip
        if order.side == "buy" and h >= st:
            base = max(o, st)
            slip = (base * costs.slippage_pct).quantize(CENT, ROUND_HALF_UP)
            return base + slip, slip
    return None, Decimal("0")
