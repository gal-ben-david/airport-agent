Scope limited to commercial service airports (scheduled passenger service), since the investment thesis depends on airline passenger demand. General aviation airports excluded.

All 19 low-confidence airports are Nonhub; every Large, Medium and Small hub airport matches FAA within 10%

OurAirports is community-maintained and provides no accuracy guarantee. Its FAA-to-IATA mapping was therefore validated against 14 airports independently confirmed by matching FAA and T-100 passenger totals.

/*
“All Commercial Service Airports (by Rank)”

Only airports with scheduled passenger airline service, a few hundred airports.
Ranked by passenger volume.
Includes hub size (Large / Medium / Small / Nonhub).
Usually also includes the previous year’s passengers and the % change. That’s useful, because it’s already a growth number.

“All Airports (Primary, Non-primary, and General Aviation) by State”

Everything above, plus thousands of small General Aviation airports: private planes, flight schools, small airfields.
Most of those have zero or near-zero airline passengers.
The client invests in terminal expansion driven by passenger and flight growth. General Aviation airports have no airline passengers and no passenger terminals to expand, so they’re not investment candidates. Including them would only add thousands of rows of noise you’d need to filter out anyway.
*/

# Decisions Log

## Scope and data sources 
### D1. Region filtering by state (ST), not FAA region (RO)
**Decision:** Geographic questions (e.g., "New England") are answered by filtering on the state column (ST), using an explicit state list (New England = CT, ME, MA, NH, RI, VT).

**Why:**
- Regions in user questions are geographic and commonly understood, while FAA regions (RO) are administrative divisions of the FAA. They don't always match how an investor thinks about a market (e.g., the FAA Western-Pacific region groups CA, NV, AZ and HI together).
- An explicit state list is transparent and easy to explain to the user.

**Tradeoff / note:** The FAA New England region (RO = "NE") matches the six New England states, so RO is used only as a cross-check for this case.

### D2. Only Primary airports are scored
**Decision:** Airports are eligible for scoring only if the FAA classifies them as Primary (S/L = "P"), meaning more than 10,000 annual enplanements. Non-primary commercial service airports (2,500–10,000 enplanements) are excluded.

**Why:**
- The investment thesis depends on airline passenger demand. Airports with very low traffic are not realistic candidates for terminal expansion.
- Very small airports produce misleading growth numbers (e.g., +200% from a tiny base), which would distort the ranking.

**Tradeoff:** A small airport with real growth potential could be missed. Acceptable for this scope, and the agent states the threshold when answering.

### D7. Historical data is preloaded into a local SQLite database; live APIs only for real-time data
**Decision:** Historical sources (FAA enplanements, BTS T-100, OurAirports) are ingested ahead of time into a SQLite database file (`data/airports.db`) with precomputed metrics. Live public APIs (e.g., OpenSky) are called only for recent activity, with caching and fallback to the last stored result.

**Why:**
- These sources update annually or monthly, so querying them live on every question adds latency and failure points without adding freshness.
- Preloading makes the scoring fast, reproducible and deterministic.
- SQLite is a single file with no server to install, so the project runs anywhere with Python.

**Delivery:** The repository includes the raw source files (`data/raw/`), the build script (`build_db.py`), and the prebuilt database. Reviewers can run the agent immediately, or rebuild the database from raw data with one command to verify the pipeline.

**Tradeoff:** Data is only as fresh as the last ingest run. The agent states the data period in its answers. In production, a scheduled ETL job would refresh the data.

### D10. T-100 source: combined domestic + international segment table
**Decision:** Use BTS "T-100 Segment (All Carriers)", which combines domestic and international segments reported by both US and foreign carriers.

**Why:** Cross-source validation against FAA enplanements showed that the domestic-only table understated passengers at international gateways by 20–55% (e.g., JFK −54%, LAX −32%, SFO −29%), while mostly domestic airports matched within ~2–3% (SNA, ANC). Load factor, long-haul share and capacity metrics require international flights.

