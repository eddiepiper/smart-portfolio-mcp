"""Merlin's deterministic financial calculator.

Every number Merlin reports that is *derived* (a margin, a growth rate, a DCF value) is
computed here, never in the model's head. Functions are pure, take plain numbers and
return plain dicts, so a run can be reproduced from its inputs alone (see `reproduce`).

Conventions (docs/merlin/modelling_conventions.md):
- rates are decimals (0.085 = 8.5%); money in one currency and one unit per model;
- end-of-year discounting unless mid_year=True;
- terminal value by Gordon growth (default) or exit multiple on terminal-year EBITDA;
- FCFF = EBIT*(1-tax) + D&A - capex - change in NWC;
- equity value = EV - net debt (net debt = debt - cash; negative = net cash).
"""
from __future__ import annotations

import math
from statistics import mean, median
from typing import Iterable, Optional, Sequence

CALC_VERSION = "merlin-calc/1.0"


class CalcError(ValueError):
    pass


def _r(x: float, nd: int = 6) -> float:
    return round(float(x), nd)


# ------------------------------------------------------------------ basic ratios
def pct_change(new: float, old: float) -> Optional[float]:
    if old in (0, None) or new is None:
        return None
    return _r((new - old) / abs(old))


def margin(numerator: float, revenue: float) -> Optional[float]:
    if not revenue:
        return None
    return _r(numerator / revenue)


def cagr(start: float, end: float, years: float) -> Optional[float]:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return _r((end / start) ** (1 / years) - 1)


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or not b:
        return None
    return _r(a / b)


def ratios(f: dict) -> dict:
    """Standard ratio set from a flat dict of statement items (any missing input -> None).

    Keys used: revenue, gross_profit, operating_income, net_income, ebitda, interest_expense,
    total_assets, total_equity, total_debt, cash, current_assets, current_liabilities,
    inventory, cfo, capex, shares_diluted, price.
    """
    g = f.get
    fcf = (g("cfo") - g("capex")) if g("cfo") is not None and g("capex") is not None else None
    net_debt = (g("total_debt") - g("cash")) if g("total_debt") is not None and g("cash") is not None else None
    mcap = g("price") * g("shares_diluted") if g("price") is not None and g("shares_diluted") else None
    ev = mcap + net_debt if mcap is not None and net_debt is not None else None
    return {
        "gross_margin": margin(g("gross_profit"), g("revenue")) if g("gross_profit") is not None else None,
        "operating_margin": margin(g("operating_income"), g("revenue")) if g("operating_income") is not None else None,
        "net_margin": margin(g("net_income"), g("revenue")) if g("net_income") is not None else None,
        "fcf": fcf,
        "fcf_margin": margin(fcf, g("revenue")) if fcf is not None else None,
        "fcf_conversion": safe_div(fcf, g("net_income")),
        "roe": safe_div(g("net_income"), g("total_equity")),
        "roa": safe_div(g("net_income"), g("total_assets")),
        "current_ratio": safe_div(g("current_assets"), g("current_liabilities")),
        "quick_ratio": safe_div((g("current_assets") - (g("inventory") or 0)) if g("current_assets") is not None else None,
                                g("current_liabilities")),
        "debt_to_equity": safe_div(g("total_debt"), g("total_equity")),
        "net_debt": net_debt,
        "net_debt_to_ebitda": safe_div(net_debt, g("ebitda")),
        "interest_cover": safe_div(g("operating_income"), g("interest_expense")),
        "market_cap": mcap,
        "enterprise_value": ev,
        "pe": safe_div(mcap, g("net_income")),
        "ev_ebitda": safe_div(ev, g("ebitda")),
        "ev_sales": safe_div(ev, g("revenue")),
        "fcf_yield": safe_div(fcf, mcap),
    }


def period_compare(cur: dict, prior: dict, keys: Iterable[str]) -> dict:
    """Change of each key between two periods (e.g. quarter vs year-ago quarter)."""
    return {k: {"current": cur.get(k), "prior": prior.get(k), "change": pct_change(cur.get(k), prior.get(k))}
            for k in keys}


