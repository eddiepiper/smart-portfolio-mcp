# Merlin research: Semiconductor industry

*market research · as of 2026-11-14 · claude-cli · report `mrep_semis_20261114` · quality **PROVISIONAL***

> Produce a research report on the semiconductor industry: market size and growth, key competitors and their positioning (compute/AI accelerators, foundry, memory, equipment), industry drivers, and material risks (cyclicality, export controls, concentration, valuation). Use primary sources (SIA/WSTS, company IR releases, TSMC, BIS) and the Anthropic sector-overview / competitive-analysis method.

## Executive summary

On the caller-supplied primary releases, the semiconductor industry is in an AI- and memory-led upcycle of exceptional magnitude. SIA reports Q3 2026 global sales of $517.7bn, up 24.1% QoQ, and September 2026 (3-month moving average) sales up 146.8% YoY. WSTS's Q2 update of its spring forecast puts 2026 at about $1,655bn (+~108%) and 2027 at about $2.1tn (+~29%), with memory up ~302% in 2026. Company prints corroborate the direction: NVIDIA Q2 FY27 revenue $96.2bn (+105.9% YoY, Data Center 92.5% of revenue, 66.2% GAAP operating margin), TSMC 3Q26 revenue $46.72bn (+41.2% YoY, 66.4% gross margin, 79% of wafer revenue at 7nm and below), Micron FQ4-26 revenue $54.23bn (+379% YoY) and AMD Q3 2026 revenue $13.38bn (+45%). All four guide to further sequential growth (TSMC midpoint +14.1%, NVIDIA +12.2%, AMD +27.1%, Micron +13.4%). A key analytical point: the WSTS full-year 2026 figure is already too low on its own arithmetic — it implies Q4 sales of only $435.3bn, 15.9% below SIA's Q3 actual, while the companies guide up — so the WSTS autumn forecast (conference Nov 16-19, 2026) is the next reference-setting data point. Material risks are a memory-price-driven cycle (memory growth far outruns logic), concentration of leading-edge supply in TSMC and of demand in AI data-center capex, US export controls that NVIDIA already excludes from guidance for China data-center compute, and valuation, which this run could not assess because no market-price source was available. All sourced figures are PROVISIONAL (caller-supplied, not re-fetched in this run); several magnitudes are far outside historical industry ranges and should be re-verified against the original documents before reliance.

## Key findings

