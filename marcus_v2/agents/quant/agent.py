"""Quant agent (rebuilt in the Claude cloud): independent quantitative challenger.

Quant is deliberately separate from Strategy Lab. Strategy Lab produces evidence;
Quant judges whether that evidence supports a conclusion, using its own methods:

- trade-level significance (t-statistic and a seeded bootstrap CI of the mean
  trade return; a CI that includes zero means no demonstrated edge),
- sample size (Quant requires more trades than Strategy Lab's minimum),
- excess return over buy-and-hold, cost and parameter fragility,
  out-of-sample consistency, leakage flags, data-quality status,
- an independent recomputation of the benchmark from its OWN validated data
  pull, so a wrong input or computation in Strategy Lab is caught.

It never sees Marcus's recommendation, only the evidence. Deterministic: no LLM.
Verdicts: SUPPORTED, CHALLENGED, INSUFFICIENT_EVIDENCE.
"""
from __future__ import annotations

import math
import random
from datetime import date
from typing import Any, Optional

from ...core.contracts import QualityStatus
from ..base import Agent

MIN_TRADES_FOR_SIGNIFICANCE = 10
BOOTSTRAP_SAMPLES = 5000
SEED = 20261110


def _trade_returns(trade_log: list[dict]) -> list[float]:
    out = []
    for t in trade_log or []:
        e, x = t.get("entry_price"), t.get("exit_price")
        if e and x and str(t.get("status", "")).lower() == "closed":
            out.append(x / e - 1)
    return out


def bootstrap_mean_ci(values: list[float], samples: int = BOOTSTRAP_SAMPLES, seed: int = SEED,
                      alpha: float = 0.05) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(samples))
    return means[int(alpha / 2 * samples)], means[int((1 - alpha / 2) * samples) - 1]


def t_stat(values: list[float]) -> Optional[float]:
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))
    return None if sd == 0 else m / (sd / math.sqrt(n))


class Quant(Agent):
    agent_id = "quant"

    def __init__(self, rt, mi=None, dg=None):
        super().__init__(rt)
        self.mi, self.dg = mi, dg

    def register(self):
        self.tool("review", self.review, "read_research", timeout_s=180)
        return self

    # QuantTransport protocol: review(request: dict) -> dict
    def review(self, request: dict) -> dict:
        ev: dict[str, Any] = request.get("evidence", {}) or {}
        findings: list[str] = []
        challenged, insufficient = False, False
        dq = ev.get("data_quality_status")
        if dq and dq not in ("VALID", "WARNING"):
            insufficient = True
            findings.append(f"market data quality is {dq}: no quantitative conclusion possible")
        bt = ev.get("backtest")
        if not bt:
            insufficient = True
            findings.append("no backtest or other quantitative evidence supplied")
        else:
            st = bt.get("status")
            if st == "LEAKAGE_DETECTED":
                challenged = True
                findings.append("backtest flagged look-ahead leakage: results are invalid")
            elif st != "OK":
                insufficient = True
                findings.append(f"backtest status {st}: {'; '.join(bt.get('concerns', []))[:200]}")
            m, b = bt.get("metrics", {}) or {}, bt.get("benchmark_metrics", {}) or {}
            rets = _trade_returns(m.get("trade_log", []))
            n = len(rets)
            if n < MIN_TRADES_FOR_SIGNIFICANCE:
                insufficient = True
                findings.append(f"{n} closed trades; at least {MIN_TRADES_FOR_SIGNIFICANCE} needed before any edge can be judged")
            if n >= 2:
                lo, hi = bootstrap_mean_ci(rets)
                ts = t_stat(rets)
                findings.append(f"mean trade return {100 * sum(rets) / n:.2f}% (95% bootstrap CI {100 * lo:.2f}% to {100 * hi:.2f}%, t={ts if ts is None else round(ts, 2)})")
                if n >= MIN_TRADES_FOR_SIGNIFICANCE and lo <= 0 <= hi:
                    challenged = True
                    findings.append("confidence interval includes zero: no statistically demonstrated edge")
            tr, br = m.get("total_return_pct"), b.get("total_return_pct")
            if tr is not None and br is not None and tr < br:
                challenged = True
                findings.append(f"strategy {tr:.2f}% underperformed buy-and-hold {br:.2f}% on the same data")
            fees = (bt.get("sensitivity", {}) or {}).get("fees", {})
            if tr is not None and tr > 0 and fees.get("fees_x2") is not None and fees["fees_x2"] <= 0:
                challenged = True
                findings.append("profit disappears at 2x costs: not robust to fees")
            params = (bt.get("sensitivity", {}) or {}).get("parameters", {})
            if params.get("min") is not None and tr is not None and tr > 0 and params["min"] < 0:
                challenged = True
                findings.append(f"nearby parameters lose money (worst {params['min']:.2f}%): fragile")
            oos = (bt.get("out_of_sample", {}) or {}).get("out_of_sample", {})
            if oos and oos.get("trades", 0) == 0:
                insufficient = True
                findings.append("no out-of-sample trades: in-sample result is unconfirmed")
            # independent recomputation of the benchmark from Quant's own validated data pull
            check = self._recompute_benchmark(ev.get("instrument_id"), b)
            if check:
                findings.append(check["finding"])
                challenged = challenged or check["mismatch"]
        verdict = "CHALLENGED" if challenged else "INSUFFICIENT_EVIDENCE" if insufficient else "SUPPORTED"
        out = {"verdict": verdict, "findings": findings, "reviewer": "quant (cloud, deterministic)",
               "method": {"min_trades": MIN_TRADES_FOR_SIGNIFICANCE, "bootstrap_samples": BOOTSTRAP_SAMPLES, "seed": SEED}}
        self.rt.evidence.put({"request_id": request.get("request_id"), **out}, "quant_review_raw",
                             obj_id=f"qraw_{request.get('request_id', 'na')}")
        return out

    def _recompute_benchmark(self, instrument_id: Optional[str], bench: dict) -> Optional[dict]:
        if not (self.mi and self.dg and instrument_id and bench.get("start") and bench.get("end")
                and bench.get("total_return_pct") is not None):
            return None
        start, end = date.fromisoformat(bench["start"]), date.fromisoformat(bench["end"])
        rep = self.mi.get_historical_prices(instrument_id, start, end, requesting_agent="quant")
        self.dg.validate_report(rep, purpose="research")
        if rep.data_quality_status not in (QualityStatus.VALID, QualityStatus.WARNING):
            return {"finding": f"could not independently verify: own data pull {rep.data_quality_status}", "mismatch": False}
        bars = [o for o in rep.observations if o.validation_status != QualityStatus.REJECTED and o.open and o.close
                and o.price_type.value != "provisional_intraday"]
        if len(bars) < 2:
            return {"finding": "could not independently verify: too few bars", "mismatch": False}
        raw = (float(bars[-1].close) / float(bars[0].open) - 1) * 100       # gross buy-and-hold, open to close
        diff = abs(raw - bench["total_return_pct"])
        # Strategy Lab's benchmark is net of fees/slippage, so allow a cost band before calling it a mismatch
        if diff > 1.0:
            return {"finding": f"independent buy-and-hold {raw:.2f}% vs Strategy Lab benchmark {bench['total_return_pct']:.2f}%: inputs or computation differ", "mismatch": True}
        return {"finding": f"independent buy-and-hold check {raw:.2f}% agrees with Strategy Lab benchmark {bench['total_return_pct']:.2f}% (within cost band)", "mismatch": False}