**Limitation:** Foreign-to-foreign flights are excluded by BTS. Not relevant, since all scored airports are in the US.

### D11. Common analysis period: calendar year 2025
**Decision:** All scored metrics (FAA enplanements, T-100 capacity and load factor, BTS delays) use full calendar year 2025.

**Why:** Metrics combined in one score must describe the same period. 2025 is the most recent full year available in all sources. Partial-year 2026 data is biased by seasonality (it covers January–August only) and is not used for scoring.

**Possible extension:** 2026 year-to-date data could serve as a "recent trend" signal, compared only against the same months of 2025.

## D23. Primary government data, not published airport rankings
**Decision:** The analysis uses primary government data (FAA, BTS) rather than published airport rankings (J.D. Power, Wall Street Journal, The Points Guy, Skytrax, ACI).

**Why:**
- Those rankings measure passenger experience (satisfaction, service, convenience), while the investment question is about demand versus capacity.
- They typically cover only the largest ~20–30 airports, while this analysis scores 402.
- They publish final rankings, not raw data, so they cannot support deterministic, explainable scoring or cross-source validation.
- They are proprietary, with no public API and licensing restrictions for use in a product.
- Some are themselves partly built on BTS data, which this project uses directly.

**Possible extension:** Passenger satisfaction could complement the score for renovation decisions specifically: high demand pressure combined with poor terminal satisfaction suggests the largest renovation upside. This would require a source with broad coverage and a license that permits use.

## Data cleaning and joining 
### D4. FAA CY 2025 enplanement data is preliminary
**Decision:** Using FAA CY 2025 enplanements as the latest year, labeled as preliminary in both the agent's answers and the documentation.

**Why:**
- It is the most recent full-year data available. The FAA publishes preliminary figures first, and final figures may be revised.

**Impact:**
- Growth figures involving 2025 may change slightly once final data is published.
- The agent states the data year and its preliminary status when relevant.

### D5. Growth period: 2019 → 2025, excluding COVID years as baselines
**Decision:** Passenger growth is measured as the compound annual growth rate (CAGR) from 2019 (last full pre-pandemic year) to 2025 (latest available year). The 2024→2025 change is used as a secondary "recent momentum" signal. 2020–2021 are never used as a starting point for growth.

CAGR = (enplanements_2025 / enplanements_2019)^(1/6) − 1: the equivalent steady annual growth rate between the two years.

**Why not 2020–2021 as a baseline:**
- Passenger traffic collapsed during COVID. Growth measured from those years would show artificially huge numbers that reflect recovery, not real new demand.
- The same applies to 2022, when many airports were still recovering.

**Why 2019 is a good starting point:**
- It is the last "normal" year before the pandemic, a clean baseline.
- Comparing 2025 to 2019 answers the question the investor cares about: has this airport grown beyond its pre-pandemic level, or only recovered? An airport above its 2019 level shows genuine new demand.
- CAGR uses only the start and end years, so the COVID dip in between does not distort the result.

**Why not go further back (e.g., 2015):**
- Travel patterns changed after COVID (e.g., less business travel, more leisure, remote work). Older years describe a market that partly no longer exists.
- Airline networks change over time (mergers, new routes, airlines entering or leaving an airport). Data from 10 years ago is less relevant to an investment decision made today.
- A 6-year window is long enough to smooth out one-year noise, and recent enough to reflect current conditions.

**Tradeoffs:**
- CAGR depends only on two endpoints. An unusual 2019 or 2025 at a specific airport affects its growth figure.
- 2025 data is preliminary (see D4).
- An airport that is still recovering slowly may look weak even if its long-term outlook is good. The recent-momentum signal (2024→2025) partly addresses this.
- Airports without 2019 data (e.g., new commercial service) are flagged as "insufficient history" rather than scored on growth.

### D8. Eligibility and peer group are based on the latest year (2025)
**Decision:** An airport is scored if it is Primary in 2025, and its hub group is its 2025 hub size. Its 2019 enplanements are used for growth regardless of its 2019 classification. Airports with no 2019 data are kept and flagged as "insufficient history" rather than excluded.