1. **[high]** Industry revenue is growing at a pace without modern precedent: SIA Q3 2026 sales $517.7bn (+24.1% QoQ), September 3MMA sales $172.6bn (+146.8% YoY); WSTS calculates 2026 at ~$1,655bn (+~108%) and 2027 at ~$2.1tn (+~29%). Q1-Q3 2026 cumulative sales are $1,219.7bn (WSTS H1 $702bn + SIA Q3). The September 3MMA x3 ($517.8bn) ties to the Q3 total within rounding. (F01, F02, F03, F04, F06, F07, F08, F09, F11, F14, F05, S1, S2)
2. **[high]** Memory is the swing factor of this cycle. WSTS expects memory to grow ~302% in 2026, versus ~108% for the total market; Micron's FQ4-26 revenue of $54.23bn is 4.8x the year-earlier $11.32bn (+379.1%), +30.8% QoQ, and FY2026 revenue was $133.19bn, with FQ1-27 guided to $61.5bn ±$1.5bn (+13.4% at midpoint). Growth this concentrated in a historically price-driven commodity segment makes the aggregate market more cyclical, not less. (F10, F36, F37, F38, F39, F40, F41, S2, S6)
3. **[high]** The WSTS full-year 2026 figure is internally inconsistent with the run-rate: $1,655bn less H1 $702bn less SIA Q3 $517.7bn leaves an implied Q4 of $435.3bn, 15.9% below Q3. Every company in the sample guides Q4/next quarter up sequentially (TSMC +14.1% at midpoint, NVIDIA +12.2%, AMD +27.1%, Micron +13.4%). Either WSTS's spring-based calculation understates 2026, or a sharp Q4 correction is embedded; the WSTS autumn forecast (Nov 16-19, 2026) should resolve this. Implied quarterly path from the same sources: Q1 ~$284.8bn, Q2 ~$417.2bn, Q3 $517.7bn. (F15, F16, F12, F13, F24, F33, F35, F41, S1, S2)
4. **[high]** Compute/AI accelerators: NVIDIA is the dominant scale player — Q2 FY27 revenue $96.22bn (+105.9% YoY), Data Center $89.0bn (92.5% of revenue), GAAP gross margin 75.0% and GAAP operating margin 66.2%, Q3 FY27 outlook $108.0bn ±2% (+12.2% sequential) while assuming no China data-center compute revenue. AMD is the principal merchant challenger at roughly one-seventh of NVIDIA's quarterly revenue ($13.38bn, +45% YoY, 54% GAAP / 56% non-GAAP gross margin) but guides faster sequential growth (Q4 ~$17bn, +27.1%). Fiscal periods differ (NVIDIA quarter ended July 2026; AMD September 2026). (F26, F27, F28, F29, F30, F31, F32, F33, F34, F35a, F35b, F35, S4, S5)
5. **[high]** Foundry: TSMC is the leading-edge bottleneck and is earning exceptional returns — 3Q26 revenue $46.72bn (+41.2% YoY, +16.2% QoQ), gross margin 66.4%, operating margin 58.7%; 79% of wafer revenue from 7nm and below (2nm 8%, 3nm 33%, 5nm 28%, 7nm 10%; node shares sum to 79%, tying to the reported total). 4Q26 guidance $52.3-54.3bn (midpoint $53.3bn, +14.1% QoQ) with 65-67% gross margin. TSMC's growth (+41%) is well below the market's (+~108%), consistent with memory, not logic wafers, driving most of the aggregate jump. (F17, F18, F19, F20, F21, F22, F23, F24, F25, S3, S2)
6. **[medium]** Export controls remain an active, moving variable rather than a settled regime: BIS moved to case-by-case license review for H200/MI325X-class exports to China (2026-01-13), issued enforcement guidance on advanced computing items for D:5/Macau-headquartered entities (2026-05-31), extended the Entity List to 50%-owned affiliates (2025-09-29), changed VEU status for foreign-owned fabs in China (2025-08-29) and rescinded the AI Diffusion Rule (2025-05-13). NVIDIA's outlook excluding China data-center compute shows the revenue at stake is already outside base guidance. (F32, S7, S4)
7. **[medium]** Equipment, Broadcom/custom silicon and Intel could not be profiled: no SEMI equipment-sales figure was found and Broadcom/Intel releases were not fetched. Valuation (P/E, EV/EBITDA, EV/Sales, historical ranges) could not be assessed because no price source was available. The competitive picture is therefore partial on two of the four requested segments and silent on valuation. (S8, S9, S10)

## Financial evidence

