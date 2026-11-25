"""Offline tests for the live public-data builder (no network: canned Yahoo/SEC payloads)."""

from datetime import date, datetime, timedelta

import pytest

from merlin_quant.calendar import NY, trading_days
from merlin_quant.cli import durability_warning, main
from merlin_quant.data import MarketData
from merlin_quant.live_data import FetchError, Http, build_filings, build_market_data, fetch_bars, quarterly_flow

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=NY)          # after the 2026-11-13 close


def _ts(d: date, hour=9, minute=30) -> int:
    return int(datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY).timestamp())


def yahoo_chart(start=date(2025, 1, 2), end=date(2026, 11, 13), split=(date(2026, 6, 10), 4, 1), price=100.0):
    days = trading_days(start, end) + [end + timedelta(days=4)]   # one session after NOW (must be dropped)
    o, h, lo, c, v = [], [], [], [], []
    for i, d in enumerate(days):
        p = price + i * 0.1
        o.append(p); h.append(p + 1); lo.append(p - 1); c.append(p + 0.5); v.append(1_000_000)
    c[3] = None                                                   # a missing bar is skipped, not invented
    events = {"dividends": {"x": {"date": _ts(date(2026, 2, 9)), "amount": 0.25}},
              "splits": {"y": {"date": _ts(split[0]), "numerator": split[1], "denominator": split[2]}}}
    return {"chart": {"result": [{"meta": {"longName": "Test Co", "fullExchangeName": "NasdaqGS", "currency": "USD",
                                           "instrumentType": "EQUITY"},
                                  "timestamp": [_ts(d) for d in days], "events": events,
                                  "indicators": {"quote": [dict(open=o, high=h, low=lo, close=c, volume=v)]}}],
                      "error": None}}


def fact(start, end, val, filed, form="10-Q", accn="a"):
    f = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        f["start"] = start
    return f


def companyfacts():
    """FY = calendar year. Revenue reported quarterly (Q4 only via the 10-K annual figure);
    operating cash flow only year-to-date; a later restatement of Q1-2025 revenue."""
    qs = [("2025-01-01", "2025-03-31", "2025-05-01"), ("2025-04-01", "2025-06-30", "2025-08-01"),
          ("2025-07-01", "2025-09-30", "2025-11-01"), ("2026-01-01", "2026-03-31", "2026-05-01"),
          ("2026-04-01", "2026-06-30", "2026-08-01"), ("2026-07-01", "2026-09-30", "2026-11-01")]
    rev, ni, oi, gp, sh = [], [], [], [], []
    for i, (s, e, filed) in enumerate(qs):
        rev.append(fact(s, e, (10 + i) * 1e9, filed))
        ni.append(fact(s, e, 2e9, filed)); oi.append(fact(s, e, 3e9, filed)); gp.append(fact(s, e, 5e9, filed))
        sh.append(fact(s, e, 1e9, filed))
    rev.append(fact("2025-01-01", "2025-12-31", 50e9, "2026-02-01", "10-K"))     # Q4-2025 = 50 - 10 - 11 - 12 = 17
    ni.append(fact("2025-01-01", "2025-12-31", 8e9, "2026-02-01", "10-K"))
    oi.append(fact("2025-01-01", "2025-12-31", 12e9, "2026-02-01", "10-K"))
    gp.append(fact("2025-01-01", "2025-12-31", 20e9, "2026-02-01", "10-K"))
    sh.append(fact("2025-01-01", "2025-12-31", 1e9, "2026-02-01", "10-K"))
    rev.append(fact("2025-01-01", "2025-03-31", 99e9, "2026-05-01", "10-Q", "z"))  # restatement: never used
    cfo = [fact("2025-01-01", "2025-03-31", 4e9, "2025-05-01"), fact("2025-01-01", "2025-06-30", 9e9, "2025-08-01"),
           fact("2025-01-01", "2025-09-30", 15e9, "2025-11-01"),
           fact("2025-01-01", "2025-12-31", 22e9, "2026-02-01", "10-K"),
           fact("2026-01-01", "2026-03-31", 5e9, "2026-05-01"), fact("2026-01-01", "2026-06-30", 11e9, "2026-08-01"),
           fact("2026-01-01", "2026-09-30", 18e9, "2026-11-01")]
    capex = [fact(f["start"], f["end"], f["val"] / 4, f["filed"], f["form"]) for f in cfo]
    ends = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
    cash = [fact(None, e, 30e9, "2026-11-01") for e in ends]
    debt = [fact(None, e, 10e9, "2026-11-01") for e in ends]
    g = lambda rows, unit="USD": {"units": {unit: rows}}  # noqa: E731
    return {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": g(rev), "NetIncomeLoss": g(ni),
        "OperatingIncomeLoss": g(oi), "GrossProfit": g(gp), "NetCashProvidedByUsedInOperatingActivities": g(cfo),
        "PaymentsToAcquirePropertyPlantAndEquipment": g(capex), "CashAndCashEquivalentsAtCarryingValue": g(cash),
        "LongTermDebt": g(debt), "WeightedAverageNumberOfDilutedSharesOutstanding": g(sh, "shares")}}}


