import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from merlin import calc  # noqa: E402

DCF_INPUTS = {
    "projection": {"base_revenue": 1000, "growth": [0.10, 0.10], "ebit_margin": 0.20, "tax_rate": 0.25,
                   "da_pct": 0.05, "capex_pct": 0.06, "nwc_pct_of_change": 0.10},
    "valuation": {"discount_rate": 0.10, "terminal_growth": 0.02, "net_debt": 100, "shares": 10},
}


def make_report(**over):
    out = calc.run_dcf(DCF_INPUTS)
    sens = calc.sensitivity(DCF_INPUTS, "discount_rate", [0.09, 0.10, 0.11], "terminal_growth", [0.015, 0.02, 0.025])
    r = {
        "environment": "test",
        "request": {"task_type": "financial_model", "subject": "SYNTH", "question": "Value SYNTH",
                    "as_of": "2026-11-14", "requested_by": "test", "instrument_id": "US:SYNTH"},
        "capabilities_used": [{"name": "merlin.calc.run_dcf", "kind": "merlin_calc", "genuine_execution": True}],
        "executive_summary": "Synthetic company valued at 183.09 per share on stated assumptions.",
        "key_findings": [{"rank": 1, "finding": "Value per share 183.09 (synthetic).", "significance": "high",
                          "figure_ids": ["F1"], "evidence_ids": ["E1"]}],
        "evidence": [{"evidence_id": "E1", "title": "Synthetic inputs", "publisher": "Merlin test", "kind": "synthetic",
                      "status": "ASSUMPTION"}],
        "figures": [{"figure_id": "F1", "metric": "value_per_share", "value": out["value_per_share"], "unit": "USD/share",
                     "evidence_ids": ["E1"], "status": "ASSUMPTION", "derived_by": "merlin.calc.run_dcf"}],
        "model": {"type": "dcf", "inputs": DCF_INPUTS, "outputs": out, "sensitivity": sens,
                  "assumptions": [{"name": "discount_rate", "value": 0.10, "rationale": "synthetic"}],
                  "limitations": ["synthetic"]},
        "scenarios": [{"name": "base", "narrative": "base", "value": out["value_per_share"], "unit": "USD/share"}],
        "risks": [{"risk": "Inputs are synthetic.", "evidence_ids": ["E1"]}],
        "unknowns": ["Everything real."],
        "implications": [{"audience": "quant", "text": "Check the terminal value share."}],
        "quality_status": "PROVISIONAL",
        "quality_notes": "Synthetic inputs; arithmetic verified.",
    }
    for k, v in over.items():
        r[k] = v
    return copy.deepcopy(r)