| id | metric | period | value | status | sources | derived by |
|---|---|---|---|---|---|---|
| F01 | Global semiconductor sales, Q3 2026 (SIA/WSTS) | Q3 2026 | 517.7 USD bn | PROVISIONAL | S1 | reported |
| F02 | Global semiconductor sales, September 2026 (3-month moving average) | Sep 2026 | 172.6 USD bn | PROVISIONAL | S1 | reported |
| F03 | Global sales growth, September 2026 vs September 2025 | Sep 2026 | 1.468 decimal YoY | PROVISIONAL | S1 | reported |
| F04 | Global sales growth, Q3 2026 vs Q2 2026 | Q3 2026 | 0.241 decimal QoQ | PROVISIONAL | S1 | reported |
| F05 | Tie-out: September 3MMA x 3 (vs reported Q3 $517.7bn) | Q3 2026 | 517.8 USD bn | PROVISIONAL | S1 | merlin.calc.arith (product 172.6 x 3) |
| F06 | WSTS full-year 2026 market (spring forecast with Q2 actuals) | FY2026 | 1,655 USD bn | PROVISIONAL | S2 | reported |
| F07 | WSTS 2026 market growth (approximate) | FY2026 | 1.08 decimal YoY | PROVISIONAL | S2 | reported |
| F08 | WSTS 2027 market (approximate) | FY2027 | 2,100 USD bn | PROVISIONAL | S2 | reported |
| F09 | WSTS 2027 market growth (approximate) | FY2027 | 0.29 decimal YoY | PROVISIONAL | S2 | reported |
| F10 | WSTS memory segment growth 2026 | FY2026 | 3.02 decimal YoY | PROVISIONAL | S2 | reported |
| F11 | Global semiconductor sales, H1 2026 (WSTS) | H1 2026 | 702 USD bn | PROVISIONAL | S2 | reported |
| F12 | Implied Q2 2026 global sales (Q3 / 1.241) | Q2 2026 | 417.2 USD bn | PROVISIONAL | S1 | merlin.calc.arith (ratio 517.7 / 1.241) |
| F13 | Implied Q1 2026 global sales (WSTS H1 less implied Q2) | Q1 2026 | 284.8 USD bn | PROVISIONAL | S1, S2 | merlin.calc.arith (diff 702 - 417.163578) |
| F14 | Cumulative global sales Q1-Q3 2026 (WSTS H1 + SIA Q3) | 9M 2026 | 1,220 USD bn | PROVISIONAL | S1, S2 | merlin.calc.arith (sum 702 + 517.7) |
| F15 | Implied Q4 2026 sales consistent with WSTS FY2026 $1,655bn | Q4 2026 | 435.3 USD bn | PROVISIONAL | S1, S2 | merlin.calc.arith (diff 1655 - 702 - 517.7) |
| F16 | Implied Q4 2026 vs Q3 2026 actual | Q4 2026 | -0.1592 decimal QoQ | PROVISIONAL | S1, S2 | merlin.calc.compare (435.3 vs 517.7) |
| F17 | TSMC revenue | 3Q26 | 46.72 USD bn | PROVISIONAL | S3 | reported |
| F18 | TSMC revenue growth YoY | 3Q26 | 0.412 decimal YoY | PROVISIONAL | S3 | reported |
| F19 | TSMC gross margin | 3Q26 | 0.664 decimal | PROVISIONAL | S3 | reported |
| F20 | TSMC operating margin | 3Q26 | 0.587 decimal | PROVISIONAL | S3 | reported |
| F21 | TSMC wafer revenue share, 7nm and below | 3Q26 | 0.79 decimal | PROVISIONAL | S3 | reported |
| F22 | Tie-out: sum of 2nm+3nm+5nm+7nm shares | 3Q26 | 79 percent of wafer revenue | PROVISIONAL | S3 | merlin.calc.arith (sum 8+33+28+10) |
| F23 | TSMC 4Q26 revenue guidance midpoint | 4Q26 | 53.3 USD bn | PROVISIONAL | S3 | merlin.calc.arith (sum 52.3+54.3 = 106.6; ratio 106.6 / 2) |
| F24 | TSMC guided sequential growth at midpoint | 4Q26 | 0.1408 decimal QoQ | PROVISIONAL | S3 | merlin.calc.compare (53.3 vs 46.72) |
| F25 | TSMC wafer revenue share, 3nm | 3Q26 | 0.33 decimal | PROVISIONAL | S3 | reported |
| F26 | NVIDIA revenue | Q2 FY27 (quarter ended Jul 2026) | 96,221 USD m | PROVISIONAL | S4 | reported |
| F27 | NVIDIA revenue growth YoY (vs $46,743m) | Q2 FY27 | 1.059 decimal YoY | PROVISIONAL | S4 | merlin.calc.compare (96221 vs 46743) |
| F28 | NVIDIA Data Center revenue | Q2 FY27 | 89 USD bn | PROVISIONAL | S4 | reported |
| F29 | NVIDIA Data Center share of revenue | Q2 FY27 | 0.925 decimal | PROVISIONAL | S4 | merlin.calc.arith (share 89.0 / 96.221) |
| F30 | NVIDIA GAAP gross margin | Q2 FY27 | 0.75 decimal | PROVISIONAL | S4 | reported |
| F31 | NVIDIA GAAP operating margin | Q2 FY27 | 0.6624 decimal | PROVISIONAL | S4 | merlin.calc.ratios (operating income 63734 / revenue 96221) |
| F32 | NVIDIA Q3 FY27 revenue outlook (±2%, excludes China data-center compute) | Q3 FY27 | 108 USD bn | PROVISIONAL | S4 | reported |
| F33 | NVIDIA guided sequential growth | Q3 FY27 | 0.1224 decimal QoQ | PROVISIONAL | S4 | merlin.calc.compare (108.0 vs 96.221) |
| F34 | AMD revenue | Q3 2026 | 13,377 USD m | PROVISIONAL | S5 | reported |
| F35a | AMD revenue growth YoY (as reported) | Q3 2026 | 0.45 decimal YoY | PROVISIONAL | S5 | reported |
| F35b | AMD GAAP gross margin | Q3 2026 | 0.54 decimal | PROVISIONAL | S5 | reported |
| F35 | AMD guided sequential growth (Q4 ~$17bn ±3% vs Q3) | Q4 2026 | 0.2708 decimal QoQ | PROVISIONAL | S5 | merlin.calc.compare (17.0 vs 13.377) |
| F36 | Micron revenue | FQ4-26 (ended 2026-09-03) | 54.23 USD bn | PROVISIONAL | S6 | reported |
| F37 | Micron revenue growth YoY (vs $11.32bn) | FQ4-26 | 3.791 decimal YoY | PROVISIONAL | S6 | merlin.calc.compare (54.23 vs 11.32) |
| F38 | Micron revenue growth QoQ (vs $41.46bn) | FQ4-26 | 0.308 decimal QoQ | PROVISIONAL | S6 | merlin.calc.compare (54.23 vs 41.46) |
| F39 | Micron FY2026 revenue | FY2026 | 133.2 USD bn | PROVISIONAL | S6 | reported |
| F40 | Micron FQ1-27 revenue guidance midpoint (±$1.5bn) | FQ1-27 | 61.5 USD bn | PROVISIONAL | S6 | reported |
| F41 | Micron guided sequential growth | FQ1-27 | 0.1341 decimal QoQ | PROVISIONAL | S6 | merlin.calc.compare (61.5 vs 54.23) |

