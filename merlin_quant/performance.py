"""Performance measurement, kept as three separate things:

1. Forward paper trading  — NAV snapshots in the ledger (append-only), vs benchmark.
2. Historical backtest    — backtest.py, a separate simulated book (never the ledger).
3. Research scoring       — did each lead's stance point the right way over the next N
                            sessions? Scored only once N sessions have traded.

Small samples are flagged: no profitability claim is made from a handful of trades,
and risk-adjusted ratios are reported only with at least 20 return observations.
"""

from __future__ import annotations

import math
from datetime import date

from merlin_quant.calendar import trading_days
from merlin_quant.data import MarketData

MIN_OBS_FOR_RATIOS = 20
LIMITATIONS = [
    "Synthetic fixture data is not market history; results say nothing about real performance.",
    "Small sample: a handful of simulated trades cannot establish profitability or skill.",
    "Survivorship bias: the universe is fixed today's names; delisted names are absent.",
    "Look-ahead risk is controlled by point-in-time views, but restated filings are not modelled (data revisions).",
    "LLM outputs vary between runs (model variability); the same inputs may give a different narrative or stance.",
    "Fills are simulated at the next session open plus modelled costs; no market fill occurred.",
]


def series_metrics(navs: list[float], bench: list[float] | None = None) -> dict:
    out = {"observations": len(navs)}
    if len(navs) < 2:
        out["note"] = "fewer than two NAV points"
        return out
    rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    out["total_return"] = navs[-1] / navs[0] - 1
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    out["max_drawdown"] = mdd
    if len(rets) >= MIN_OBS_FOR_RATIOS:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        out["ann_volatility"] = sd * math.sqrt(252)
        out["sharpe_rf0"] = (mu / sd * math.sqrt(252)) if sd > 0 else None
    else:
        out["ann_volatility"] = out["sharpe_rf0"] = None
        out["ratios_note"] = f"risk-adjusted ratios need >= {MIN_OBS_FOR_RATIOS} return observations"
    if bench and len(bench) == len(navs) and all(bench):
        out["benchmark_return"] = bench[-1] / bench[0] - 1
        out["excess_return"] = out["total_return"] - out["benchmark_return"]
    return out


def trade_stats(closed: list[dict]) -> dict:
    wins = [t for t in closed if t["pnl"] > 0]
    out = {"closed_trades": len(closed), "wins": len(wins), "losses": len(closed) - len(wins)}
    out["win_rate"] = len(wins) / len(closed) if closed else None
    if len(closed) < 30:
        out["note"] = f"only {len(closed)} closed trade(s): not a basis for any profitability claim"
    return out


def score_research(proposals: list[dict], merlin: dict, quant: dict, md: MarketData, benchmark: str,
                   horizon: int, through: date) -> list[dict]:
    """Stance hit/miss on the instrument's return vs benchmark over *horizon* sessions after the cutoff."""
    rows = []
    for p in proposals:
        cut = date.fromisoformat(p["data_cutoff"])
        sessions = trading_days(cut, through)
        if len(sessions) <= horizon:
            rows.append({"proposal_id": p["proposal_id"], "instrument": p["instrument"], "status": "PENDING",
                         "note": f"needs {horizon} sessions after {cut}"})
            continue
        end = sessions[horizon]
        a0, a1 = md.raw_close(p["instrument"], cut)[0], md.raw_close(p["instrument"], end)[0]
        b0, b1 = md.raw_close(benchmark, cut)[0], md.raw_close(benchmark, end)[0]
        # split-safe: use the split-adjusted view at the end date
        v = md.view(end)
        adj = {b["date"]: b["close"] for b in v.bars(p["instrument"])}
        a0, a1 = adj[cut.isoformat()], adj[end.isoformat()]
        rel = (a1 / a0 - 1) - (b1 / b0 - 1)

        def hit(stance):
            return {"BULLISH": rel > 0, "BEARISH": rel < 0}.get(stance)
        ms = merlin.get(p["merlin_report_id"], {}).get("stance")
        qs = quant.get(p["quant_report_id"], {}).get("stance")
        rows.append({"proposal_id": p["proposal_id"], "instrument": p["instrument"], "status": "SCORED",
                     "horizon_sessions": horizon, "relative_return": round(rel, 4), "merlin_stance": ms,
                     "merlin_hit": hit(ms), "quant_stance": qs, "quant_hit": hit(qs), "action": p["action"]})
    return rows
