# Merlin research: Synthetic Co (SYNTHCO)

*financial model · as of 2026-11-14 · claude-cli · report `mrep_synthco_dcf_20261114` · quality **PROVISIONAL***

> Construct a basic, reproducible 5-year FCFF DCF for a SYNTHETIC company using caller-supplied synthetic inputs: WACC build, FCFF schedule, EV-to-equity bridge, value per share, TV share of EV, implied exit multiple, 5x5 WACC x terminal-growth sensitivity centred on base, bull/base/bear.

## Executive summary

Synthetic Co is a SYNTHETIC test company. Every input is a caller-supplied ASSUMPTION, not fact, and no outside evidence was retrieved. The model follows the Anthropic dcf-model method, and merlin.calc ran all of the arithmetic. CAPM cost of equity is 9.70% (4.2% + 1.1 x 5.0%), after-tax cost of debt is 4.345% and the weights are 90.9% equity / 9.1% debt, giving a WACC of 9.2132%. Revenue grows from USD 10,000m to USD 14,809m in year 5, and FCFF rises from USD 1,361m to USD 1,887m. The PV of FCFF sums to USD 6,261m and the PV of the Gordon-growth terminal value (g 2.5%) is USD 18,546m, so EV is USD 24,807m. After net debt of USD 1,500m, equity value is USD 23,307m, or USD 46.61 per diluted share on 500m shares. Terminal value is 74.8% of EV, just under the 75% flag, and the implied exit multiple is 8.84x year-5 EBITDA. The 5x5 grid of WACC (8.21%-10.21%) by g (1.5%-3.5%) runs from USD 36.23 to USD 65.73 per share, and its centre equals the base case. The bull case (21% EBIT margin, growth +2pts) gives USD 60.20 per share and the bear case (15% margin, growth -3pts) gives USD 33.52. The terminal assumption and the margin drive most of the value. The arithmetic is internally consistent on assumed inputs and is not a view on any real security.

## Key findings

1. **[high]** Base-case DCF value is USD 46.61 per diluted share (equity USD 23,307m = EV USD 24,807m less net debt USD 1,500m), with all inputs synthetic ASSUMPTIONS and arithmetic by merlin.calc.dcf. (F_VPS, F_EQ, F_EV, F_NETDEBT, E1)
2. **[high]** Terminal value carries 74.8% of EV (PV TV USD 18,546m vs PV of explicit FCFF USD 6,261m), at the edge of the 75% flag; in the bull case it crosses it (75.4%), so the result rests mainly on WACC and terminal growth. (F_TVSHARE, F_PVTV, F_SUMPV, F_BULL_TVSHARE, E1)
3. **[high]** WACC is 9.2132%: CAPM cost of equity 9.70% weighted 90.9% plus after-tax cost of debt 4.345% weighted 9.1%, using the supplied market equity (USD 30,000m) and gross debt (USD 3,000m). (F_WACC, F_KE, F_KD_AT, F_WE, F_WD, E1)
4. **[medium]** Sensitivity is wide: across WACC 8.21%-10.21% and terminal growth 1.5%-3.5%, value per share ranges USD 36.23 to USD 65.73; the centre cell equals the base case (USD 46.61). (F_SENS_MIN, F_SENS_MAX, F_VPS, E1)
5. **[medium]** Scenario range is USD 33.52 (bear) to USD 60.20 (bull) per share around the USD 46.61 base; growth and margin were flexed together, so their separate contributions are not isolated. (F_BEAR_VPS, F_BULL_VPS, F_VPS, E1)
6. **[low]** The Gordon-growth terminal value implies 8.84x year-5 EBITDA (TV USD 28,815m / EBITDA USD 3,258m), a plausible cross-check for a mature 2.5%-growth business. (F_EXITMULT, F_TV, F_EBITDA5, E1)
7. **[low]** Internal tension in the synthetic inputs: the market equity value used for WACC weights (USD 30,000m) is above the DCF equity value (USD 23,307m), so the capital weights are not consistent with the model output. (F_MKT_EQ, F_EQ, E1)

## Financial evidence