## Valuation / modelling (ratios)

| assumption | value | status | rationale |
|---|---|---|---|
| SIA and WSTS series are on the same basis | True | ASSUMPTION | SIA publishes WSTS data; H1 (WSTS) and Q3 (SIA) are combined to derive the quarterly path. |
| WSTS 108% growth figure is approximate | 1.08 | ASSUMPTION | WSTS states 'approximately 108 percent'; no implied 2025 base is computed from it. |

| output | value |
|---|---|

Model limitations:
- No valuation multiples: no price or market-data source was available.
- Company fiscal quarters are not aligned (NVIDIA to Jul-2026, Micron to 3-Sep-2026, TSMC/AMD to Sep-2026).
- Implied Q1/Q2/Q4 values are arithmetic derivations, not reported figures.

## Bull, base and bear

| case | value | key assumptions | narrative |
|---|---|---|---|
| bull | - | {'q4_2026_vs_q3': 'positive, consistent with company guidance', 'wsts_2027': '>= 2.1tn USD', 'memory_pricing': 'stable to rising'} | AI data-center capex and memory pricing hold through 2027: companies deliver on guided sequential growth (TSMC +14%, NVIDIA +12%, AMD +27%, Micron +13%), WSTS revises 2026 above $1,655bn at its Nov 16-19 conference and 2027 tracks or exceeds ~$2.1tn; China licensing under the January 2026 case-by-case policy adds revenue not in NVIDIA's guidance. |
| base | - | {'q4_2026_vs_q3': 'around guided levels', 'wsts_2027_growth': 0.29, 'export_controls': 'no major new restriction'} | Q4 2026 broadly flat to up on Q3 as guided by the logic and memory leaders, WSTS lifts 2026 toward the run-rate and forecasts decelerating but positive 2027 growth (~29% on its current calculation); export-control policy stays restrictive but stable. |
| bear | - | {'memory_growth_2027': 'negative', 'accelerator_demand': 'capex digestion', 'export_controls': 'tightened'} | Memory supply catches up and prices reset — the segment that grew ~302% in 2026 contracts — while hyperscaler capex digestion slows accelerator orders; the WSTS-implied Q4 decline (−15.9% QoQ) becomes the template for 2027, and tighter export controls or entity-list extensions remove further China revenue. |

## Risks and counterarguments

