"""VectorBT (free edition, Apache-2.0 + Commons Clause) adapter for Strategy Lab.

Internal research use only — the Commons Clause forbids selling a product or
service whose value derives substantially from vectorbt.

Rules are a small declarative vocabulary evaluated on information available at
each bar's close. Orders execute at the NEXT bar's open (signals are shifted one
bar), which removes same-bar look-ahead by construction. `detect_lookahead`
additionally re-computes signals on truncated history to catch rules that peek
at future data.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

import numpy as np
import pandas as pd
import vectorbt as vbt

RuleFn = Callable[[pd.DataFrame], pd.Series]


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def entry_signal(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    c = df["close"]
    t = rule["type"]
    if t == "close_above_level":
        sig = (c > float(rule["level"])) & (c.shift(1) <= float(rule["level"]))
    elif t == "close_above_ma":
        ma = _sma(c, int(rule["window"]))
        sig = (c > ma) & (c.shift(1) <= ma.shift(1))
    elif t == "sma_cross":
        f, s = _sma(c, int(rule["fast"])), _sma(c, int(rule["slow"]))
        sig = (f > s) & (f.shift(1) <= s.shift(1))
    elif t == "breakout":
        prior_high = df["high"].rolling(int(rule["window"])).max().shift(1)
        sig = c > prior_high
    elif t == "rsi_below":
        r = _rsi(c, int(rule.get("window", 14)))
        sig = (r < float(rule["threshold"])) & (r.shift(1) >= float(rule["threshold"]))
    elif t == "pullback_in_uptrend":
        ma50, ma200 = _sma(c, 50), _sma(c, 200)
        ma20 = _sma(c, int(rule.get("window", 20)))
        sig = (c > ma50) & (ma50 > ma200) & (df["low"] <= ma20) & (c > ma20)
    elif t == "custom":
        sig = rule["fn"](df)                     # used by tests (e.g. a leaky rule fixture)
    else:
        raise ValueError(f"unknown entry rule {t}")
    return sig.fillna(False).astype(bool)


def exit_signal(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    c = df["close"]
    t = rule.get("type", "none")
    if t == "none":
        sig = pd.Series(False, index=df.index)
    elif t == "close_below_ma":
        ma = _sma(c, int(rule["window"]))
        sig = (c < ma) & (c.shift(1) >= ma.shift(1))
    elif t == "sma_cross_down":
        f, s = _sma(c, int(rule["fast"])), _sma(c, int(rule["slow"]))
        sig = (f < s) & (f.shift(1) >= s.shift(1))
    elif t == "rsi_above":
        r = _rsi(c, int(rule.get("window", 14)))
        sig = r > float(rule["threshold"])
    elif t == "custom":
        sig = rule["fn"](df)
    else:
        raise ValueError(f"unknown exit rule {t}")
    return sig.fillna(False).astype(bool)


def data_version(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df[["open", "high", "low", "close"]], index=True).values.tobytes()).hexdigest()[:20]


def run_backtest(df: pd.DataFrame, entry_rule: dict, exit_rule: dict, *, init_cash: float, fees: float,
                 fixed_fees: float, slippage: float) -> dict:
    """Simulate long-only trades: signal at close t, fill at open t+1 (with slippage and fees).
    Stops/targets are evaluated by vectorbt against the bar range."""
    entries = entry_signal(df, entry_rule).shift(1, fill_value=False)
    exits = exit_signal(df, exit_rule).shift(1, fill_value=False)
    kw = {}
    if exit_rule.get("stop_loss_pct"):
        kw["sl_stop"] = float(exit_rule["stop_loss_pct"]) / 100
    if exit_rule.get("take_profit_pct"):
        kw["tp_stop"] = float(exit_rule["take_profit_pct"]) / 100
    pf = vbt.Portfolio.from_signals(
        close=df["close"], entries=entries, exits=exits, price=df["open"],
        open=df["open"], high=df["high"], low=df["low"],
        init_cash=init_cash, fees=fees, fixed_fees=fixed_fees, slippage=slippage,
        size=np.inf, freq="1D", **kw)
    return _metrics(pf, df)


def run_buy_and_hold(df: pd.DataFrame, *, init_cash: float, fees: float, fixed_fees: float, slippage: float) -> dict:
    entries = pd.Series(False, index=df.index)
    entries.iloc[0] = True
    pf = vbt.Portfolio.from_signals(close=df["close"], entries=entries, price=df["open"], init_cash=init_cash,
                                    fees=fees, fixed_fees=fixed_fees, slippage=slippage, size=np.inf, freq="1D")
    return _metrics(pf, df)


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (x != x or x in (float("inf"), float("-inf"))) else round(x, 6)


def _metrics(pf, df) -> dict:
    trades = pf.trades.records_readable if pf.trades.count() else pd.DataFrame()
    rets = pf.returns()
    return {
        "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "bars": int(len(df)),
        "trades": int(pf.trades.count()),
        "total_return_pct": _f(pf.total_return() * 100),
        "final_value": _f(pf.final_value()),
        "max_drawdown_pct": _f(pf.max_drawdown() * 100),
        "annualized_volatility_pct": _f(rets.std() * np.sqrt(252) * 100),
        "sharpe": _f(pf.sharpe_ratio()),
        "win_rate_pct": _f(pf.trades.win_rate() * 100) if pf.trades.count() else None,
        "total_fees": _f(pf.orders.records_readable["Fees"].sum()) if pf.orders.count() else 0.0,
        "exposure_pct": _f((pf.position_mask().mean()) * 100),
        "trade_log": [] if trades.empty else [
            {"entry": str(r["Entry Timestamp"].date()), "exit": str(r["Exit Timestamp"].date()) if pd.notna(r["Exit Timestamp"]) else None,
             "entry_price": _f(r["Avg Entry Price"]), "exit_price": _f(r["Avg Exit Price"]), "pnl": _f(r["PnL"]),
             "status": str(r["Status"])} for _, r in trades.iterrows()],
    }


def detect_lookahead(df: pd.DataFrame, rule: dict, kind: str = "entry", checkpoints: int = 12, warmup: int = 220) -> list[str]:
    """Signals at bar t must not change when bars after t are added.
    Recompute on history truncated at several checkpoints and compare."""
    fn = entry_signal if kind == "entry" else exit_signal
    full = fn(df, rule)
    n = len(df)
    start = min(warmup, max(n // 3, 2))
    idxs = np.unique(np.linspace(start, n - 2, num=min(checkpoints, max(n - start - 1, 1))).astype(int))
    problems = []
    for t in idxs:
        part = fn(df.iloc[: t + 1], rule)
        if bool(part.iloc[-1]) != bool(full.iloc[t]):
            problems.append(f"{kind} signal at {df.index[t].date()} changes when later data is added (look-ahead)")
    return problems