| id | metric | period | value | status | sources | derived by |
|---|---|---|---|---|---|---|
| F_REV0 | Base-year revenue (synthetic input) | Year 0 | 10,000 USD m | ASSUMPTION | E1 | reported |
| F_RF | Risk-free rate (synthetic input) | - | 0.042 decimal | ASSUMPTION | E1 | reported |
| F_BETA | Equity beta (synthetic input) | - | 1.1 x | ASSUMPTION | E1 | reported |
| F_ERP | Equity risk premium (synthetic input) | - | 0.05 decimal | ASSUMPTION | E1 | reported |
| F_KD | Pre-tax cost of debt (synthetic input) | - | 0.055 decimal | ASSUMPTION | E1 | reported |
| F_TAX | Cash tax rate (synthetic input) | - | 0.21 decimal | ASSUMPTION | E1 | reported |
| F_MKT_EQ | Market equity value for WACC weights (synthetic input) | - | 30,000 USD m | ASSUMPTION | E1 | reported |
| F_DEBT | Debt for WACC weights (synthetic input) | - | 3,000 USD m | ASSUMPTION | E1 | reported |
| F_G | Terminal growth (synthetic input) | - | 0.025 decimal | ASSUMPTION | E1 | reported |
| F_NETDEBT | Net debt (synthetic input) | - | 1,500 USD m | ASSUMPTION | E1 | reported |
| F_SHARES | Diluted shares (synthetic input) | - | 500 m shares | ASSUMPTION | E1 | reported |
| F_KE | Cost of equity (CAPM) | - | 0.097 decimal | ASSUMPTION | E1 | merlin.calc.wacc |
| F_KD_AT | After-tax cost of debt | - | 0.04345 decimal | ASSUMPTION | E1 | merlin.calc.wacc |
| F_WE | Equity weight | - | 0.9091 decimal | ASSUMPTION | E1 | merlin.calc.wacc |
| F_WD | Debt weight | - | 0.09091 decimal | ASSUMPTION | E1 | merlin.calc.wacc |
| F_WACC | WACC | - | 0.09213 decimal | ASSUMPTION | E1 | merlin.calc.wacc |
| F_REV5 | Revenue, year 5 | Year 5 | 14,809 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_FCFF1 | FCFF, year 1 | Year 1 | 1,361 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_FCFF2 | FCFF, year 2 | Year 2 | 1,517 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_FCFF3 | FCFF, year 3 | Year 3 | 1,660 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_FCFF4 | FCFF, year 4 | Year 4 | 1,785 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_FCFF5 | FCFF, year 5 | Year 5 | 1,887 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_EBITDA5 | EBITDA, year 5 | Year 5 | 3,258 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_SUMPV | Sum of PV of FCFF (years 1-5) | - | 6,261 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_TV | Terminal value (Gordon growth, end of year 5) | - | 28,815 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_PVTV | PV of terminal value | - | 18,546 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_EV | Enterprise value (base) | - | 24,807 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_EQ | Equity value (base) | - | 23,307 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_VPS | Value per diluted share (base) | - | 46.61 USD/share | ASSUMPTION | E1 | merlin.calc.dcf |
| F_TVSHARE | PV of terminal value as share of EV (base) | - | 0.7476 decimal | ASSUMPTION | E1 | merlin.calc.dcf |
| F_EXITMULT | Implied exit multiple (TV / year-5 EBITDA) | - | 8.845 x | ASSUMPTION | E1 | merlin.calc.dcf |
| F_SENS_MIN | Sensitivity grid minimum value per share (WACC 10.2132%, g 1.5%) | - | 36.23 USD/share | ASSUMPTION | E1 | merlin.calc.dcf |
| F_SENS_MAX | Sensitivity grid maximum value per share (WACC 8.2132%, g 3.5%) | - | 65.73 USD/share | ASSUMPTION | E1 | merlin.calc.dcf |
| F_BULL_EV | Enterprise value (bull) | - | 31,601 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_BULL_VPS | Value per diluted share (bull) | - | 60.2 USD/share | ASSUMPTION | E1 | merlin.calc.dcf |
| F_BULL_TVSHARE | PV of terminal value as share of EV (bull) | - | 0.7538 decimal | ASSUMPTION | E1 | merlin.calc.dcf |
| F_BEAR_EV | Enterprise value (bear) | - | 18,261 USD m | ASSUMPTION | E1 | merlin.calc.dcf |
| F_BEAR_VPS | Value per diluted share (bear) | - | 33.52 USD/share | ASSUMPTION | E1 | merlin.calc.dcf |
| F_BEAR_TVSHARE | PV of terminal value as share of EV (bear) | - | 0.7376 decimal | ASSUMPTION | E1 | merlin.calc.dcf |

## Valuation / modelling (dcf)