def tie_out(revenue: float, opex: float, operating_income: float, reported_gross_margin: float,
            tol: float = 0.0006) -> dict:
    """Internal consistency check of a print: (operating income + opex) / revenue should equal the
    reported gross margin to within rounding. Catches transcription errors in fetched figures."""
    implied_gp = operating_income + opex
    implied_gm = implied_gp / revenue
    return {"implied_gross_profit": _r(implied_gp, 2), "implied_gross_margin": _r(implied_gm),
            "reported_gross_margin": reported_gross_margin,
            "difference": _r(implied_gm - reported_gross_margin), "ok": abs(implied_gm - reported_gross_margin) <= tol}


def arith(op: str, values: Sequence[float]) -> float:
    """Plain arithmetic with a named operation, so simple sums and differences are not forced
    through unrelated functions (found in testing: a model used `tieout` to add two numbers).
    sum: a+b+...; diff: a-b-...; product: a*b*...; ratio: a/b; share: a/b (a part of b)."""
    if not values:
        raise CalcError("no values")
    v = [float(x) for x in values]
    if op == "sum":
        return _r(sum(v))
    if op == "diff":
        return _r(v[0] - sum(v[1:]))
    if op == "product":
        out = 1.0
        for x in v:
            out *= x
        return _r(out)
    if op in ("ratio", "share"):
        if len(v) != 2 or v[1] == 0:
            raise CalcError(f"{op} needs exactly two values and a non-zero divisor")
        return _r(v[0] / v[1])
    raise CalcError(f"unknown op {op}; use sum, diff, product, ratio or share")


# ---------------------------------------------------------------- cost of capital
def capm(risk_free: float, beta: float, equity_risk_premium: float) -> float:
    return _r(risk_free + beta * equity_risk_premium)


def wacc(cost_of_equity: float, pre_tax_cost_of_debt: float, tax_rate: float,
         equity_value: float, debt_value: float) -> dict:
    if equity_value < 0 or debt_value < 0 or equity_value + debt_value == 0:
        raise CalcError("capital weights need non-negative equity and debt values")
    v = equity_value + debt_value
    we, wd = equity_value / v, debt_value / v
    w = we * cost_of_equity + wd * pre_tax_cost_of_debt * (1 - tax_rate)
    return {"wacc": _r(w), "weight_equity": _r(we), "weight_debt": _r(wd),
            "after_tax_cost_of_debt": _r(pre_tax_cost_of_debt * (1 - tax_rate))}


# --------------------------------------------------------------------------- DCF
def project(base_revenue: float, growth: Sequence[float], ebit_margin: Sequence[float] | float,
            tax_rate: float, da_pct: Sequence[float] | float, capex_pct: Sequence[float] | float,
            nwc_pct_of_change: Sequence[float] | float) -> list[dict]:
    """Year-by-year FCFF build. Percent drivers may be scalars or one value per year.
    nwc_pct_of_change: change in net working capital as a share of the revenue change."""
    n = len(growth)

    def at(x, i):
        return x[i] if isinstance(x, (list, tuple)) else x

    for name, x in (("ebit_margin", ebit_margin), ("da_pct", da_pct), ("capex_pct", capex_pct),
                    ("nwc_pct_of_change", nwc_pct_of_change)):
        if isinstance(x, (list, tuple)) and len(x) != n:
            raise CalcError(f"{name} has {len(x)} values for {n} projection years")
    rows, prev = [], base_revenue
    for i in range(n):
        rev = prev * (1 + growth[i])
        ebit = rev * at(ebit_margin, i)
        nopat = ebit * (1 - tax_rate)
        da = rev * at(da_pct, i)
        capex = rev * at(capex_pct, i)
        dnwc = (rev - prev) * at(nwc_pct_of_change, i)
        fcff = nopat + da - capex - dnwc
        rows.append({"year": i + 1, "revenue": _r(rev, 4), "growth": growth[i], "ebit": _r(ebit, 4),
                     "nopat": _r(nopat, 4), "da": _r(da, 4), "capex": _r(capex, 4),
                     "change_nwc": _r(dnwc, 4), "fcff": _r(fcff, 4), "ebitda": _r(ebit + da, 4)})
        prev = rev
    return rows