**Why:** The investor cares about airports as they are today. Filtering by classification in both years would wrongly exclude airports that grew into Primary status, which are exactly the high-growth candidates the client is looking for.

### D9. Airport identifiers: FAA codes vs. airline (IATA) codes
**Problem:** FAA enplanement data uses FAA location identifiers, while T-100 and BTS delay data use airline (IATA) codes. For most airports they are identical, but not always (e.g., Mesa: FAA IWA / IATA AZA; Kalispell: FAA GPI / IATA FCA). Codes can also change over time (West Palm Beach: PBI in 2019 FAA data, DJT in 2025).

**How found:** Cross-validation flagged 14 Primary airports with no T-100 match, including Mesa (1.0M passengers) and Kalispell (0.56M). All but one were code mismatches, not missing airports.

**Decision:**
- FAA codes are mapped to IATA codes using OurAirports [6][7], which lists both identifiers (`local_code`, `iata_code`) for each airport.
- A manual override handles codes that changed after the data period: DJT → PBI (OurAirports already shows the new code DJT, while 2025 T-100 still uses PBI).
- Fallback rule: the mapped code is used only if it exists in T-100; otherwise the FAA code is kept. This prevents the mapping from breaking airports that already matched (e.g., Grand Canyon West, 1G4).

**Validation:**
- The mapping reproduced 13 of 14 independently verified airports; the exception is the DJT/PBI code change, covered by the override.
- After mapping, the mapped airports match FAA within 1% on the combined T-100 table (e.g., Mesa 0.3%, Kalispell 0.1%, West Palm Beach 0.3%). Saipan matches within 9%.
- 401 of 402 Primary airports have T-100 data. Only Ceiba, PR has none.

**Supporting source:** BTS recommends its numeric Airport ID for analysis across years, because airport codes can change and be reused.

**Production note:** OurAirports is community-maintained without accuracy guarantees, which is why the mapping is validated against T-100 passenger totals rather than trusted directly.

## Metrics 
### D12. Congestion is measured by NAS-attributed delays
**Decision:** The congestion KPI is the NAS delay rate: arrivals delayed 15+ minutes due to the National Aviation System, divided by total arrivals (full year 2025, summed over all carriers and months before dividing).

**Why:**
- Overall delay rate mixes causes unrelated to airport capacity (airline operations, late-arriving aircraft).
- NAS delays reflect airport operations, heavy traffic volume and air traffic control, the closest available signal to airport congestion.

**Limitations:**
- NAS also includes non-extreme weather (e.g., fog at SFO), so it is not a pure traffic measure.
- NAS delays mainly reflect runway and airspace constraints. Terminal expansion alone may not resolve them, but they indicate demand pressure.
- Arrivals only, and only carriers that report on-time data. 364 of 402 Primary airports have delay data. Airports without data are marked "no delay data", never treated as zero.
- Airports with regulatory traffic caps may show low delays despite being constrained, since excess demand is never scheduled.

### D13. Airport metrics are measured on departures
**Decision:** All T-100 airport metrics (passengers, seats, load factor, long-haul share, cargo share) are computed from departing flights (ORIGIN = airport).

**Why:** FAA enplanements count passengers boarding at an airport, which is the departing side. Measuring T-100 the same way keeps all metrics on one definition, and is what allowed the T-100 vs FAA validation to match within 1% (e.g., SFO: 26.48M vs 26.25M). Total flight activity ("operations" = takeoffs + landings) is approximately twice the departures.

### D14. Flight classes used per metric
**Decision:**
| Metric | T-100 service classes |
|---|---|
| Load factor | F (scheduled passenger) |
| Passenger volume | F + L (scheduled + charter passenger) |
| Long-haul share | F, with all-class share reported as context |
| Total flight activity | F + L + G + P |
| Cargo share | G + P (all-cargo) |