SUBMISSIONS = {"name": "TEST CO", "sicDescription": "Electronic Computers",
               "filings": {"recent": {"form": ["8-K", "10-Q", "8-K"],
                                      "acceptanceDateTime": ["2026-10-30T16:31:00.000Z", "2026-11-01T12:00:00.000Z",
                                                             "2024-01-05T12:00:00.000Z"],
                                      "filingDate": ["2026-10-30", "2026-11-01", "2024-01-05"],
                                      "items": ["2.02,9.01", "", "5.02"]}}}
NEWS = {"news": [{"title": "Test Co beats", "publisher": "Wire", "providerPublishTime": _ts(date(2026, 11, 12), 8),
                  "relatedTickers": ["TST"]},
                 {"title": "Unrelated", "publisher": "Wire", "providerPublishTime": _ts(date(2026, 11, 12), 9),
                  "relatedTickers": ["OTHER"]}]}


class FakeHttp(Http):
    def __init__(self, fail_hosts=()):
        super().__init__("Test Runner test@example.com", pause=0)
        self.fail_hosts, self.calls = fail_hosts, []

    def get_json(self, url):
        self.calls.append(url)
        if any(h in url for h in self.fail_hosts):
            raise FetchError(f"blocked {url}")
        if "company_tickers" in url:
            return {"0": {"cik_str": 1234, "ticker": "TST", "title": "Test Co"}}
        if "/submissions/" in url:
            return SUBMISSIONS
        if "companyfacts" in url:
            return companyfacts()
        if "/v1/finance/search" in url:
            return NEWS
        if "/v8/finance/chart/" in url:
            return yahoo_chart(price=400.0 if "SPY" in url else 100.0)
        raise AssertionError(url)


def test_bars_are_unadjusted_back_to_raw_and_split_is_an_action():
    bars, actions, _ = fetch_bars(FakeHttp(), "TST", date(2025, 1, 2), date(2026, 11, 13), NOW)
    by = {b["date"]: b for b in bars}
    assert bars[-1]["date"] == "2026-11-13"                          # the post-NOW session is dropped
    assert "2025-01-07" not in by                                     # missing close: skipped, not invented
    before, after = by["2026-06-09"], by["2026-06-10"]
    assert before["close"] > 3.5 * after["close"]                     # raw prices jump at the split
    split = [a for a in actions if a["type"] == "split"][0]
    assert split == dict(type="split", ex_date="2026-06-10", ratio=4.0, source="yahoo")
    div = [a for a in actions if a["type"] == "dividend"][0]
    assert div["amount"] == pytest.approx(1.0)                        # 0.25 split-adjusted -> 1.00 raw


def test_point_in_time_view_readjusts_live_bars():
    md = build_market_data(["TST"], "SPY", date(2025, 1, 2), http=FakeHttp(), now=NOW, log=lambda m: None)
    v = md.view(date(2026, 11, 13))
    adj = {b["date"]: b["close"] for b in v.bars("TST")}
    assert adj["2026-06-09"] == pytest.approx(adj["2026-06-10"], rel=0.01)   # continuous after re-adjusting


def test_quarterly_flow_decumulates_ytd_and_derives_q4():
    f = companyfacts()
    rev = quarterly_flow(f, ["RevenueFromContractWithCustomerExcludingAssessedTax"])
    assert rev["2025-12-31"] == (17e9, "2026-02-01")                 # 10-K annual minus three quarters
    assert rev["2025-03-31"] == (10e9, "2025-05-01")                 # first reported; restatement ignored
    cfo = quarterly_flow(f, ["NetCashProvidedByUsedInOperatingActivities"])
    assert cfo["2025-06-30"] == (5e9, "2025-08-01") and cfo["2025-12-31"] == (7e9, "2026-02-01")
    assert cfo["2026-09-30"] == (7e9, "2026-11-01")


def test_build_filings_rows_match_marketdata_units():
    rows, notes = build_filings(companyfacts())
    assert [r["period_end"] for r in rows] == ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
                                               "2026-03-31", "2026-06-30", "2026-09-30"]
    q2 = rows[1]
    assert q2["revenue"] == 11.0 and q2["fcf"] == pytest.approx(5 * 0.75) and q2["debt"] == 10.0
    assert q2["diluted_shares"] == 1.0 and q2["units"] == "USD bn; shares bn"
    assert all(r["filed"] > r["period_end"] for r in rows)
    assert notes == [] and all(r["notes"] == [] for r in rows)
    old, _ = build_filings(companyfacts(), max_quarters=2)
    assert [r["period_end"] for r in old] == ["2026-06-30", "2026-09-30"]