def dcf(rows: list[dict], discount_rate: float, *, terminal_growth: Optional[float] = None,
        exit_multiple: Optional[float] = None, net_debt: float = 0.0, shares: Optional[float] = None,
        mid_year: bool = False) -> dict:
    """Discount projected FCFF and a terminal value to an enterprise and equity value."""
    if (terminal_growth is None) == (exit_multiple is None):
        raise CalcError("give exactly one of terminal_growth or exit_multiple")
    if terminal_growth is not None and terminal_growth >= discount_rate:
        raise CalcError(f"terminal growth {terminal_growth} must be below the discount rate {discount_rate}")
    if discount_rate <= -1:
        raise CalcError("discount rate must be > -100%")
    pv_fcff, out_rows = 0.0, []
    for r in rows:
        t = r["year"] - (0.5 if mid_year else 0.0)
        df = 1 / (1 + discount_rate) ** t
        pv = r["fcff"] * df
        pv_fcff += pv
        out_rows.append({**r, "discount_factor": _r(df, 8), "pv_fcff": _r(pv, 4)})
    last = rows[-1]
    if terminal_growth is not None:
        tv = last["fcff"] * (1 + terminal_growth) / (discount_rate - terminal_growth)
        method = "gordon_growth"
    else:
        tv = last["ebitda"] * exit_multiple
        method = "exit_multiple"
    # terminal value is discounted from the end of the final year in both conventions
    tv_df = 1 / (1 + discount_rate) ** last["year"]
    pv_tv = tv * tv_df
    ev = pv_fcff + pv_tv
    eq = ev - net_debt
    out = {"calc_version": CALC_VERSION, "method": method, "discount_rate": discount_rate,
           "terminal_growth": terminal_growth, "exit_multiple": exit_multiple, "mid_year": mid_year,
           "rows": out_rows, "sum_pv_fcff": _r(pv_fcff, 4), "terminal_value": _r(tv, 4),
           "pv_terminal_value": _r(pv_tv, 4), "enterprise_value": _r(ev, 4),
           "tv_share_of_ev": _r(pv_tv / ev) if ev else None, "net_debt": net_debt,
           "equity_value": _r(eq, 4), "shares": shares,
           "value_per_share": _r(eq / shares, 4) if shares else None}
    if terminal_growth is not None and last["ebitda"]:
        out["implied_exit_multiple"] = _r(tv / last["ebitda"], 4)
    if exit_multiple is not None and tv:
        # growth implied by the exit multiple: tv = fcff*(1+g)/(r-g)  ->  g = (tv*r - fcff)/(tv + fcff)
        out["implied_terminal_growth"] = _r((tv * discount_rate - last["fcff"]) / (tv + last["fcff"]))
    warnings = []
    if out["tv_share_of_ev"] is not None and out["tv_share_of_ev"] > 0.75:
        warnings.append(f"terminal value is {out['tv_share_of_ev']:.0%} of EV: valuation rests mainly on the terminal assumption")
    if terminal_growth is not None and terminal_growth > 0.04:
        warnings.append("terminal growth above 4% exceeds long-run nominal GDP for most economies")
    if any(r["fcff"] < 0 for r in rows):
        warnings.append("negative FCFF in at least one projection year")
    out["warnings"] = warnings
    return out


def run_dcf(inputs: dict) -> dict:
    """One call from a JSON-able input block (the form stored in a report's model section)."""
    p = inputs["projection"]
    rows = project(p["base_revenue"], p["growth"], p["ebit_margin"], p["tax_rate"], p["da_pct"],
                   p["capex_pct"], p["nwc_pct_of_change"])
    v = inputs["valuation"]
    return dcf(rows, v["discount_rate"], terminal_growth=v.get("terminal_growth"),
               exit_multiple=v.get("exit_multiple"), net_debt=v.get("net_debt", 0.0),
               shares=v.get("shares"), mid_year=v.get("mid_year", False))


