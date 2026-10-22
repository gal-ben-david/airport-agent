# Airport Investment Intelligence Agent

An AI agent that helps investment analysts find US airports where terminal expansion is most promising, based on passenger growth and pressure on existing capacity.

Ask questions in plain English, for example:
- *Which airports in New England are strong candidates for terminal expansion?*
- *Compare LA and Santa Ana airport congestion levels.*
- *What is the percentage of long haul flights out of Anchorage airport?*
- *What is the unmet flight demand in SFO airport and why?*

The agent answers with numbers from public government data, explains its reasoning, and states its assumptions and uncertainty. **Every number in an answer comes from deterministic code, never from the language model**, and a runtime check verifies this on every answer.

![Screenshot](docs/screenshot.png) <!-- TODO: add a screenshot of an answer with "Tools used" open -->

---

## How it works (in one paragraph)

Public data (FAA passenger counts, BTS flight and delay data) is processed offline into a table of metrics for the 402 FAA Primary airports. A deterministic scoring model ranks each airport against airports of similar size. A Claude-based agent answers questions by calling read-only tools over this data, plus one live tool that calls the OpenSky Network API for recent flight activity. A grounding guardrail checks that every number in the final answer appears in the tool results.

See **[docs/design.md](docs/design.md)** for the design, scoring methodology, tradeoffs and where AI is used.

---

## Quick start

Requirements: Python 3.13 (3.11+ should work).

```bash
# 1. Install
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt

# 2. Configure
cp .env.example .env
# edit .env and add your Anthropic API key:
# ANTHROPIC_API_KEY=sk-ant-...

# 3. Run the chat
streamlit run ui/streamlit_app.py
```

Then open http://localhost:8501 and click one of the example questions.

**Optional: live traffic (OpenSky).** Fill in `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` in `.env` (free account at [opensky-network.org](https://opensky-network.org/)). Without them, the agent works normally, and recent-traffic questions return "live data unavailable".

The processed data is included in the repository, so no data download is needed to run the agent.

---

## Testing

```bash
# Deterministic tests: tools, scoring, display values (no API calls)
pip install -r server/requirements-dev.txt
cd server && pytest

# End-to-end agent evaluation: 29 cases, uses the Claude API (~5 minutes)
python -m evals.run_evals   # from server/
```

Latest evaluation results: **[docs/eval-results.md](docs/eval-results.md)** (final run: 28/29 passed, every answer grounded; the one failure is a phrase check missing a correct answer, see D27).

Manual check scripts (print results for reading, not pass/fail):
- `server/scripts/check_tools.py`: runs every tool on sample airports.
- `server/scripts/check_live.py`: tests the OpenSky tool, cache and fallback (uses OpenSky API credits).
- `server/scripts/try_agent.py`: runs the four assignment questions and a follow-up through the agent.

---

## Rebuilding the data (optional)

```bash
pip install -r server/requirements-dev.txt   # includes openpyxl for the FAA files
python server/scripts/build_data.py          # rebuilds and compares with committed files
python server/scripts/build_data.py --write  # overwrites data/processed/ only if all match
```

---

## Project structure

```
├── server/
│   ├── app/
│   │   ├── core/scoring.py        # deterministic scoring model
│   │   └── agent/
│   │       ├── agent.py           # agent loop (Claude, tool use, tool budget)
│   │       ├── prompts.py         # system prompt
│   │       ├── grounding.py       # number grounding check (runtime + evals)
│   │       └── tools/             # read-only tools + live OpenSky tool
│   ├── evals/                     # evaluation suite (cases, runner, fixtures)
│   ├── scripts/                   # build_data.py (data rebuild) and manual check scripts
│   └── tests/                     # pytest suite
├── ui/streamlit_app.py            # thin chat UI
├── data/
│   ├── raw/                       # source files (compressed)
│   └── processed/                 # airport_metrics.csv, airport_scores.csv, airport_codes.csv
├── notebooks/                     # data exploration, validation and scoring review
└── docs/
    ├── design.md                  # design and architecture document
    ├── decisions.md               # decision log (D1–D29)
    ├── validation.md              # data validation log
    ├── data-dictionary.md         # meaning of every metric
    └── eval-results.md            # latest evaluation run
```

---

## Data sources

| Source | What it provides |
|---|---|
| FAA Passenger Boarding (Enplanement) Data, CY2019 / CY2024 / CY2025 (preliminary) | Passengers per airport, hub size |
| BTS T-100 Segment (All Carriers), 2025 | Flights, seats, passengers, distance, service class |
| BTS Airline On-Time Statistics and Delay Causes, 2025 | Delays by cause (NAS), cancellations |
| OurAirports | Mapping between FAA, IATA and ICAO airport codes |
| OpenSky Network API (live) | Recent arrivals and departures (context only, not scored) |

All data is public. Sources and validation are documented in [docs/validation.md](docs/validation.md).

---

## Main limitations

- Unmet demand is not measured directly; the agent uses proxies (load factor, congestion, growth) and says so.
- FAA 2025 passenger data is preliminary.
- Delay data covers domestic arrivals of large US carriers only.
- Live OpenSky data counts tracked flights, not passengers, and is never used in the score.
- This is data analysis, not financial advice.

More in [docs/design.md](docs/design.md#assumptions-and-limitations).