def test_build_market_data_end_to_end_and_news_is_point_in_time(tmp_path):
    md = build_market_data(["TST"], "SPY", date(2025, 1, 2), http=FakeHttp(), now=NOW, log=lambda m: None)
    assert md.source_kind == "market_data" and "SYNTHETIC" not in md.source_name.upper()
    assert md.fetched_at == "2026-11-14T01:00:00+00:00"              # NOW (20:00 ET) in UTC
    heads = [n["headline"] for n in md.news["TST"]]
    assert "Test Co beats" in heads and "Unrelated" not in heads
    assert any(h.startswith("SEC 8-K filed: 2.02 results of operations") for h in heads)
    assert not any("5.02" in h for h in heads)                       # 8-K before --start is left out
    assert md.profiles["TST"]["sector"] == "Electronic Computers" and md.filings.get("SPY") is None
    v = md.view(date(2026, 10, 29))
    assert all(n["published"] < "2026-10-30" for n in v.news("TST", days=30))   # 8-K of 10-30 not yet visible
    p = tmp_path / "md.json"
    md.save(p)
    again = build_market_data(["TST"], "SPY", date(2025, 1, 2), http=FakeHttp(fail_hosts=("/v1/finance/search",)),
                              now=NOW, previous=MarketData.load(p), log=lambda m: None)
    assert "Test Co beats" in [n["headline"] for n in again.news["TST"]]   # headlines kept across fetches


def test_merlin_and_quant_inputs_build_from_live_shaped_data():
    from merlin_quant.merlin import build_inputs
    from merlin_quant.quant import indicators
    md = build_market_data(["TST"], "SPY", date(2025, 1, 2), http=FakeHttp(), now=NOW, log=lambda m: None)
    v = md.view(date(2026, 11, 13))
    inp = build_inputs(v, "TST", ["TST"], date(2026, 11, 13))
    assert inp["metrics"]["ttm_revenue"] > 0 and inp["data_source"].startswith("LIVE_PUBLIC")
    ind = indicators(v, "TST", "SPY")
    assert ind["close"] > 0 and "rsi14" in ind


def test_blocked_network_fails_clearly():
    with pytest.raises(FetchError, match="Yahoo unavailable"):
        fetch_bars(FakeHttp(fail_hosts=("yahoo",)), "TST", date(2025, 1, 2), date(2026, 11, 13), NOW)
    with pytest.raises(FetchError, match="User-Agent"):
        Http(None).get_json("https://data.sec.gov/submissions/CIK0000001234.json")


def test_fetch_data_cli_writes_a_loadable_file(tmp_path, monkeypatch):
    import merlin_quant.live_data as ld
    monkeypatch.setattr(ld, "Http", lambda ua: FakeHttp())
    real = ld.build_market_data
    monkeypatch.setattr(ld, "build_market_data", lambda *a, **k: real(*a, **dict(k, now=NOW)))
    out = tmp_path / "live.json"
    cfg = tmp_path / "cfg.json"
    import json
    from merlin_quant.config import EXAMPLE_CONFIG
    data = json.loads(EXAMPLE_CONFIG.read_text())
    data["portfolio"].update(universe=["TST"], sectors={"TST": "Hardware"})
    cfg.write_text(json.dumps(data))
    assert main(["--config", str(cfg), "fetch-data", "--out", str(out)]) == 0
    md = MarketData.load(out)
    assert set(md.bars) == {"TST", "SPY"} and md.profiles["TST"]["sector"] == "Hardware"
    assert md.fetched_at.startswith("2026-11-14T01:00")


def test_old_data_files_without_a_download_stamp_still_load(tmp_path):
    import json
    md = build_market_data(["TST"], "SPY", date(2025, 1, 2), http=FakeHttp(), now=NOW, log=lambda m: None)
    d = dict(md.__dict__); d.pop("fetched_at")
    (tmp_path / "old.json").write_text(json.dumps(d))
    old = MarketData.load(tmp_path / "old.json")
    assert old.fetched_at == "" and old.digest() == md.digest()


def test_durability_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    assert durability_warning("/tmp/mq") is not None
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert "cloud container" in durability_warning("/home/someone/merlin-quant-paper")


def test_owner_config_next_to_ledger_is_used(tmp_path):
    import json
    from merlin_quant.cli import _config_path
    from merlin_quant.config import EXAMPLE_CONFIG

    class A:
        config, home = None, str(tmp_path)
    assert _config_path(A) is None
    (tmp_path / "config.json").write_text(EXAMPLE_CONFIG.read_text())
    assert _config_path(A) == tmp_path / "config.json"
    A.config = "x.json"
    assert _config_path(A) == "x.json"


def test_prices_only_keeps_an_etf_with_no_sec_filings():
    from merlin_quant.guards import check_data_freshness
    from merlin_quant.config import load_config
    with pytest.raises(FetchError, match="--prices-only"):
        build_market_data(["ETFX"], "SPY", date(2025, 1, 2), http=FakeHttp(), now=NOW, log=lambda m: None)
    md = build_market_data(["ETFX"], "SPY", date(2025, 1, 2), http=FakeHttp(), now=NOW, log=lambda m: None,
                           prices_only=True)
    assert md.bars["ETFX"] and md.filings["ETFX"] == []
    r = check_data_freshness(md.view(date(2026, 11, 13)), "ETFX", load_config(None).guards, NOW)
    assert r.details["limited"] is True