| assumption | value | status | rationale |
|---|---|---|---|
| base_revenue | 10000 | ASSUMPTION | Caller-supplied synthetic base-year revenue, USD m. |
| revenue_growth | [0.12, 0.1, 0.08, 0.06, 0.05] | ASSUMPTION | Caller-supplied; decelerates toward terminal growth, consistent with the dcf-model growth framework. |
| ebit_margin | 0.18 | ASSUMPTION | Caller-supplied, flat across the projection. |
| cash_tax_rate | 0.21 | ASSUMPTION | Caller-supplied; applied to EBIT for NOPAT and to cost of debt for the tax shield. |
| da_pct_revenue | 0.04 | ASSUMPTION | Caller-supplied. |
| capex_pct_revenue | 0.05 | ASSUMPTION | Caller-supplied; capex exceeds D&A by 1pt of revenue, consistent with a growing asset base. |
| nwc_pct_of_revenue_change | 0.1 | ASSUMPTION | Caller-supplied; change in NWC = 10% of the revenue change (a use of cash while growing). |
| capm_inputs | {'risk_free': 0.042, 'beta': 1.1, 'equity_risk_premium': 0.05} | ASSUMPTION | Caller-supplied synthetic CAPM inputs; not market observations. |
| cost_of_debt_and_weights | {'pre_tax_cost_of_debt': 0.055, 'equity_value': 30000, 'debt_value': 3000} | ASSUMPTION | Caller-supplied; weights use gross debt as instructed (the dcf-model skill text uses net debt; the assignment governs). |
| terminal_growth | 0.025 | ASSUMPTION | Caller-supplied; below WACC and within long-run nominal GDP. |
| net_debt_and_shares | {'net_debt': 1500, 'diluted_shares': 500} | ASSUMPTION | Caller-supplied; implies cash of USD 1,500m against USD 3,000m gross debt. |
| discounting_convention | end-year | ASSUMPTION | Merlin modelling convention (end-year unless stated); the dcf-model skill default is mid-year, which would raise value. TV discounted from the end of year 5. |
| scenario_overrides | {'bull': {'ebit_margin': 0.21, 'growth': [0.14, 0.12, 0.1, 0.08, 0.07]}, 'bear': {'ebit_margin': 0.15, 'growth': [0.09, 0.07, 0.05, 0.03, 0.02]}} | ASSUMPTION | Caller-supplied; WACC, terminal growth, tax and capital intensity held at base in all cases. |

| output | value |
|---|---|
| sum_pv_fcff | 6,261.2705 |
| terminal_value | 28,815.3026 |
| pv_terminal_value | 18,545.8833 |
| enterprise_value | 24,807.1538 |
| net_debt | 1,500.0000 |
| equity_value | 23,307.1538 |
| value_per_share | 46.6143 |
| tv_share_of_ev | 0.7476 |
| implied_exit_multiple | 8.8445 |

Sensitivity of value_per_share (discount_rate down, terminal_growth across); centre = base case (checked)

| | 0.015 | 0.02 | 0.025 | 0.03 | 0.035 |
|---|---|---|---|---|---|
| 0.082132 | 48.33 | 51.63 | 55.51 | 60.13 | 65.73 |
| 0.087132 | 44.67 | 47.46 | 50.70 | 54.51 | 59.05 |
| 0.092132 | 41.49 | 43.87 | 46.61 | 49.79 | 53.53 |
| 0.097132 | 38.70 | 40.75 | 43.09 | 45.78 | 48.91 |
| 0.102132 | 36.23 | 38.01 | 40.03 | 42.33 | 44.97 |

Model limitations:
- All inputs are synthetic; outputs describe no real company or security.
- End-year discounting; a mid-year convention would give a higher value.
- WACC weights use supplied market equity (USD 30,000m) and gross debt (USD 3,000m), not iterated to the DCF equity value.
- Flat margin and fixed capital-intensity ratios; no operating-leverage or reinvestment-to-growth (ROIC) consistency check is modelled.
- Scenarios flex only growth and margin; WACC and terminal growth are not varied across cases.
- No Excel workbook was produced, so the dcf-model formatting, cell-comment and recalc steps do not apply.

## Bull, base and bear

| case | value | key assumptions | narrative |
|---|---|---|---|
| bull | 60.20 USD/share | {'ebit_margin': 0.21, 'growth': [0.14, 0.12, 0.1, 0.08, 0.07], 'wacc': 0.092132, 'terminal_growth': 0.025} | EBIT margin 21% and growth 14/12/10/8/7%: EV USD 31,601m, value per share USD 60.20; TV share 75.4%, above the 75% flag. |
| base | 46.61 USD/share | {'ebit_margin': 0.18, 'growth': [0.12, 0.1, 0.08, 0.06, 0.05], 'wacc': 0.092132, 'terminal_growth': 0.025} | EBIT margin 18% and growth 12/10/8/6/5%: EV USD 24,807m, value per share USD 46.61; TV share 74.8%. |
| bear | 33.52 USD/share | {'ebit_margin': 0.15, 'growth': [0.09, 0.07, 0.05, 0.03, 0.02], 'wacc': 0.092132, 'terminal_growth': 0.025} | EBIT margin 15% and growth 9/7/5/3/2%: EV USD 18,261m, value per share USD 33.52; TV share 73.8%. |