**Why:**
- The client invests in passenger terminals, so passenger metrics drive the score.
- Load factor uses scheduled service only, because charter aircraft are sold as a whole regardless of how many seats are filled.
- Cargo flights still use runways and airspace, so total activity and cargo share are reported as context. At Anchorage, cargo is 56.5% of departures, so excluding it without stating so would misrepresent the airport.

**Note:** This is an analytical assumption, not stated in the assignment. The agent states which flights are included when answering.

### D15. Long-haul definition: 3,000+ miles, with sensitivity and cargo context
**Decision:** A flight is long haul if its segment distance is 3,000 miles or more (`LONG_HAUL_MILES = 3000`, configurable). The share is computed on scheduled passenger departures (CLASS F), by flights and by passengers. The all-flights share (including cargo) is reported as context.

**Why:**
- There is no single official definition of long haul. 3,000 miles (roughly 6+ hours of flight) separates domestic transcontinental flights (e.g., Boston–Los Angeles, ~2,600 miles) from intercontinental ones.
- The threshold is a parameter, so it can be changed and its effect tested.

**Sensitivity (Anchorage, scheduled passenger departures):**
| Threshold | Long-haul share |
|---|---|
| 2,500 miles | 10.1% |
| 3,000 miles | 2.8% |
| 4,000 miles | 0.2% |

Including cargo flights, Anchorage's long-haul share at 3,000 miles is 31.1%, since cargo is 56.5% of its departures.

**Implication:** The answer depends heavily on two definitions (distance threshold, and whether cargo is included). The agent always states both, and mentions sensitivity when the airport has many routes near the threshold.

**Other airports (3,000 miles, flights / passengers):** SFO 13.3% / 22.5%, LAX 12.3% / 21.2%, BOS 7.3% / 13.7%, SNA 0% / 0%.

## Scoring 
### D3. Scoring within peer groups (FAA hub size)
**Decision:** Airports are normalized and compared within their FAA hub size group, based on each airport's share of total US enplanements.

