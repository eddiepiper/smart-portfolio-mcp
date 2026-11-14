from datetime import datetime, timezone

from merlin.contracts import MerlinReport
from merlin.validate import validate
from merlin_fixtures import make_report


def checks(v):
    return {i["check"] for i in v["issues"] if i["severity"] == "error"}


def test_good_report_passes(report):
    v = validate(report)
    assert v["status"] in ("PASS", "WARN"), v
    assert not checks(v)


def test_schema_rejects_missing_sections(report):
    report.pop("unknowns")
    assert validate(report)["status"] == "FAIL"


def test_boundaries_cannot_be_changed(report):
    report["boundaries"] = {"trade_instruction": True}
    assert validate(report)["status"] == "FAIL"


def test_phantom_evidence_and_status_inflation(report):
    report["figures"][0]["evidence_ids"] = ["E9"]
    assert "phantom_evidence" in checks(validate(report))
    r = make_report()
    r["figures"][0]["status"] = "VERIFIED"
    assert "status_inflation" in checks(validate(r))


def test_synthetic_cannot_be_verified():
    r = make_report()
    r["evidence"][0]["status"] = "VERIFIED"
    v = validate(r)
    assert {"synthetic_as_fact", "evidence_provenance"} <= checks(v)


def test_historical_as_live():
    r = make_report()
    r["evidence"].append({"evidence_id": "E2", "title": "close", "publisher": "Yahoo", "kind": "market_data",
                          "status": "PROVISIONAL", "retrieved_at": "2026-11-10T00:00:00Z"})
    r["figures"].append({"figure_id": "F2", "metric": "price", "value": 100.0, "unit": "USD", "evidence_ids": ["E2"],
                         "status": "PROVISIONAL", "as_live": True})
    assert "historical_as_live" in checks(validate(r))


def test_confidence_and_trade_language():
    r = make_report(executive_summary="We are 80% confident the stock is cheap. Buy now.")
    c = checks(validate(r))
    assert {"invented_confidence", "trade_instruction"} <= c
    r = make_report(executive_summary="Rating: Overweight.")
    assert "trade_instruction" in checks(validate(r))


def test_tampered_model_and_bad_sensitivity():
    r = make_report()
    r["model"]["outputs"]["value_per_share"] = 300.0
    assert "model_not_reproducible" in checks(validate(r))
    r = make_report()
    r["model"]["sensitivity"]["centre_equals_base"] = False
    assert "sensitivity_centre" in checks(validate(r))


def test_quality_inflation():
    r = make_report(quality_status="VERIFIED")
    assert "quality_inflation" in checks(validate(r))


def test_verified_evidence_needs_provenance_and_stale_warning():
    r = make_report()
    r["evidence"].append({"evidence_id": "E2", "title": "10-Q", "publisher": "SEC", "kind": "filing", "status": "VERIFIED",
                          "url": "https://www.sec.gov/x", "document_date": "2025-01-01",
                          "retrieved_at": datetime.now(timezone.utc).isoformat()})
    v = validate(r)
    assert any(i["check"] == "stale_source" for i in v["issues"])
    r["evidence"][1]["url"] = None
    assert "evidence_provenance" in checks(validate(r))


def test_future_document_rejected():
    r = make_report()
    r["evidence"].append({"evidence_id": "E2", "title": "x", "publisher": "y", "kind": "news", "status": "PROVISIONAL",
                          "document_date": "2027-01-01"})
    assert "future_document" in checks(validate(r))


def test_schema_export_is_json_schema():
    s = MerlinReport.model_json_schema()
    assert s["properties"]["contract"]["const"] == "merlin-research/v1"


def test_calc_misuse_warning():
    r = make_report()
    r["figures"][0]["derived_by"] = "merlin.calc.tieout (implied_gross_profit used as a sum: 1 + 2)"
    v = validate(r)
    assert any(i["check"] == "calc_misuse" for i in v["issues"])