- Cyclicality / memory price reversal: memory growth (~302% in 2026) and Micron's +379% YoY quarter are price-led and historically mean-revert; a memory downturn alone could turn aggregate industry growth negative. (S2, S6)
- Demand concentration: NVIDIA's Data Center segment is 92.5% of its revenue, and accelerator, HBM and leading-edge foundry demand all rest on the same AI data-center capex cycle — a slowdown hits compute, foundry and memory simultaneously. (S4, S3)
- Supply concentration: 79% of TSMC wafer revenue is at 7nm and below; leading-edge logic for the main accelerator vendors runs through one foundry and one geography (Taiwan), a single-point geopolitical and operational exposure. (S3)
- Export controls: BIS rules on advanced computing items (case-by-case China licensing, D:5/Macau enforcement guidance, Entity List 50% affiliates rule, VEU changes for China fabs) change frequently; NVIDIA already excludes China data-center compute from its outlook. (S7, S4)
- Valuation: could not be assessed in this run (no price source). After revenue doubling and record margins (NVIDIA 66.2% operating, TSMC 58.7%), the risk is that peak-cycle earnings are capitalised as durable; multiples must be checked before any conclusion. (S10)
- Data reliability: all figures come from a caller-supplied pack not re-fetched in this run, and the magnitudes (market +~108%, September +146.8% YoY) are far outside historical industry ranges; a transcription or retrieval error would change the conclusions. (S1, S2)
- Counterargument: The WSTS-implied Q4 decline may be an artefact of a spring forecast mechanically updated with Q2 actuals rather than a real signal; company guidance points the other way.
- Counterargument: TSMC's +41.2% YoY growth is far below the market's ~108%, so the logic/foundry layer is growing much more modestly than the headline — the headline is heavily memory-price-driven and overstates volume growth.
- Counterargument: Margins at NVIDIA (75% gross) and TSMC (66.4% gross) reflect scarcity pricing; new capacity (foundry, HBM) and AMD/custom-silicon competition could compress them even if volumes keep rising.
- Counterargument: Export controls may loosen as well as tighten: the January 2026 case-by-case policy and the 2025 rescission of the AI Diffusion Rule show policy moving in both directions, so China is an upside as well as a downside variable.

## Unknowns and uncertainty

- Peer valuation multiples (P/E, EV/EBITDA, EV/Sales) and their historical ranges — no market-price source available in this environment.
- Semiconductor equipment market size and leaders (ASML, Applied Materials, Lam, Tokyo Electron, KLA) — SEMI figure not found; company releases not fetched.
- Broadcom (custom AI accelerators/networking) and Intel latest quarter — not fetched.
- TSMC revenue by platform (HPC vs smartphone) — chart image, not extractable.
- WSTS logic vs memory vs analog breakdown in dollars — chart image, not extractable; only memory growth rate available.
- Samsung and SK hynix memory results — not retrieved, so memory market share cannot be estimated.
- WSTS autumn 2026 forecast (conference Nov 16-19, 2026) — not yet published.
- Five-year historical CAGR of the market — no historical series in the evidence pack.

## Research implications

- **marcus**: Treat the sector backdrop as an exceptionally strong but late-stage, memory-led upcycle with unresolved valuation. Any single-name opportunity in compute, foundry or memory should be evaluated with this report's cyclicality and concentration risks as the standing challenge, and with valuation work done separately once a price source is available.
- **quant**: Challenge points: (1) reconcile WSTS FY2026 $1,655bn with the SIA Q3 run-rate (implied Q4 −15.9% QoQ vs +12-27% guided by companies); (2) test how much of 2026 growth is memory price versus volume; (3) re-fetch S1-S6 to confirm the unusually large magnitudes before using them in any model; (4) stress the sector for a memory-price reset scenario.
- **owner**: The WSTS autumn forecast (Nov 16-19, 2026) is the next sector-level data point; NVIDIA's Q3 FY27 report and Micron's FQ1-27 report will test the guided sequential growth. Equipment, Broadcom/Intel and valuation are open gaps that a follow-up Merlin run with retrieval access can fill.
- **tradingagents**: Use the cited figures (SIA, WSTS, TSMC, NVIDIA, AMD, Micron) as fundamental context; they are PROVISIONAL and fiscal periods differ by company.

## Evidence register

