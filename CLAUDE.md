# Airport Investment Intelligence Agent

## Project goal
An AI agent that helps analysts find US airports where terminal expansion investment is
most promising, based on passenger and flight growth.
Requirements: public data, deterministic scoring (not only LLM output), a chat interface,
clear explanations, and stated assumptions and uncertainty.

## Status
The system is complete: data pipeline (build_data.py), deterministic scoring, a Claude
agent with 6 tools (including live OpenSky), Streamlit UI, pytest suite (26 tests),
29-case eval suite, runtime grounding guardrail, README and design doc.
Final eval run: 29/29 passed, every answer grounded, 2 guardrail retries.

## Key principles
- All numbers come from deterministic code (tools, `*_display` values). The LLM never
  computes, rounds or invents numbers; it understands the question, picks tools and explains.
- A grounding check runs on every answer: every number must appear in that turn's tool
  results, with one correction retry (D28). Same check in the evals.
- Scores are percentiles within FAA hub groups, so they are comparable only within a
  group. Regional answers are grouped by hub size, never one "best" airport across groups (D20).
- "Why" questions are answered with the scoring logic: drivers, peer comparison,
  components. Real-world causes only as a short closing limit (D30).
- Every design decision is recorded in docs/decisions.md.
- Clarity and reasoning over completeness and polish.

## Data facts
- FAA CY2025 enplanements are FINAL (published September 24, 2026), not preliminary.
  The label is in the page header of the xlsx file, not in the cells.
- Data year 2025 for all scored metrics (2026 is partial, Jan–Aug; D11).
  Growth = CAGR 2019 → 2025 (70%) + 2024 → 2025 change (30%), each ranked within hub (D5, D16).
- Scope: 402 FAA Primary airports (>10,000 enplanements). Hubs: 30 L, 35 M, 79 S, 258 N.
  395 ranked, 7 unranked (fewer than 3 of 4 real components, D18).
- T-100: combined domestic + international, departures side (D10, D13). 401/402 have
  T-100 data (Ceiba, PR has none). Delay data (domestic, large carriers): 358/402.
- Data confidence (T-100 vs FAA within 10%): 383 high, 18 low, 1 no_t100.
- FAA → IATA mapping via OurAirports + override DJT → PBI + fallback rule (D9).
  15 airports had no T-100 match by FAA code: 14 code mismatches (now mapped) + Ceiba.
- Raw CSVs are committed as .csv.gz (uncompressed .csv is gitignored); all code reads
  the .gz directly. Raw files downloaded October 20, 2026.

## Where things are
server/app/core/scoring.py          scoring model (weights, percentiles, sensitivity)
server/app/agent/agent.py           agent loop (claude-sonnet-5-5, max 6 tool calls, grounding retry)
server/app/agent/prompts.py         system prompt (rules reference decision IDs)
server/app/agent/grounding.py       number grounding check (runtime + evals)
server/app/agent/questions.py       the 4 assignment questions + follow-up
server/app/agent/tools/             find_airport, get_airport_profile, rank_airports,
                                    compare_airports, long_haul_share, get_recent_traffic (OpenSky)
server/evals/                       cases.py (29 cases), run_evals.py, fixtures.py
server/scripts/                     build_data.py, build_airport_codes.py, check_tools.py,
                                    check_live.py, try_agent.py
server/tests/                       pytest suite
ui/streamlit_app.py                 thin chat UI
data/raw/                           source files (.csv.gz, FAA .xlsx)
data/processed/                     airport_metrics.csv, airport_scores.csv, airport_codes.csv
data/cache/opensky/                 live API cache (gitignored)
notebooks/                          explore.ipynb (data), scoring_review.ipynb (sensitivity, baselines)

## How to run (from the repo root unless noted)
- App: `streamlit run ui/streamlit_app.py` (needs ANTHROPIC_API_KEY in .env;
  OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET optional)
- Tests: `cd server && pytest`
- Evals (Claude API, ~4–5 min): `cd server && python -m evals.run_evals [case_ids]`.
  Any run, even a subset, overwrites docs/eval-results.md. Checks are phrase-based:
  read every failure before changing the agent; a correct answer can fail on wording.
- Rebuild data: `python server/scripts/build_data.py` (compare only) or `--write`
  (overwrites data/processed/ only if all files match). Needs requirements-dev.txt (openpyxl).
- Manual checks: `python server/scripts/check_tools.py`, `check_live.py` (uses OpenSky
  credits), `try_agent.py` (uses the Claude API).

## Docs
- docs/decisions.md: decision log, D1–D30
- docs/validation.md: data validation log
- docs/data-dictionary.md: meaning and units of every metric
- docs/design.md: architecture, scoring, guardrails, evaluation, tradeoffs
- docs/eval-results.md: latest eval run (generated)
- README.md: overview, quick start, testing, project structure
