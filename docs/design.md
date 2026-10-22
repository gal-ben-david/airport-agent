# Airport Investment Intelligence Agent: Design Document

This document explains how the agent works, how airports are scored, the key tradeoffs, and where AI is and isn't used. Detailed reasoning for each decision is in [decisions.md](decisions.md) (referenced as D1–D29), and data checks are in [validation.md](validation.md).

---

## 1. The problem

The client invests in airport modernization projects in the US and wants to know where terminal renovation would be most profitable, based on increased flight and passenger capacity.

This is framed as one question: **where is passenger demand growing, and already pressing against the airport's capacity?** An airport with rising demand and full, strained operations is where added capacity is most likely to be used and to pay off.

The agent is a proof of concept: analysts ask questions in plain English, and get answers grounded in public data, with the reasoning, assumptions and uncertainty made explicit.

---

## 2. Architecture

```
                    OFFLINE (data pipeline)                          ONLINE (agent)

  FAA enplanements ─┐                                      ┌───────────────────────────┐
  BTS T-100        ─┼─► clean, join, ─► airport_metrics ─► │ Scoring (deterministic)   │
  BTS delay causes ─┤   validate        (402 airports)     │ airport_scores.csv        │
  OurAirports      ─┘                                      └─────────────┬─────────────┘
                                                                         │ read-only
                                                                         ▼
  User ─► Streamlit chat ─► run_agent ─► Claude (tool use) ◄──► Tools: find_airport,
              ▲                │                                  get_airport_profile,
              │                ▼                                  rank_airports,
              │        Grounding check (code):                    compare_airports,
              │        every number in the answer                 long_haul_share,
              └─────── must appear in tool results                get_recent_traffic ──► OpenSky API
                       (one correction retry)                                            (live, cached)
```

**Key properties:**
- **Historical data is processed offline** into small tables (D7). At 402 airports, in-memory tables are faster and simpler than a database; a database is the production path. The processed data is rebuilt from the raw files by `server/scripts/build_data.py`, which verifies the result matches the committed files.
- **Scores are computed in code** and stored. The language model can describe them but cannot change them.
- **The agent is stateless** (D24): each request carries the chat history; numbers are re-fetched through tools on every turn. Any server instance can handle any request, which allows horizontal scaling.
- **The UI is thin** (D26): Streamlit only sends messages and displays answers, with a "Tools used" panel that shows every tool call. A FastAPI endpoint and a React client could replace it without changing the agent.

---

## 3. Data

| Source | Used for | Key limitation |
|---|---|---|
| FAA enplanements (2019, 2024, 2025) | Passenger volume, growth, hub size | 2025 is final (published September 24, 2026) |
| BTS T-100 Segment, All Carriers (2025) | Load factor, flights, long-haul share, cargo share | Segment counting (multi-stop routes count a passenger twice) |
| BTS delay causes (2025) | NAS delay rate (congestion) | Domestic arrivals of large US carriers only |
| OurAirports | FAA ↔ IATA ↔ ICAO code mapping | Community-maintained, so validated against T-100 |
| OpenSky Network API (live) | Recent arrivals and departures | ADS-B tracking, not passengers; context only |

**Airport codes:** the sources use three code systems. **FAA codes** (used in FAA data, e.g., IWA for Mesa), **IATA codes** (used by airlines and in BTS data, e.g., AZA), and **ICAO codes** (used in flight tracking such as OpenSky, e.g., KIWA). Usually they match (BOS, KBOS), but not always, so the sources are joined through a validated code mapping (D9).

**Scope:** the 402 FAA Primary airports (more than 10,000 passengers a year), all metrics for calendar year 2025 (D2, D11). Published airport rankings (J.D. Power, WSJ, Skytrax) were not used: they measure passenger experience, cover only the largest airports, and don't provide raw data (D23). The data year is 2025, not 2026: 2026 data covers only part of the year (to August), and a partial year is biased by seasonality (D11).

**Data as of:** FAA CY2025 enplanements (final, published September 24, 2026); BTS T-100 Segment and delay causes for calendar year 2025 (downloaded October 20, 2026); OurAirports (downloaded October 20, 2026); OpenSky live (last 7 full days at question time).

