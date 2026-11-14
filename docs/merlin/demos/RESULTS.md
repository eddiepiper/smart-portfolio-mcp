# Merlin validation — three demonstration assignments (2026-11-14)

All three ran through the **headless Merlin runner** (`python -m merlin run`, i.e. the Grokbot /
Claude CLI path: `claude -p` with the Merlin spec, the official Anthropic financial-services
plugins installed from the `anthropics/financial-services` marketplace plus the vendored copies,
`--json-schema merlin-research/v1`). Every report was then validated deterministically and its
arithmetic re-checked independently, outside Merlin.

| Test | Verdict | Validation | Quality status | FSI skills that genuinely ran (from the CLI tool trace) | Cost | Turns |
|---|---|---|---|---|---|---|
| A — Semiconductor market research | **PASS** | PASS | PROVISIONAL | `sector-overview`, `competitive-analysis` | $1.34 | 28 |
| B — NVIDIA earnings review (Q2 FY27) | **PASS** | PASS | PROVISIONAL | `earnings-analysis` | $1.24 | 23 |
| C — Reproducible DCF (synthetic company) | **PASS** | PASS | PROVISIONAL (synthetic inputs) | `dcf-model` | $1.64 | 20 |

"Genuine execution" is taken from the CLI's stream (`tool_trace.json` in each folder), not from
Merlin's own claims: the runner fails any report that claims a skill, agent or calculator call
the trace does not show. All other text in the reports is ordinary Claude analysis.

