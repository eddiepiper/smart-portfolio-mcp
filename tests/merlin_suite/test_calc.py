"""Hand-checked expectations for merlin.calc (each number below was worked out on paper)."""
import pytest

from merlin import calc
from merlin_fixtures import DCF_INPUTS


def test_fcff_build_matches_hand_calculation():
    rows = calc.project(1000, [0.10, 0.10], 0.20, 0.25, 0.05, 0.06, 0.10)
    # yr1: rev 1100, EBIT 220, NOPAT 165, D&A 55, capex 66, dNWC 10 -> FCFF 144
    assert rows[0]["fcff"] == pytest.approx(144.0)
    # yr2: rev 1210, NOPAT 181.5, D&A 60.5, capex 72.6, dNWC 11 -> 158.4
    assert rows[1]["fcff"] == pytest.approx(158.4)


def test_dcf_gordon():
    d = calc.run_dcf(DCF_INPUTS)
    assert d["terminal_value"] == pytest.approx(158.4 * 1.02 / 0.08)          # 2019.6
    assert d["pv_terminal_value"] == pytest.approx(2019.6 / 1.1 ** 2, rel=1e-6)
    assert d["sum_pv_fcff"] == pytest.approx(144 / 1.1 + 158.4 / 1.21, rel=1e-6)
    assert d["enterprise_value"] == pytest.approx(1930.9091, abs=1e-3)
    assert d["value_per_share"] == pytest.approx(183.0909, abs=1e-3)
    assert d["implied_exit_multiple"] == pytest.approx(2019.6 / 302.5, abs=1e-4)
    assert any("terminal value is" in w for w in d["warnings"])               # 86% of EV


def test_dcf_mid_year_and_exit_multiple():
    rows = calc.project(1000, [0.10, 0.10], 0.20, 0.25, 0.05, 0.06, 0.10)
    m = calc.dcf(rows, 0.10, terminal_growth=0.02, mid_year=True)
    assert m["rows"][0]["discount_factor"] == pytest.approx(1 / 1.1 ** 0.5, rel=1e-7)
    e = calc.dcf(rows, 0.10, exit_multiple=8.0)
    assert e["terminal_value"] == pytest.approx(302.5 * 8)
    g = e["implied_terminal_growth"]
    assert 158.4 * (1 + g) / (0.10 - g) == pytest.approx(2420, rel=1e-4)       # round-trips


def test_dcf_rejects_bad_terminal():
    rows = calc.project(1000, [0.1], 0.2, 0.25, 0.05, 0.06, 0.1)
    with pytest.raises(calc.CalcError):
        calc.dcf(rows, 0.05, terminal_growth=0.05)
    with pytest.raises(calc.CalcError):
        calc.dcf(rows, 0.10, terminal_growth=0.02, exit_multiple=8)


def test_sensitivity_centre_is_base_and_invalid_cells_are_none():
    s = calc.sensitivity(DCF_INPUTS, "discount_rate", [0.02, 0.10, 0.11], "terminal_growth", [0.015, 0.02, 0.025])
    assert s["centre_equals_base"] is True
    assert s["grid"][0][2] is None                       # g 2.5% >= r 2%
    with pytest.raises(calc.CalcError):
        calc.sensitivity(DCF_INPUTS, "discount_rate", [0.09, 0.10], "terminal_growth", [0.02])


def test_scenarios_and_reverse_dcf():
    sc = calc.scenarios(DCF_INPUTS, {"bull": {"projection": {"ebit_margin": 0.25}}, "base": {},
                                     "bear": {"projection": {"ebit_margin": 0.15}}})
    assert sc["bear"]["value_per_share"] < sc["base"]["value_per_share"] < sc["bull"]["value_per_share"]
    assert sc["base"]["value_per_share"] == pytest.approx(183.0909, abs=1e-3)
    g = calc.reverse_dcf_growth(DCF_INPUTS, 183.0909)
    assert g == pytest.approx(0.10, abs=1e-4)


def test_wacc_capm_ratios_comps():
    assert calc.capm(0.04, 1.2, 0.05) == pytest.approx(0.10)
    w = calc.wacc(0.10, 0.05, 0.25, 800, 200)
    assert w["wacc"] == pytest.approx(0.8 * 0.10 + 0.2 * 0.05 * 0.75)          # 0.0875
    r = calc.ratios({"revenue": 100, "gross_profit": 60, "net_income": 20, "cfo": 30, "capex": 10,
                     "total_debt": 50, "cash": 70, "price": 10, "shares_diluted": 10, "ebitda": 40})
    assert r["gross_margin"] == 0.6 and r["fcf"] == 20 and r["net_debt"] == -20
    assert r["enterprise_value"] == 80 and r["ev_ebitda"] == 2.0 and r["pe"] == 5.0
    c = calc.comps_stats({"A": 10, "B": 20, "C": 30, "D": None, "E": -5})
    assert c["n"] == 3 and c["median"] == 20 and c["p25"] == 15 and c["excluded"] == ["D", "E"]
    assert calc.cagr(100, 121, 2) == pytest.approx(0.10)


def test_tie_out_detects_transcription_error():
    ok = calc.tie_out(96221, 8408, 63734, 0.750)
    assert ok["ok"] and ok["implied_gross_profit"] == 72142
    bad = calc.tie_out(96221, 8408, 36734, 0.750)                               # digits swapped
    assert not bad["ok"]


def test_reproduce_detects_tampering():
    out = calc.run_dcf(DCF_INPUTS)
    assert calc.reproduce({"inputs": DCF_INPUTS, "outputs": out})["ok"] is True
    out2 = {**out, "value_per_share": 250.0}
    assert calc.reproduce({"inputs": DCF_INPUTS, "outputs": out2})["ok"] is False