def sensitivity(inputs: dict, row_key: str, row_values: Sequence[float], col_key: str,
                col_values: Sequence[float], output: str = "value_per_share") -> dict:
    """Two-way table over valuation keys (discount_rate, terminal_growth, exit_multiple) or a
    scalar projection key (tax_rate, ebit_margin, ...). Cells that are invalid (g >= r) are None."""
    if len(row_values) % 2 == 0 or len(col_values) % 2 == 0:
        raise CalcError("use an odd number of rows and columns so the centre cell is the base case")

    def with_(base, key, val):
        new = {"projection": dict(base["projection"]), "valuation": dict(base["valuation"])}
        (new["valuation"] if key in new["valuation"] or key in ("discount_rate", "terminal_growth", "exit_multiple")
         else new["projection"])[key] = val
        return new

    grid = []
    for rv in row_values:
        line = []
        for cv in col_values:
            try:
                line.append(run_dcf(with_(with_(inputs, row_key, rv), col_key, cv))[output])
            except CalcError:
                line.append(None)
        grid.append(line)
    base = run_dcf(inputs)[output]
    centre = grid[len(row_values) // 2][len(col_values) // 2]
    return {"row_key": row_key, "rows": list(row_values), "col_key": col_key, "cols": list(col_values),
            "output": output, "grid": grid, "base": base, "centre_equals_base": centre == base}


def scenarios(inputs: dict, overrides: dict[str, dict]) -> dict:
    """Bull/base/bear (or any named) cases: each override replaces projection/valuation keys."""
    out = {}
    for name, ov in overrides.items():
        case = {"projection": {**inputs["projection"], **ov.get("projection", {})},
                "valuation": {**inputs["valuation"], **ov.get("valuation", {})}}
        r = run_dcf(case)
        out[name] = {"enterprise_value": r["enterprise_value"], "equity_value": r["equity_value"],
                     "value_per_share": r["value_per_share"], "tv_share_of_ev": r["tv_share_of_ev"],
                     "overrides": ov}
    return out


def reverse_dcf_growth(inputs: dict, target_value_per_share: float, lo: float = -0.5, hi: float = 2.0,
                       tol: float = 1e-7) -> Optional[float]:
    """Constant annual revenue growth over the projection that makes value/share equal the target
    (what the price 'implies'). Bisection; None when the target is unreachable in [lo, hi]."""
    n = len(inputs["projection"]["growth"])

    def f(g):
        case = {"projection": {**inputs["projection"], "growth": [g] * n}, "valuation": inputs["valuation"]}
        return run_dcf(case)["value_per_share"] - target_value_per_share

    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < tol:
            break
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return _r((lo + hi) / 2)


# ------------------------------------------------------------------------- comps
def comps_stats(values: dict[str, Optional[float]]) -> dict:
    """Summary of a multiple across peers. None / non-positive values are excluded and listed."""
    good = {k: v for k, v in values.items() if v is not None and v > 0 and math.isfinite(v)}
    excluded = sorted(set(values) - set(good))
    xs = sorted(good.values())
    if not xs:
        return {"n": 0, "excluded": excluded}

    def q(p):
        if len(xs) == 1:
            return xs[0]
        k = (len(xs) - 1) * p
        f, c = math.floor(k), math.ceil(k)
        return xs[f] + (xs[c] - xs[f]) * (k - f)

    return {"n": len(xs), "min": _r(xs[0], 4), "p25": _r(q(0.25), 4), "median": _r(median(xs), 4),
            "mean": _r(mean(xs), 4), "p75": _r(q(0.75), 4), "max": _r(xs[-1], 4), "excluded": excluded}


def implied_value(metric_value: float, multiple: float, net_debt: float = 0.0,
                  shares: Optional[float] = None) -> dict:
    ev = metric_value * multiple
    eq = ev - net_debt
    return {"enterprise_value": _r(ev, 4), "equity_value": _r(eq, 4),
            "value_per_share": _r(eq / shares, 4) if shares else None}


# ------------------------------------------------------------------ reproduction
def reproduce(model: dict, tol: float = 1e-4) -> dict:
    """Re-run a report's stored DCF inputs and compare with its stored outputs."""
    if not model or "inputs" not in model or "outputs" not in model:
        return {"ok": None, "reason": "no reproducible model block"}
    fresh = run_dcf(model["inputs"])
    diffs = {}
    for k in ("enterprise_value", "equity_value", "value_per_share", "pv_terminal_value", "sum_pv_fcff"):
        a, b = model["outputs"].get(k), fresh.get(k)
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > tol * max(1.0, abs(b)):
            diffs[k] = {"stored": a, "recomputed": b}
    return {"ok": not diffs, "differences": diffs, "recomputed": {k: fresh[k] for k in
            ("enterprise_value", "equity_value", "value_per_share")}}