## Risks and counterarguments

- Terminal-value dependence: about three-quarters of EV sits in the terminal value; in the 2.5% growth column of the grid, each 0.5pt WACC step moves value per share by roughly USD 3-5 (USD 55.51 at 8.21% to USD 40.03 at 10.21%). (E1)
- Operating assumptions matter: the joint growth-and-margin flex moves value per share from USD 33.52 (bear) to USD 60.20 (bull). (E1)
- Capital weights are inconsistent with the model output (market equity input above DCF equity), so the WACC is not self-consistent. (E1)
- Counterargument: A mid-year discounting convention, as the dcf-model skill defaults to, would raise the base value; end-year is the more conservative choice here.
- Counterargument: The 8.84x implied exit multiple looks unremarkable, which supports the terminal assumption as a cross-check, but it is itself derived from the same assumptions.
- Counterargument: Scenarios hold WACC and terminal growth fixed; combining bear operating assumptions with a higher WACC would widen the downside beyond USD 33.52.

## Unknowns and uncertainty

- No real-world data: comparable company multiples, an observed beta or a market cost of debt to test the synthetic inputs are not available.
- Whether capex at 5% of revenue is sufficient to sustain the assumed growth (no ROIC or reinvestment-rate check).
- Whether the synthetic net debt of USD 1,500m captures all debt-like items (leases, pensions, minorities).

## Research implications

- **quant**: Challenge the terminal-value reliance (74.8% base, 75.4% bull), the gross-debt WACC weighting and the end-year convention; reproduce with python -m merlin reproduce.
- **owner**: This is a reproducible template on synthetic inputs, useful for testing the pipeline; it carries no information about any real security.
- **marcus**: No action relevance: SYNTHCO is synthetic. Treat as a calculator and contract test only.

## Evidence register

| id | status | kind | title | publisher | document date | period | retrieved | url |
|---|---|---|---|---|---|---|---|---|
| E1 | ASSUMPTION | synthetic | Caller-supplied synthetic DCF inputs for Synthetic Co (assignment mreq_12e8a4abea0d) | Caller (requested_by: test) | 2026-11-14 | Synthetic base year + 5 projection years | 2026-11-14T12:00+00:00 | - |
| E2 | PROVISIONAL | other | Anthropic dcf-model skill (method reference) | Anthropic financial-services (vendored skill) | - | - | 2026-11-14T12:00+00:00 | .claude/skills/dcf-model/SKILL.md |

## Quality status

**PROVISIONAL**. All figures rest on caller-supplied synthetic ASSUMPTIONS (E1), so the ceiling is PROVISIONAL. merlin.calc ran all of the arithmetic, the stored DCF reproduces, and the sensitivity centre equals the base case. merlin validate returned PASS with no issues. It ran on a scratchpad file, because the permission layer refused the inline-JSON command.

Limitations:
- Synthetic inputs only; no primary evidence retrieved or needed.
- End-year discounting and gross-debt WACC weights, as noted in the model block.
- No Excel deliverable produced.

## Capabilities used

| capability | kind | genuine execution | detail |
|---|---|---|---|
| dcf-model (Anthropic financial-services skill) | fsi_plugin_skill | yes | Skill loaded via the Skill tool; its FCFF build order, CAPM/WACC method, Gordon-growth terminal value, TV-share sanity check (flag above 75%), EV-to-equity bridge, odd-sized sensitivity with base-case centre and bear/base/bull blocks were followed. No Excel workbook was produced (not requested); recalc.py not run. |
| merlin calc wacc | merlin_calc | yes | python -m merlin calc wacc: CAPM cost of equity, after-tax cost of debt, capital weights, WACC. |
| merlin calc dcf (with --sensitivity and --scenarios) | merlin_calc | yes | python -m merlin calc dcf: FCFF schedule, discounting, Gordon TV, bridge, implied exit multiple, 5x5 WACC x g grid, bull/base/bear cases. |
| merlin validate | merlin_calc | yes | python -m merlin validate returned PASS (0 issues, suggested quality PROVISIONAL). The inline-JSON form was refused by the headless permission layer (command too long), so the report was written to a scratchpad JSON file and validated by path. |
| Report drafting and interpretation | ordinary_analysis | no | Narrative, findings, risks and implications written by Merlin. |

---
*Research only. Merlin does not trade, rate or make binding portfolio decisions; Marcus integrates the evidence and Quant challenges it.*