**Domestic vs. international:** passenger, capacity and long-haul metrics include international flights (combined T-100, D10); delay metrics cover domestic flights only.

**Why these years:** each metric uses only the years it needs. Growth is a trend, so it uses FAA passenger data for 2019 (the last year before COVID) and 2025, plus 2024 for recent momentum; the years in between are not needed, since CAGR depends only on its endpoints (D5). Load factor, congestion, long-haul and cargo shares describe the airport's current state, so they use 2025 only, the same year as the latest passenger data (D11). The one thing this leaves out is how airline capacity changed over time: comparing 2019 and 2025 seats would show whether demand is outpacing supply.

**Validation highlights** (full log in [validation.md](validation.md)):
- Cross-checking T-100 against FAA revealed that the first T-100 file was domestic-only (LAX −32%, JFK −54%). After switching to the combined table, the key airports matched within 1% (D10).
- 15 airports had no T-100 match by FAA code: 14 were code mismatches (now mapped with a validated code mapping, e.g., Mesa: FAA IWA, airline AZA; D9) and 1 (Ceiba, PR) has no T-100 data.
- Every airport gets a data-confidence flag from this cross-check (383 high, 18 low, 1 without T-100 data).

---

## 4. Scoring methodology

### Investment thesis and components (D16)

**The scoring is fully deterministic:** it is computed in code (server/app/core/scoring.py) from the data, with no language model involved. The same data always produces the same scores and ranks, which is verified by the test suite and reproduced exactly by build_data.py.

| Component | Weight | Metric | What it captures |
|---|---|---|---|
| **Growth** | 35% | 70% passenger CAGR 2019→2025 + 30% change 2024→2025 | Is demand rising? (long-term trend + recent momentum) |
| **Seat demand** | 25% | Load factor (passengers ÷ seats, scheduled flights) | Are flights already full? |
| **System congestion** | 25% | NAS delay rate (arrivals delayed 15+ min for system reasons) | Is the airport system strained? |
| **Scale** | 15% | 2025 enplanements | Is the passenger base large enough? (mostly a tie-breaker) |

Growth uses 2019 as the baseline, the last year before COVID, so recovery is not mistaken for new demand (D5).

**How growth is computed:** CAGR and the 2024–25 momentum are each ranked within the hub group, combined 70/30, and the blend is ranked again so it is on the same percentile scale as the other components.