PROVISIONAL (not VERIFIED) is the honest ceiling here: the headless runs could not fetch web
pages themselves (the container's proxy refused them), so they worked from an evidence pack
that the Claude Cloud session fetched from the original documents and passed in as caller data.
Merlin correctly declined to mark figures it had not re-read itself as VERIFIED.

## Test A — Semiconductor industry
Folder: `test-a-semiconductor-market/`

- **Capabilities used:** FSI `sector-overview` and `competitive-analysis` (genuine, traced);
  `merlin calc compare/arith/ratios` (17 derived figures, traced); `merlin validate` (traced);
  caller evidence pack from web fetch of SIA, WSTS, TSMC, NVIDIA, AMD, Micron and BIS pages.
- **Calculations verified independently:** NVIDIA revenue growth 105.85% y/y (96,221 / 46,743),
  Data Center share 92.50%, TSMC guided q/q growth at midpoint 14.08% (53.3 / 46.72), Micron
  growth 379.06% y/y and 30.80% q/q, implied Q4 2026 global sales $435.3bn ($1,655bn − $702bn −
  $517.7bn) — all match.
- **Key finding worth noting:** WSTS's spring-based 2026 figure ($1,655bn) implies Q4 2026 sales
  15.9% *below* Q3, while every company guide points up, so the WSTS figure is likely to be
  revised up at the 16–19 Nov forecast meeting. Merlin flagged this, not a source.
- **Sources:** SIA (2026-11-03), WSTS (2026-08-06), TSMC 3Q26 (2026-10-15), NVIDIA (2026-08-26),
  AMD (2026-11-03), Micron (2026-09-30), BIS news page. URLs in the report's evidence register.
- **Missing:** SEMI equipment data (not found), Broadcom, Intel, Samsung, SK hynix and ASML
  results (not fetched), TSMC platform split (image only), valuation multiples (no price feed).
- **Limitations:** valuation risk could not be quantified; no licensed connector, so no consensus
  or comps multiples.
- **Defect found and fixed:** run 2 forced simple sums through unrelated calculator functions
  (`tieout`, `ratios`). Added `calc arith` and a `calc_misuse` validator warning; run 3 used `arith`.
  Run 3 was first failed by the runner on a false positive (a `merlin validate` claim matched
  against Skill calls instead of Bash calls); fixed and re-checked from the stored trace.

## Test B — NVIDIA earnings review
Folder: `test-b-nvidia-earnings/`

- **Company/period:** NVIDIA Q2 FY2027 (quarter ended 2026-07-26, released 2026-08-26), the
  latest reported quarter as of 2026-11-14 (Q3 FY27 not yet out per NVIDIA IR).
- **Capabilities used:** FSI `earnings-analysis` (genuine, traced); `merlin calc compare/tieout/
  ratios/arith` (27 derived figures, traced); `merlin validate` (traced).
- **Calculations verified independently:** revenue +17.90% q/q and +105.85% y/y; GAAP operating
  margin 66.24%; GAAP EPS +127.78% y/y; guidance midpoint implies +12.24% q/q; FCF/net income
  35.75%. Tie-outs for three quarters: (operating income + opex) / revenue = 74.98%, 74.93% and
  72.42% vs reported 75.0%, 74.9% and 72.4%, which supports the transcription.
- **Findings Merlin raised:** sequential deceleration in the guide; FCF conversion fell to 36% of
  GAAP net income (from 83%) while capital returns (~$26bn) exceeded FCF; GAAP earnings inflated
  by mark-to-market equity gains; an unread CEO-transition release dated 2026-11-12.
- **Missing:** consensus (no licensed connector), call transcript, CFO commentary, SEC 8-K
  (EDGAR blocked); cash-flow lines came from a truncated part of the page and stay PROVISIONAL.

## Test C — Reproducible DCF
Folder: `test-c-synthetic-dcf/`

- **Inputs:** synthetic, all registered as ASSUMPTION (revenue $10,000m; growth 12/10/8/6/5%;
  EBIT 18%; tax 21%; D&A 4%; capex 5%; ΔNWC 10% of Δrevenue; rf 4.2%, β 1.1, ERP 5%, kd 5.5%,
  E $30,000m, D $3,000m; g 2.5%; net debt $1,500m; 500m shares).
- **Capabilities used:** FSI `dcf-model` (genuine, traced); `merlin calc wacc` and `merlin calc
  dcf --sensitivity --scenarios` (traced); `merlin validate` (traced).
- **Outputs:** WACC 9.2132%; EV $24,807.15m; equity $23,307.15m; **$46.61 per share**; terminal
  value 74.8% of EV; implied exit multiple 8.84× EBITDA; 5×5 sensitivity $36.23–$65.73 with the
  centre equal to the base case; bull $60.20, bear $33.52.
- **Independent check:** a separate implementation (not `merlin.calc`) gives EV $24,807.22m and
  $46.6144/share (bull $60.2024, bear $33.5220). The $0.07m EV gap comes from WACC rounded to six
  decimals. `python -m merlin reproduce` re-runs the stored inputs to the same outputs.
- **Merlin's own challenge:** it flagged that the market equity used for WACC weights ($30bn)
  exceeds its DCF equity value ($23.3bn), an internal inconsistency in the synthetic inputs.
- **Handoff tested:** `merlin handoff --to marcus` stored a `ResearchReport(source="merlin",
  rating=None, proposed_action=None)` and the Quant packet in a Marcus evidence store;
  `--to quant` produced the `merlin-quant-challenge/v1` packet.

## Round 1 (before fixes) — what failed and why

| Run | Result | Cause | Fix |
|---|---|---|---|
| C run 1 | FAIL | Headless permissions denied a compound `python -m merlin calc … \| tee` command; Merlin refused to compute by hand and reported INCOMPLETE (correct behaviour) | Allow `Bash(python -m merlin:*)`; prompt and spec require single plain commands |
| C run 1 | FAIL | Runner rejected the true claim "dcf-model (Anthropic financial-services skill)" (exact-name match) | Whole-name matching inside the claim |
| A/B run 1 | WARN, INCOMPLETE | No calculator (same Bash denial), no FSI skill invoked, web fetches refused by the proxy | Prompt names the FSI skill(s) per task type; caller evidence pack |
| local | crash | `python -m merlin calc` treated long inline JSON as a file path (`File name too long`) | Inline JSON parsed first |

Round-1 runs are not kept in the repo; their outcomes are summarised above.

## What these tests do not show

- That Merlin can fetch primary sources itself when run headless: in this container it could
  not. On the Grokbot host this is **UNVERIFIED** until Dex runs one assignment there.
- Any licensed-data workflow (FactSet, S&P, LSEG, Morningstar, Daloopa): none is connected.
- The Research Challenger review inside Marcus for a real instrument (needs Marcus's price feed,
  which this container cannot reach).
