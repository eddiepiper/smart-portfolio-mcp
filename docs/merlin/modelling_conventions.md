# Merlin modelling conventions

Applies to every model Merlin builds, in either environment. The calculator
(`merlin/calc.py`, version `merlin-calc/1.0`) implements these rules; a report's stored model
must reproduce under `python -m merlin reproduce`.

## Units and signs
- Rates are decimals (`0.085` = 8.5%). Money is in one currency and one unit per model
  (state it: "USD m"). Per-share values use diluted shares from the latest filing.
- Net debt = total debt − cash and marketable securities. Negative means net cash.
- Reported figures and derived figures are kept apart: a derived figure records `derived_by`.

## Free cash flow to the firm
`FCFF = EBIT × (1 − tax) + D&A − capex − ΔNWC`, where ΔNWC = (revenue_t − revenue_{t−1}) ×
`nwc_pct_of_change`. Drivers may be constant or one value per year. Use the effective cash tax
rate, not the statutory rate, when the filing supports it, and say which.

## Discounting
- End-of-year discounting by default; `mid_year=True` discounts year t at t − 0.5. The terminal
  value is always discounted from the end of the final year.
- WACC from CAPM: `ke = rf + β × ERP`; `WACC = we × ke + wd × kd × (1 − t)` on market weights.
  Record rf (source and date), β (source and window), ERP (source) as evidence or ASSUMPTION.

## Terminal value
- Default Gordon growth: `TV = FCFF_N × (1 + g) / (WACC − g)`, `g < WACC`, and g should not exceed
  long-run nominal GDP (the calculator warns above 4%).
- Cross-check: the calculator reports the implied exit EV/EBITDA for a Gordon TV, and the implied
  perpetual growth for an exit-multiple TV. Explain any gap with peer multiples.
- Report TV as a share of EV; above 75% the valuation rests mainly on the terminal assumption and
  the report must say so.

## Sensitivity and scenarios
- 5×5 (or 7×7) two-way tables, odd sizes, centre cell = the base case (checked automatically).
  Standard axes: WACC × terminal growth; growth × margin for operating risk.
- Bull, base and bear cases change named drivers only; list each driver's value per case.
- Reverse DCF: the constant growth a given price implies (`calc reverse_dcf`), to test whether the
  market price needs heroic assumptions.

## Comparables
- Same metric definitions across peers (calendarised to the same fiscal period where possible).
- Report n, min, p25, median, mean, p75, max; exclude non-meaningful (negative or missing) values
  and list the exclusions.

## Earnings reviews
- Each quarter vs the prior quarter and the year-ago quarter (`calc compare`).
- Tie out the print: operating income + opex = gross profit = revenue × gross margin (`calc tieout`).
  A failed tie-out downgrades the affected figures to PROVISIONAL until the source is re-read.
- GAAP and non-GAAP are labelled every time; the reconciliation items are named.
- Guidance is quoted verbatim, with its range and conditions (e.g. "excludes China data-centre compute").
- Consensus is UNKNOWN unless a licensed connector supplied it: never fill it from memory.

## Spreadsheets
When a workbook is requested, follow the FSI `dcf-model` / `3-statement-model` skills (formulas,
not hardcodes; blue inputs, black formulas) and check the workbook's base case equals
`merlin.calc` to the cent.