**Context only, not scored:** long-haul share (describes the network, not capacity pressure), cargo share (cargo doesn't use passenger terminals), overall delay and cancellation rates (mixed causes, often airline-driven), and live OpenSky traffic.

### How the score is computed

1. **Peer groups (D3, D6):** each airport is compared only with airports of its FAA hub size (Large, Medium, Small, Nonhub), computed nationally. Without this, the largest airports would always win on size alone.
2. **Normalization (D17):** each metric becomes a percentile rank (0–1) within the hub group. "0.85 on growth" means "grew faster than 85% of peers". Percentiles are robust to outliers (one airport with 60% annual growth doesn't distort the scale).
3. **Missing data (D18):** a missing component (e.g., no delay data) gets the neutral value 0.5, so the airport is neither rewarded nor penalized. Airports with fewer than 3 of 4 real components are not ranked (7 of 402).
4. **Weighted sum:** score = 0.35 × growth + 0.25 × seat demand + 0.25 × congestion + 0.15 × scale, from 0 to 1.

### How robust is it?

- **Weight sensitivity (D19):** each weight was shifted ±0.10 (8 scenarios). The within-hub ranking stayed highly stable (Spearman ≥ 0.97 everywhere), and the top 3 of each hub group never changed. Airports whose rank moves across more than 20% of their group are flagged as weight-sensitive (e.g., New Haven: 23rd–42nd of 79), and the agent reports the range.
- **Compared with simple rankings (D21):** ranking by size alone would favor shrinking airports (LAX: 5th of 30 by size, 21st by our score, passengers −2.7%/yr since 2019). Ranking by growth alone would favor tiny airports with large percentage jumps. The score surfaces airports with growth and pressure together (e.g., Knoxville: 15th by size and by growth, 1st by our score).

### Presenting results (D20)

Scores are percentiles within hub groups, so they can't be compared across groups. Regional answers are grouped by hub size, with each airport's national rank within its group ("Boston: 9th of 30 large hubs"). The agent never names a single "best" airport across groups.

---

## 5. Where and how AI is used

The guiding rule: **code computes, the language model explains.**

| Task | Done by | Why |
|---|---|---|
| Cleaning, joining and validating data | Code, ahead of time (data pipeline, build_data.py) | Must be correct and reproducible |
| Computing metrics and scores | Code | Deterministic: same input, same output |
| Looking up, ranking, comparing airports | Code (tools) | Every number traceable to data |
| Formatting numbers ("82.3%") | Code: tools return ready-formatted values, which the model copies | The model rounded numbers incorrectly when it formatted them itself |
| Checking every number in the answer | Code (grounding check) | Catches errors the prompt alone didn't prevent |
| Understanding the question | **LLM** | Natural language: "LA", "New England", follow-ups |
| Choosing which tools to call | **LLM** | Plans the steps for each question |
| Explaining results and caveats | **LLM** | Turns numbers into a clear, honest answer |
| Following the conversation | **LLM** | Supports follow-up questions |

**Model:** Claude (claude-sonnet-5-5) with native tool use. The agent loop allows at most 6 tool calls per question; tool errors are returned to the model so it can explain them (D24).

**AI during development:** AI assistants were used in two roles: as a thinking partner (discussing approaches, explaining concepts, challenging decisions and reviewing results) and as a coding assistant (writing code from specified requirements). The decisions were made and documented by the author, and every output was verified against the data rather than trusted. Several AI-generated claims were caught this way: a conclusion that a 30–50% gap between two data sources was not a data problem, when it actually meant the file was domestic-only (D10); an explanation of missing airports that turned out to be code mismatches (D9); and a code change that broke an airport that previously worked, detected with a regression check.

---

## 6. Guardrails

| Guardrail | Where it's enforced | How it's tested |
|---|---|---|
| Every number comes from a tool; display values copied exactly | Tools + system prompt | Grounding check on every eval answer |
| Runtime grounding check with one correction retry (D28) | Agent loop (code) | Eval "Guardrail" column |
| No unsupported guesses or causal claims | System prompt | SFO and forecast cases |
| Input validation (unknown airports, invalid thresholds) | Tools (code) | Unknown-airport case |
| Ambiguous names: list candidates, state assumptions | Tools + system prompt | LA, Anchorage, Chicago cases |
| Tool budget: max 6 calls per question (D24) | Agent loop (code) | Design feature |
| Out-of-scope requests declined briefly, no tools (D25) | System prompt | Off-topic case |
| Prompt injection resisted (D29) | Architecture + system prompt | 7 injection cases (5 direct, 2 indirect), 2 false-memory cases |
| Not financial advice | System prompt | Financial-advice case |
| No cross-group comparisons (D20) | Tools + system prompt | New England, Bangor-vs-Boston cases |
| Uncertainty flags: missing data, weight sensitivity, low confidence, unranked | Tools + system prompt | HVN and unranked-airport cases |
| Live data kept out of the score (D22) | Tools + system prompt | "Does that change its ranking?" case |

**Code guardrails vs. prompt guardrails.** Rules in the system prompt guide the model, but a language model does not follow instructions with certainty: in evaluation runs, the agent occasionally rounded numbers or wrote longer refusals than instructed, even though the prompt forbade it. Guardrails enforced in code apply on every answer regardless of the model's behavior. Where a rule matters most, such as every number coming from the data, it is enforced in code, with the prompt as a first line of defense rather than the only one.

**Prompt injection (D29).** The main protection is architectural: scores live in the data and are computed in code; the tools are read-only, so a successful injection could at most produce a misleading text answer; and the grounding check flags numbers that don't come from the tools. Tested with direct attempts (change a score, reveal the system prompt, steer conclusions, supply false data, claim the agent said something earlier) and indirect ones (an instruction hidden in a pasted document, and a simulated compromised tool result). The agent resisted every attempt. **Known gap:** numbers that appear in the user's own message are exempt from the grounding check, so adopting a false user-supplied value is caught only by the prompt rule and the eval cases.

---

## 7. Evaluation

**Deterministic tests** (`pytest`): tools, scoring and display values, including the reproduction of published figures (e.g., the long-haul sensitivity table).

**Agent evaluation** (29 cases, D27): the four assignment questions and follow-ups, known risks (cross-group comparison, ambiguous city, forecast, unranked airport, custom region and threshold), live-data failures (API down, with and without cache), and prompt injection. Each case checks the tools called and the answer text; a grounding check verifies every number.

- **No LLM judge:** checks are deterministic, so the same answer always gets the same verdict, and each failure points to one rule.
- **Final runs:** three full runs with the final prompt: 26/29, 28/29 and 29/29 (the third after compare_airports began computing cross-group comparisons in code). In all three runs, every answer was grounded in tool results, and every factual, safety and prompt-injection check passed. The failures were phrasing rules the model occasionally breaks: a regional superlative without its hub group (D20, once), and the SFO answer placing its caveat in a sentence before the conclusion (D30, in the first two runs). In the first run, one more case failed because a check missed a correct wording (the unranked YNG answer); the check was widened. The model's wording is not deterministic, so these vary between runs. This is the main limit of prompt-based rules: the evals detect such slips, but only code-enforced guardrails, such as the grounding check, apply on every answer.
- Across evaluation runs, the guardrail corrected numbers in up to 5 of 29 first drafts per run, including rounded values (599.4 → "599") and a mis-copied digit (25,078,968 written as "24,078,968"), an error a reader would be unlikely to notice.
- **Checks were tested against deliberately wrong answers**, to confirm they fail when they should.
- **Limitation:** many checks look for specific words, so a correct answer worded differently can fail. Every failure is read before changing the agent.

---

## 8. Key tradeoffs

| Choice | Benefit | Cost |
|---|---|---|
| Peer groups by hub size | Fair comparisons; small airports not buried | Scores can't be compared across groups, so regional answers are grouped (D20) |
| Percentile normalization | Robust to outliers, easy to explain | Loses magnitude: a small lead and a large lead can look similar |
| Neutral value for missing data | Neither rewards nor penalizes gaps | An airport with missing data is assumed average on that component |
| CAGR from 2019 | Separates real growth from COVID recovery | Depends on two endpoints |
| NAS delays as congestion | Closest public signal of system strain | Mostly runway and airspace limits, which a terminal alone may not fix; includes non-extreme weather |
| Long haul and cargo as context only | Score stays focused on terminal demand | A revenue-focused investor might value long-haul traffic |
| Precomputed data, live API only for context | Fast, reproducible, deterministic | Data is only as fresh as the last pipeline run |
| Re-fetching numbers on every turn | Every number traceable; stateless and scalable | A few extra tool calls per follow-up |
| Deterministic evals, no LLM judge | Reproducible, explainable verdicts | Can't judge explanation quality; phrase checks are brittle |
| Streamlit UI | Working chat quickly; focus on data and guardrails | Limited design control |

---

## 9. Assumptions and limitations

- **Unmet demand is not measured directly.** The agent uses proxies (load factor, congestion, growth) and says so.
- **Long haul = 3,000+ miles** (no single official definition). The answer can be very sensitive to it (Anchorage: 10.1% at 2,500 miles, 2.8% at 3,000, 0.2% at 4,000), so the agent shows all three. Including cargo, Anchorage's long-haul share is 31.1%, since cargo is 56.5% of its departures (D15).
- **Delay data** covers domestic arrivals of large US carriers only. Airports served mainly by smaller airlines may have no delay data (e.g., New Haven), and very small samples can be unreliable (e.g., Vero Beach: 42 reported arrivals).
- **Airports with regulatory caps** on flights may show low delays despite being constrained, since excess demand is never scheduled.
- **The tools don't include delay breakdowns by cause** other than NAS, although the source data has them.
- **Live OpenSky data** counts tracked flights (not passengers), may have coverage gaps, and compares one recent week with a full-year average: the baseline is 2025 average departures per day from T-100 (all departures / 365). It is context only. Its free tier is for non-commercial use.

---

## 10. Path to production

### Scale
- **Deployment:** containerize the app (Docker) for consistent deployment.
- **Stateless agent:** each request carries its own conversation history (D24), so the agent can run as many instances behind a load balancer, with no shared state between them.
- **Conversation storage:** server-side (e.g., Redis), keyed by session, with long conversations trimmed or summarized.
- **Data:** a database (e.g., PostgreSQL) loaded by a scheduled ETL job, instead of in-memory tables.
- **Cost and latency:** tool results cached by input (the data changes rarely); prompt caching for the fixed system prompt and tool definitions.
- **Protection:** rate limiting per user, and authentication.

### Redundancy and reliability
- **Live API:** the OpenSky tool already has a timeout, a retry, a cache and a stale-data fallback (D22). In production, the cache would be shared between instances (e.g., Redis).
- **LLM provider:** a fallback model or provider if the primary API is unavailable.
- **Graceful degradation:** every tool already returns an error message instead of failing, and the agent explains it to the user (D24).
- **Data refresh:** versioned data loads, with the validation checks of build_data.py run automatically, so a bad source file never replaces good data.

### Monitoring
- Log every tool call, grounding result and correction retry.
- Track the grounding retry rate and the eval pass rate over time, as quality signals.
- Run the eval suite automatically on every change to the prompt, tools or model.

### Interface and licensing
- A FastAPI endpoint and a React client; for end users, friendly source notes instead of raw tool JSON.
- A commercial OpenSky license.

## 11. What I would do with more time

- **Extreme-weather delay rate:** add it as a context metric from the BTS weather cause category, so weather questions can be answered with data.
- **Minimum sample size for delay rates:** treat a delay rate as missing below a minimum number of reported arrivals (e.g., Vero Beach: 42 arrivals).
- **Supply vs. demand growth:** add 2019 T-100 data, to compare seat capacity growth with passenger growth, a direct signal of demand outpacing supply.
- **Stricter grounding:** exempt only user numbers that aren't presented as airport metrics (D29).
- **Explanation quality:** an LLM-as-judge rubric, as a complement to the deterministic checks.
- **Consistency:** run the eval suite several times and report the pass rate per case.
- **Passenger satisfaction:** if a broadly licensed source exists, combine it with the score: high demand pressure plus poor terminal satisfaction suggests the largest renovation upside.
- **Voice:** add voice input and output, e.g., browser speech-to-text for questions and text-to-speech for answers. The agent is UI-independent (D26), so this would only change the interface layer.

---

## 12. The assignment questions, answered

Short summaries of the agent's answers (2025 data; full answers in [eval-results.md](eval-results.md)):

- **New England candidates:** grouped by hub size. Large: Boston, 9th of 30 (high congestion, flat growth). Medium: Bradley, 15th of 35 (very full flights). Small: Portland, ME, 25th of 79 (balanced growth and full flights); New Haven has the fastest growth of all small hubs but no delay data and a weight-sensitive rank (23rd–42nd). Nonhub: Bangor, 6th of 251.
- **LA vs. Santa Ana congestion:** "LA" is taken as LAX (stated as an assumption). NAS delay rates are close: 4.6% at LAX vs. 4.3% at SNA (LAX higher by 0.3 points). Neither is highly congested within its own hub group. They are in different hub groups, so only raw rates are compared.
- **Anchorage long-haul share:** 2.8% of scheduled passenger departures (3,000+ miles), 6.2% of passengers, and 31.1% of all departures including cargo. The answer is sensitive to the threshold.
- **SFO unmet demand:** unmet demand isn't measured directly, but the indicators point to strong demand pressure. The drivers: flights fuller than 90.0% of large hubs (load factor 82.3%) and more congested than 86.7% of them (NAS delay rate 9.9%). What holds the score back is growth: passengers are still below 2019 (CAGR −0.9%), though recovering (+4.7% in 2025). SFO ranks 7th of 30 large hubs (stable at 6th–8th).