| id | status | kind | title | publisher | document date | period | retrieved | url |
|---|---|---|---|---|---|---|---|---|
| S1 | PROVISIONAL | industry_body | Global Semiconductor Sales Increase 24.1% from Q2 2026 to Q3 2026 | Semiconductor Industry Association (SIA) | 2026-11-03 | Q3 2026 / Sep 2026 | 2026-11-14T12:20+00:00 | https://www.semiconductors.org/global-semiconductor-sales-increase-24-1-from-q2-2026-to-q3-2026/ |
| S2 | PROVISIONAL | industry_body | Global Semiconductor Market records exceptional growth in Q2 2026 | World Semiconductor Trade Statistics (WSTS) | 2026-08-06 | H1 2026; FY2026-FY2027 calculation | 2026-11-14T12:20+00:00 | https://www.wsts.org/76/Global-Semiconductor-Market-records-exceptional-growth-in-Q2-2026 |
| S3 | PROVISIONAL | press_release | TSMC 3Q26 earnings release and management presentation | Taiwan Semiconductor Manufacturing Co. (Investor Relations) | 2026-10-15 | 3Q26; 4Q26 guidance | 2026-11-14T12:20+00:00 | https://investor.tsmc.com/english/quarterly-results/2026/q3 |
| S4 | PROVISIONAL | press_release | NVIDIA Announces Financial Results for Second Quarter Fiscal 2027 | NVIDIA Corporation | 2026-08-26 | Q2 FY27; Q3 FY27 outlook | 2026-11-14T12:20+00:00 | https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027 |
| S5 | PROVISIONAL | press_release | AMD Reports Third Quarter 2026 Financial Results | Advanced Micro Devices, Inc. | 2026-11-03 | Q3 2026; Q4 2026 outlook | 2026-11-14T12:20+00:00 | https://ir.amd.com/news-events/press-releases/detail/1303/amd-reports-third-quarter-2026-financial-results |
| S6 | PROVISIONAL | press_release | Micron Technology, Inc. Reports Record Fiscal Fourth-Quarter and Full-Year 2026 Results | Micron Technology, Inc. | 2026-09-30 | FQ4-26 (ended 2026-09-03), FY2026; FQ1-27 guidance | 2026-11-14T12:20+00:00 | https://investors.micron.com/news/press-release/2026/Micron-Technology-Inc--Reports-Record-Fiscal-Fourth-Quarter-and-Full-Year-2026-Results/ |
| S7 | PROVISIONAL | other | BIS News & Updates (export-control actions on semiconductors and advanced computing) | U.S. Department of Commerce, Bureau of Industry and Security | - | 2025-05-13 to 2026-05-31 actions | 2026-11-14T12:20+00:00 | https://www.bis.gov/news-updates |
| S8 | FAILED | industry_body | SEMI worldwide semiconductor equipment sales | SEMI | - | - | - | https://www.semi.org |
| S9 | UNKNOWN | press_release | Broadcom and Intel latest-quarter results | Broadcom Inc.; Intel Corporation | - | - | - | - |
| S10 | UNKNOWN | market_data | Peer market prices and valuation multiples | n/a | - | - | - | - |

## Quality status

**PROVISIONAL**. PROVISIONAL: all figures rest on caller-supplied primary-source quotes (SIA, WSTS, TSMC, NVIDIA, AMD, Micron, BIS) that were not re-read from the original documents in this run. Derived figures were computed with merlin-calc/1.0 and tie out where the print allowed (September 3MMA x3 = $517.8bn vs Q3 $517.7bn; TSMC node shares sum to 79%). Coverage is incomplete on equipment and valuation (SEMI FAILED; Broadcom/Intel and market prices UNKNOWN), which is stated in unknowns rather than estimated. Validator: PASS, no issues.

Limitations:
- Evidence was supplied by the caller and not re-fetched in this run; every sourced figure is PROVISIONAL.
- Equipment segment, Broadcom/Intel, Samsung/SK hynix and valuation are not covered with data.
- Fiscal periods are not aligned across companies.
- Market shares by segment could not be computed (no segment totals for logic, accelerators or foundry).

## Capabilities used

| capability | kind | genuine execution | detail |
|---|---|---|---|
| sector-overview (anthropics/financial-services, vendored) | fsi_plugin_skill | yes | Loaded with the Skill tool; its market overview / industry structure / competitive landscape / valuation context / implications structure was followed. Its data connectors were not available. |
| competitive-analysis (anthropics/financial-services, vendored) | fsi_plugin_skill | yes | Loaded with the Skill tool; method used for industry-defining metrics, segment grouping (compute, foundry, memory, equipment) and moat synthesis. No deck produced; interactive scoping questions skipped per Merlin defaults (headless run). |
| python -m merlin calc (compare, arith, ratios) | merlin_calc | yes | All growth rates, shares, tie-outs, guidance midpoints and implied quarterly figures computed with merlin-calc/1.0. |
| python -m merlin validate | merlin_skill | yes | Report validated before return: status PASS, no issues, suggested quality PROVISIONAL. |
| Caller-supplied evidence pack (web fetch in an earlier Merlin Claude Cloud session) | web_fetch | no | Retrieval did not execute in this run; pages were fetched by the caller 2026-11-14 ~12:20 UTC and supplied as data. Not re-fetched here, so all sourced figures are PROVISIONAL. |
| Sector synthesis, competitive positioning and risk assessment | ordinary_analysis | no | Analyst-written synthesis on top of the cited figures. |

---
*Research only. Merlin does not trade, rate or make binding portfolio decisions; Marcus integrates the evidence and Quant challenges it.*