**FAA airport categories** (source: FAA Airport Categories [[1]](#references)):

| Category | Hub value in data | Annual enplanements | Scored? |
|---|---|---|---|
| Large hub (Primary) | L | ≥ 1% of US total | Yes |
| Medium hub (Primary) | M | 0.25% – 1% of US total | Yes |
| Small hub (Primary) | S | 0.05% – 0.25% of US total | Yes |
| Nonhub (Primary) | N | < 0.05% of US total, but > 10,000 | Yes |
| Non-primary commercial service | (empty) | 2,500 – 10,000 | No (see D2) |

Hub size thresholds are statutory (49 USC 47102), as published by the FAA. Hub classification is recalculated annually based on each airport's share of US enplanements.

**Why:**
- Comparing a small regional airport directly to LAX isn't meaningful. Large airports would always win on size alone.
- Within a peer group, the question becomes meaningful: "which medium airport is most congested and growing fastest?"
- Hub size is an official, published FAA classification, not a threshold I invented.

**Validation:** The loaded CY 2025 data contains 30 large and 35 medium hubs, matching the FAA's 2025 Terminal Area Forecast summary [3]. Small hubs show 79 vs. 78 in the forecast summary, a minor difference expected between preliminary and later counts.

**Tradeoff:** An investor may also care about absolute size, since a large airport means a larger investment. This is handled by a separate Scale component in the score.

### D6. Score nationally, then filter by region
**Decision:** Scores are computed across all US Primary airports (within hub-size peer groups), and only then filtered to the region the user asks about.

**Why:** Regional subsets are too small for fair peer comparison. For example, Boston is the only Large hub in New England. Scoring nationally means each airport is compared to its true national peers, so a "strong candidate" means strong by national standards, not just the best of a small local group.

## D16. Score components: demand growth meets capacity limits
**Investment thesis:** Terminal renovation is most profitable where passenger demand is growing and the airport is already under pressure.

**Components (all oriented so that higher = stronger investment case):**
| Component | Metric | Meaning |
|---|---|---|
| Growth | 70% CAGR 2019→2025 + 30% change 2024→2025 | Long-term trend plus recent momentum |
| Seat demand | Load factor (CLASS F) | How full flights are relative to seats offered |
| System congestion | NAS delay rate | How strained the airport and airspace system is |
| Scale | Enplanements 2025 | Size of the passenger base; mostly a tie-breaker within a hub group |

**Context only (reported by the agent, not scored):**
- Long-haul share: describes the network type, not capacity pressure. Including it would systematically favor international gateways. *Possible extension:* long-haul passengers may bring higher revenue per passenger (retail, larger facilities), which could matter for a revenue-focused investor.
- Cargo share: cargo flights do not use passenger terminals (D14). This is a scope assumption; it would change if the client invested in cargo facilities.
- Overall delay rate and cancellation rate: mixed causes, often airline-driven (D12).

## D17. Normalization: percentile rank within hub group
**Decision:** Each component is converted to a percentile rank (0–1) within the airport's FAA hub group (D3), computed nationally (D6).

**Why:**
- Puts metrics in different units on one scale.
- Robust to outliers (e.g., Vero Beach's 60% annual growth does not stretch the scale for other airports).
- Easy to explain: "0.85 on growth = grew faster than 85% of peers."

**Tradeoff:** Percentiles keep the order but lose magnitude: a small lead and a large lead can produce similar percentiles.

## D18. Missing data: neutral value plus completeness flag
**Decision:** A missing component (e.g., no delay data, no 2019 history) receives the neutral value 0.5, the peer-group median. A column `score_completeness` records how many of the four components used real data.

**Why:**
- Treating missing data as zero would wrongly reward or penalize an airport.
- Reweighting the available components would amplify them. For example, New Haven (no delay data, very high growth) would have its growth count more.
- A neutral value means the airport is neither rewarded nor penalized for missing data.

**Data confidence** (T-100 vs FAA agreement) does not change the score. It is a flag the agent reports.

**Minimum completeness:** An airport is ranked only if at least 3 of its 4 score components use real data (`score_completeness` ≥ 3). The 7 airports with only 1 or 2 real components are not ranked. The agent reports them as "insufficient data to score" and shows the metrics that are available.

## D19. Weights: 35 / 25 / 25 / 15, with sensitivity check
**Decision:** Growth 35%, seat demand 25%, system congestion 25%, scale 15%. Weights are a configurable parameter.

**Why:**
- Growth is the core of the investment thesis.
- Seat demand and congestion are two independent signals of pressure (full flights vs. a strained system), weighted equally.
- Scale is mostly a tie-breaker, since hub groups already compare airports of similar size.

**Robustness:** Weights are a judgment call. A sensitivity check (each weight ±10 points) tests whether the top-ranked airports change.

**Sensitivity results:** Across 8 scenarios (each weight ±0.10, others rescaled proportionally), the within-hub ranking is highly stable: Spearman ≥ 0.97 for every hub group, and at least 3 of the baseline top 5 remain in the top 5. The top 3 of each hub never change; turnover occurs only at positions 4–5, between airports with near-identical scores (e.g., ORD/EWR). SFO ranks above LAX in every scenario. Airports with one extreme component are the most weight-sensitive: HVN (growth percentile 1.0, weak seat demand and scale) ranges from 23rd to 42nd of 79 Small hubs, so the agent notes this sensitivity when reporting it.

**Limitation:** The test covers moderate changes (±0.10). An investor with a very different view (e.g., growth at 60%) could get a different ranking. Weights are configurable for that reason.

**Weight-sensitivity flag:** Each ranked airport stores its rank range across the 8 scenarios (`rank_range_sensitivity`). An airport is flagged `weight_sensitive` when its range exceeds 20% of its hub group (`SENSITIVE_RANGE_SHARE = 0.20`), i.e., more than one quintile, meaning it could move to a different tier of its peer group. A fixed threshold (e.g., 10 places) was rejected because hub groups differ in size (30 to 251 airports): it flagged 87% of Nonhubs and no Large hubs. With the relative rule, about 11% of airports are flagged, across all hub groups (e.g., HVN: 23rd–42nd of 79). The agent mentions the flag when reporting a flagged airport.
A flag does not always indicate an extreme profile: an airport in a crowded middle of its group (e.g., Omaha) can also move many places with small score changes.

## D20. Regional answers are grouped by hub size
**Decision:** When a question covers airports of different sizes (e.g., "New England"), the agent groups results by hub size and reports each airport's national rank within its group (e.g., "Boston: 9th of 30 large hubs").

**Why:** Scores are percentiles within hub groups (D3, D17), so they are not comparable across groups. Sorting a mixed region by score would place a strong small airport above a good large one. For example, Bangor Bangor (6th of 251 ranked Nonhubs, score 0.85) would appear ahead of Boston (9th of 30 Large hubs, score 0.66), and Martha's Vineyard (68th of 251 ranked Nonhubs) ahead of Portland, ME (25th of 79 Small hubs). That ordering is meaningless for an investor.

**How found:** Reviewing the first New England results.

## D21. Validation against simple rankings
**Question:** Does the four-component score add anything over simply ranking by size or by growth?

**Method:** Within each hub group, our ranking was compared with two simple rankings: by 2025 enplanements (size only) and by 2019→2025 CAGR (growth only).

**Results:**
- Correlation with size-only is low in Medium and Small hubs (Spearman −0.07, 0.08) and moderate in Large and Nonhub (0.57, 0.66). Correlation with growth-only is 0.49–0.78: growth matters most, as intended, but it is not the whole score.
- Size alone would favor large airports that are shrinking: LAX is 5th of 30 by size but 21st by our score (passengers −2.7%/yr since 2019).
- Growth alone would favor tiny airports with large percentage jumps: four of the growth-only top 5 Nonhubs have 10,000–25,000 passengers and 54–69% load factors; our score ranks them 85th–109th.
- Our score finds balanced airports that neither rule finds: Knoxville (TYS) is 15th of 79 Small hubs by both size and growth, but 1st by our score, being strong on all four components.

**Known limitation:** A delay rate based on very few reported arrivals is unreliable. Vero Beach's 25.5% NAS delay rate rests on 42 reported arrivals (vs. 1,372 departures in T-100), which gives it the top congestion score among Nonhubs. With more time: treat the delay rate as missing (neutral, D18) below a minimum number of reported arrivals.

## Agent behavior

### D24. Agent loop: stateless, tool-grounded, with a tool budget
**Decision:**
- `run_agent(messages)` is stateless: each request carries the plain chat history. Earlier tool results are not carried forward, so follow-up questions fetch numbers again through tools.
- Each user turn is limited to 6 tool calls. After the limit, the model must answer with the information it already has.
- Tool errors (unknown airport, invalid input) are returned to the model as error results, so it can explain them to the user instead of failing.
- Every tool call, with its inputs and results, is returned alongside the answer, for evaluation and auditing.

**Why:**
- Statelessness lets any server instance handle any request, which is what allows horizontal scaling, and fits a web front end (Streamlit now, React or other clients later).
- Re-fetching is cheap: tools read a small precomputed table (D7). The main cost is LLM tokens, not tool computation.
- Re-fetching keeps every number in every answer traceable to a tool result.
- The tool budget prevents runaway loops and bounds cost and latency per question.

**Production options:** cache tool results by input (the data changes rarely), use prompt caching for the fixed system prompt and tool definitions, store conversations server-side (e.g., Redis) and trim or summarize long histories.


## References
- [FAA – Airport Categories](https://www.faa.gov/airports/planning_capacity/categories)
- [FAA – Passenger Boarding (Enplanement) Data](https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger)
- [FAA – Terminal Area Forecast Summary FY2025–FY2055](https://taf.faa.gov/Downloads/TAFSummaryFY2025-FY2055.pdf)
5. [BTS TranStats – T-100 Segment (All Carriers)](https://www.transtats.bts.gov/DataIndex.asp)
7. [OurAirports – Data Repository (GitHub)](https://github.com/davidmegginson/ourairports-data)
