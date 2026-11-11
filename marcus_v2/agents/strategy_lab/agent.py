"""Strategy Lab agent (foundation: VectorBT).

Produces reproducible historical evidence. It never deploys strategies,
approves risk controls or claims future profitability; Quant reviews its
methodology independently (see integrations.existing.quant_adapter).
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from typing import Any

import pandas as pd
import vectorbt as vbt

from ...core.contracts import BacktestRequest, BacktestResult, MarketIntelligenceReport, PriceType, QualityStatus
from ...integrations import vectorbt_adapter as vba
from ..base import Agent

MIN_BARS = 120
MIN_TRADES = 3


class DataNotValidated(Exception):
    pass


def frame_from_report(report: MarketIntelligenceReport) -> pd.DataFrame:
    """Build an OHLC frame from Data-Guardian-accepted, final bars only."""
    if report.data_quality_status not in (QualityStatus.VALID, QualityStatus.WARNING):
        raise DataNotValidated(f"report {report.report_id} has data quality {report.data_quality_status}; "
                               "Strategy Lab only uses data Data Guardian accepted")
    rows = [o for o in report.observations
            if o.validation_status != QualityStatus.REJECTED and o.price_type != PriceType.PROVISIONAL_INTRADAY
            and None not in (o.open, o.high, o.low, o.close)]
    df = pd.DataFrame({"open": [float(o.open) for o in rows], "high": [float(o.high) for o in rows],
                       "low": [float(o.low) for o in rows], "close": [float(o.close) for o in rows]},
                      index=pd.DatetimeIndex([pd.Timestamp(o.bar_date) for o in rows], name="date"))
    return df[~df.index.duplicated()].sort_index()


class StrategyLab(Agent):
    agent_id = "strategy_lab"

    def register(self):
        self.tool("run_strategy_backtest", self.run_strategy_backtest, "run_backtest", timeout_s=300)
        self.tool("run_fee_sensitivity", self.run_fee_sensitivity, "run_backtest", timeout_s=300)
        self.tool("run_slippage_sensitivity", self.run_slippage_sensitivity, "run_backtest", timeout_s=300)
        self.tool("test_parameter_robustness", self.test_parameter_robustness, "run_backtest", timeout_s=600)
        self.tool("run_walk_forward_validation", self.run_walk_forward_validation, "run_backtest", timeout_s=300)
        return self

    def _kw(self, req: BacktestRequest, fee_mult: float = 1.0, slip_mult: float = 1.0) -> dict:
        return dict(init_cash=float(req.initial_cash), fees=float(req.fees_pct) * fee_mult,
                    fixed_fees=float(req.fixed_fee) * fee_mult, slippage=float(req.slippage_pct) * slip_mult)

    def run_fee_sensitivity(self, req: BacktestRequest, df: pd.DataFrame) -> dict:
        return {f"fees_x{m}": vba.run_backtest(df, req.entry_rule, req.exit_rule, **self._kw(req, fee_mult=m))["total_return_pct"]
                for m in (0, 1, 2, 4)}

    def run_slippage_sensitivity(self, req: BacktestRequest, df: pd.DataFrame) -> dict:
        return {f"slippage_x{m}": vba.run_backtest(df, req.entry_rule, req.exit_rule, **self._kw(req, slip_mult=m))["total_return_pct"]
                for m in (0, 1, 3, 5)}

    def test_parameter_robustness(self, req: BacktestRequest, df: pd.DataFrame) -> dict:
        grid = req.config.get("robustness_grid") or _default_grid(req.entry_rule)
        out = {}
        for label, rule in grid:
            try:
                out[label] = vba.run_backtest(df, rule, req.exit_rule, **self._kw(req))["total_return_pct"]
            except ValueError as exc:
                out[label] = f"error: {exc}"
        vals = [v for v in out.values() if isinstance(v, (int, float))]
        return {"variants": out, "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "spread": (max(vals) - min(vals)) if len(vals) > 1 else None}

    def run_walk_forward_validation(self, req: BacktestRequest, df: pd.DataFrame, split: float = 0.7) -> dict:
        cut = int(len(df) * split)
        ins, oos = df.iloc[:cut], df.iloc[max(cut - 210, 0):]   # OOS keeps warm-up bars for indicators
        a = vba.run_backtest(ins, req.entry_rule, req.exit_rule, **self._kw(req))
        b = vba.run_backtest(oos, req.entry_rule, req.exit_rule, **self._kw(req))
        b_trades_in_oos = [t for t in b["trade_log"] if t["entry"] >= str(df.index[cut].date())]
        return {"in_sample": {k: a[k] for k in ("start", "end", "trades", "total_return_pct", "max_drawdown_pct")},
                "out_of_sample": {"start": str(df.index[cut].date()), "end": b["end"], "trades": len(b_trades_in_oos),
                                  "total_return_pct_incl_warmup_window": b["total_return_pct"]},
                "note": "fixed rules, no parameter fitting between windows (walk-forward evaluation, not optimisation)"}

    def run_strategy_backtest(self, req: BacktestRequest, report: MarketIntelligenceReport) -> BacktestResult:
        df = frame_from_report(report)
        dv = vba.data_version(df)
        repro = {"engine": "vectorbt", "engine_version": vbt.__version__, "data_version": dv,
                 "data_source": report.source_provider, "mi_report": report.report_id,
                 "dq_report": report.data_quality_report_id,
                 "request": json.loads(req.model_dump_json(exclude={"entry_rule", "exit_rule"})),
                 "entry_rule": _ser(req.entry_rule), "exit_rule": _ser(req.exit_rule)}
        concerns, limitations = [], ["historical simulation; not a forecast of future returns",
                                     "free delayed Yahoo data via OpenBB; adjusted for splits only as the provider reports",
                                     "fills at next bar open with fixed slippage; no intraday liquidity model"]
        if req.data_version and req.data_version != dv:
            concerns.append(f"request data_version {req.data_version} != actual {dv}: inputs changed")
        if len(df) < MIN_BARS:
            return self._store(BacktestResult(request_id=req.request_id, status="INSUFFICIENT_DATA", engine="vectorbt",
                                              engine_version=vbt.__version__, observations=len(df), trades=0,
                                              concerns=[f"only {len(df)} validated bars; {MIN_BARS} required"],
                                              limitations=limitations, reproducibility=repro))
        leaks = vba.detect_lookahead(df, req.entry_rule, "entry") + vba.detect_lookahead(df, req.exit_rule, "exit")
        if leaks:
            return self._store(BacktestResult(request_id=req.request_id, status="LEAKAGE_DETECTED", engine="vectorbt",
                                              engine_version=vbt.__version__, observations=len(df), trades=0,
                                              concerns=leaks, limitations=limitations, reproducibility=repro))
        main = vba.run_backtest(df, req.entry_rule, req.exit_rule, **self._kw(req))
        bench = vba.run_buy_and_hold(df, **self._kw(req))
        sens = {"fees": self.run_fee_sensitivity(req, df), "slippage": self.run_slippage_sensitivity(req, df),
                "parameters": self.test_parameter_robustness(req, df)}
        wf = self.run_walk_forward_validation(req, df)
        status = "OK"
        if main["trades"] < MIN_TRADES:
            status = "INSUFFICIENT_DATA"
            concerns.append(f"only {main['trades']} trades; results are anecdotal, not statistical evidence")
        spread = sens["parameters"].get("spread")
        if spread is not None and main["total_return_pct"] is not None and spread > max(abs(main["total_return_pct"]), 5):
            concerns.append(f"results swing {spread:.1f} percentage points across nearby parameters: fragile / overfitting risk")
        if wf["out_of_sample"]["trades"] == 0:
            concerns.append("no trades in the out-of-sample window")
        repro["result_hash"] = hashlib.sha256(json.dumps(main, sort_keys=True).encode()).hexdigest()[:20]
        res = BacktestResult(request_id=req.request_id, status=status, engine="vectorbt", engine_version=vbt.__version__,
                             observations=len(df), trades=main["trades"], metrics=main, benchmark_metrics=bench,
                             out_of_sample=wf, sensitivity=sens, concerns=concerns, limitations=limitations,
                             reproducibility=repro)
        return self._store(res)

    def _store(self, res: BacktestResult) -> BacktestResult:
        self.rt.evidence.put(res, "backtest_result")
        return res


def _ser(rule: dict) -> dict:
    return {k: ("<callable>" if callable(v) else v) for k, v in rule.items()}


def _default_grid(rule: dict) -> list[tuple[str, dict]]:
    out = []
    for key in ("window", "fast", "slow", "level", "threshold"):
        if key in rule and isinstance(rule[key], (int, float, str, Decimal)):
            base = float(rule[key])
            for m in (0.8, 0.9, 1.1, 1.2):
                r = copy.deepcopy(rule)
                v = base * m
                r[key] = int(round(v)) if key in ("window", "fast", "slow") else round(v, 4)
                out.append((f"{key}={r[key]}", r))
    return out
