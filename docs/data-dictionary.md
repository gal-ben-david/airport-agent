# Data Dictionary — `airport_metrics`

**File:** `data/processed/airport_metrics.csv`
**Rows:** 402 — one per FAA Primary airport (S/L = "P", i.e., >10,000 annual enplanements), based on FAA CY 2025 preliminary data. (D2, D8)
**Columns:** 28
**Primary key:** `code` (FAA location identifier, unique per row)
**Data year:** calendar year 2025 for all metrics unless stated otherwise. (D11)

---

## Identity

| Column | Meaning | Unit / format | Source & period | How computed | NaN meaning | Decisions |
|--------|---------|---------------|-----------------|--------------|-------------|-----------|
| `code` | FAA location identifier (Locid) — the primary key for this table | Text, 2–4 chars (e.g., `BOS`, `IWA`, `1G4`) | FAA CY 2025 enplanements Excel | Taken directly from the `Locid` column; `PBI` is aliased to `DJT` (West Palm Beach code change, D9) | Never NaN | D9 |
| `iata` | Airline (IATA) code used to join T-100 and delay data | Text, 3 chars (e.g., `BOS`, `AZA`, `PBI`) | OurAirports mapping + manual overrides | `faa_to_iata.get(code, code)` with fallback: use mapped code if it appears in T-100; otherwise use the FAA code directly if it appears in T-100; otherwise use the mapped code. (D9) | Never NaN (falls back to FAA code if no mapping) | D9 |
| `state` | Two-letter US state or territory abbreviation | Text (e.g., `MA`, `PR`, `MP`) | FAA CY 2025 Excel, column `ST` | Direct copy | Never NaN | D1 |
| `city` | City associated with the airport | Text | FAA CY 2025 Excel, column `City` | Direct copy | Never NaN | — |
| `name` | Official airport name | Text | FAA CY 2025 Excel, column `Airport Name` | Direct copy | Never NaN | — |
| `hub` | FAA hub size category based on each airport's share of total US enplanements | Text: `L` (large, ≥1%), `M` (medium, 0.25–1%), `S` (small, 0.05–0.25%), `N` (nonhub, <0.05% but >10,000) | FAA CY 2025 Excel, column `Hub` | Direct copy — classification is computed and published by FAA, not recomputed here | Never NaN for Primary airports | D3 |

---

## Passenger demand (FAA)

All enplanement figures are "boardings" — each leg of a connection counts separately.

| Column | Meaning | Unit / format | Source & period | How computed | NaN meaning | Decisions |
|--------|---------|---------------|-----------------|--------------|-------------|-----------|
| `enpl_2025` | Annual enplanements for calendar year 2025 | Count (integer-valued float) | FAA CY 2025 preliminary enplanements Excel | Column `CY 25 Enplanements`, after dropping non-airport summary rows | Never NaN (eligibility criterion) | D4, D8 |
| `enpl_2024` | Annual enplanements for calendar year 2024 | Count (integer-valued float) | FAA CY 2025 Excel (also contains prior year), column `CY 24 Enplanements` | Direct copy | Never NaN for Primary airports | D11 |
| `enpl_2019` | Annual enplanements for calendar year 2019 (pre-pandemic baseline) | Count (integer) | FAA CY 2019 enplanements Excel | Left-joined on FAA code; loaded regardless of the airport's 2019 classification (may have been non-primary in 2019) | NaN for 10 airports with no 2019 record (new commercial service, code changes unresolved in 2019 file) | D5, D8 |
| `cagr_19_25` | Compound annual growth rate from 2019 to 2025 | Decimal fraction (e.g., `0.0026` = 0.26%/yr) | Derived | `(enpl_2025 / enpl_2019) ^ (1/6) − 1`, with N_YEARS = 6 | NaN when `enpl_2019` is NaN (see `growth_flag`) | D5 |
| `pct_change_24_25` | Percentage change in enplanements from 2024 to 2025 | Decimal fraction (e.g., `-0.0200` = −2.0%) | FAA CY 2025 Excel, column `% Change` | Direct copy — FAA computes `(CY25 − CY24) / CY24` | Never NaN for Primary airports | D5, D11 |
| `growth_flag` | Whether CAGR was computed or could not be computed | Text: `computed` (392 airports) or `insufficient_history` (10 airports) | Derived | `"computed"` when `enpl_2019` is not NaN; `"insufficient_history"` otherwise | Never NaN | D5, D8 |

---

## Flights & capacity (T-100)

All T-100 metrics are measured on the **departures (origin) side** — i.e., for flights *departing* from the airport. (D13)
The base dataset is BTS T-100 Segment All Carriers CY2025, filtered to rows where `DEPARTURES_PERFORMED > 0` and `ORIGIN ≠ DEST` (566,030 of 571,205 rows). (D10, D13, D14)
Long-haul threshold: `DISTANCE ≥ 3,000 miles` (configurable). (D15)

T-100 CLASS codes used (D14). Each class covers domestic and international flights:
- F = scheduled passenger
- L = non-scheduled (charter) passenger
- G = scheduled all-cargo
- P = non-scheduled all-cargo

| Column | Meaning | Unit / format | Source & period | How computed | NaN meaning | Decisions |
|--------|---------|---------------|-----------------|--------------|-------------|-----------|
| `departures_FL` | Total passenger-service flight departures (scheduled + charter) | Count (float) | T-100 Segment All Carriers, CY2025 | `sum(DEPARTURES_PERFORMED)` for CLASS F + L, by ORIGIN | NaN when `has_t100` is False (no T-100 data) | D13, D14 |
| `passengers_FL` | Total passengers carried on departing passenger-service flights | Count (float) | T-100 Segment All Carriers, CY2025 | `sum(PASSENGERS)` for CLASS F + L, by ORIGIN | NaN when `has_t100` is False | D13, D14 |
| `tot_departures_all` | Total flight departures across all service classes (passenger + cargo) | Count (float) | T-100 Segment All Carriers, CY2025 | `sum(DEPARTURES_PERFORMED)` for CLASS F + L + G + P, by ORIGIN | NaN when `has_t100` is False | D13, D14 |
| `load_factor` | Passenger load factor on scheduled passenger service | Fraction 0–1 (e.g., `0.817` = 81.7%) | T-100 Segment All Carriers, CY2025 | `sum(PASSENGERS) / sum(SEATS)` for CLASS F only, by ORIGIN | NaN when no CLASS F service (no T-100, or airport has only charter / all-cargo service) | D13, D14 |
| `long_haul_share_flights` | Share of scheduled departures that are long-haul (≥3,000 mi) | Fraction 0–1 | T-100 Segment All Carriers, CY2025 | `sum(DEPARTURES_PERFORMED where DISTANCE ≥ 3000) / sum(DEPARTURES_PERFORMED)` for CLASS F only, by ORIGIN | 0.0 when `has_t100` is True but no long-haul flights; NaN when `has_t100` is False | D14, D15 |
| `long_haul_share_pax` | Share of scheduled passengers travelling on long-haul flights (≥3,000 mi) | Fraction 0–1 | T-100 Segment All Carriers, CY2025 | `sum(PASSENGERS where DISTANCE ≥ 3000) / sum(PASSENGERS)` for CLASS F only, by ORIGIN | 0.0 when `has_t100` is True but no long-haul flights; NaN when `has_t100` is False | D14, D15 |
| `long_haul_share_all_flights` | Share of total departures (all classes) that are long-haul (≥3,000 mi) | Fraction 0–1 | T-100 Segment All Carriers, CY2025 | `sum(DEPARTURES_PERFORMED where DISTANCE ≥ 3000) / sum(DEPARTURES_PERFORMED)` for CLASS F + L + G + P, by ORIGIN | 0.0 when `has_t100` is True but no long-haul flights; NaN when `has_t100` is False | D14, D15 |
| `cargo_departure_share` | Share of total departures operated by all-cargo service (scheduled + non-scheduled) | Fraction 0–1 | T-100 Segment All Carriers, CY2025 | `sum(DEPARTURES_PERFORMED, CLASS G + P) / sum(DEPARTURES_PERFORMED, all classes)`, by ORIGIN | 0.0 when `has_t100` is True but no all-cargo service; NaN when `has_t100` is False | D13, D14 |

---

## Congestion (delays)

Delay metrics are measured on the **arrivals side** — per BTS OT_DelayCause1 convention. (D12)
The pipeline sums all carriers and all months first, then computes the rate from the totals. (D12)
Coverage: 358 of 402 airports; missing ones are 43 Nonhub and 1 Small hub (New Haven, HVN — its carriers, Avelo and Breeze, are not included in BTS on-time reporting).

| Column | Meaning | Unit / format | Source & period | How computed | NaN meaning | Decisions |
|--------|---------|---------------|-----------------|--------------|-------------|-----------|
| `arr_flights` | Total arriving flights reported, CY2025 | Count (float) | BTS OT_DelayCause1, CY2025 | `sum(arr_flights)` across all carriers and months, by airport (IATA code) | NaN when `has_delays` is False | D12 |
| `delay_rate` | Fraction of arriving flights delayed 15+ minutes (any cause) | Fraction 0–1 | BTS OT_DelayCause1, CY2025 | `sum(arr_del15) / sum(arr_flights)` | NaN when `has_delays` is False — never treated as zero | D12 |
| `nas_delay_rate` | Fraction of arriving flights with a National Aviation System delay | Fraction 0–1 | BTS OT_DelayCause1, CY2025 | `sum(nas_ct) / sum(arr_flights)` where `nas_ct` is the carrier-reported fractional count of NAS-attributed delayed flights | NaN when `has_delays` is False — never treated as zero | D12 |
| `cancel_rate` | Fraction of scheduled arriving flights that were cancelled | Fraction 0–1 | BTS OT_DelayCause1, CY2025 | `sum(arr_cancelled) / sum(arr_flights)` | NaN when `has_delays` is False — never treated as zero | D12 |

---

## Data quality flags

| Column | Meaning | Unit / format | Source & period | How computed | NaN meaning | Decisions |
|--------|---------|---------------|-----------------|--------------|-------------|-----------|
| `has_t100` | Whether the airport was matched to at least one T-100 origin record | Boolean (`True` / `False`) | Derived | `True` if `iata` appears in the T-100 ORIGIN set (all classes). False for 1 airport: Ceiba, PR (RVR / NRR) | Never NaN | D9, D10 |
| `t100_vs_faa_diff` | Relative gap between T-100 CLASS F+L passengers (departures side, IATA-mapped) and FAA enplanements | Fraction 0–1 (e.g., `0.006` = T-100 is 0.6% above FAA) | Derived from T-100 + FAA | `(passengers_FL − enpl_2025) / enpl_2025` using IATA-mapped join. NaN when `has_t100` is False, or when the airport has T-100 data but no CLASS F+L service (e.g. all-cargo only). | NaN for 1 airport where `has_t100` is False (Ceiba), plus any airports with no CLASS F+L flights in T-100 | D9 |
| `data_confidence` | Quality of the FAA↔T-100 cross-validation | Text: `high`, `low`, or `no_t100` | Derived | `"no_t100"` only when `has_t100` is False (1 airport: Ceiba); `"high"` when `abs(t100_vs_faa_diff) ≤ 0.10`; `"low"` otherwise (gap > 10% or `t100_vs_faa_diff` is NaN despite having T-100). All `low` airports are Nonhub. | Never NaN | D9 |
| `has_delays` | Whether the airport appears in BTS on-time delay data | Boolean (`True` / `False`) | Derived | `True` if `iata` appears in the delay aggregation. `False` for 44 airports (43 Nonhub + 1 Small hub) | Never NaN | D12 |

---

## Notes

### T-100 columns (departures-side, all carriers) vs. delay columns (arrivals-side, reporting carriers only)

**T-100 columns** (`departures_FL`, `passengers_FL`, `tot_departures_all`, `load_factor`, all four `_share_` columns, `t100_vs_faa_diff`) are measured on the **departures (origin) side** and cover **all carriers** — domestic and international — that file T-100 segment reports, including foreign carriers on US routes. Period: full calendar year 2025.

**Delay columns** (`arr_flights`, `delay_rate`, `nas_delay_rate`, `cancel_rate`) are measured on the **arrivals side** and cover only **domestic scheduled flights operated by reporting carriers** (US carriers operating at least 1% of total domestic scheduled traffic). International flights and smaller carriers are excluded from the delay data.

Consequences:
- `arr_flights` and `departures_FL` count different universes of flights at the same airport. `arr_flights` will typically be smaller than `departures_FL` because it excludes international and non-reporting-carrier flights.
- An airport's delay metrics may understate true congestion if a significant share of its traffic is international or operated by smaller carriers outside the reporting threshold.
- New Haven (HVN) has `has_delays = False` because its two primary carriers — Avelo and Breeze — are not included in BTS on-time reporting. Its congestion is unknown, not zero.

### `load_factor` can be NaN even when `has_t100 = True`

`load_factor` requires CLASS F (scheduled passenger) data. An airport served only by charter or all-cargo operations (CLASS L / G / P but not F) would have `has_t100 = True` but `load_factor = NaN`. Grand Canyon West (1G4) is a near-example: its T-100 traffic is mostly air-tour charter (CLASS L: 667 of 764 departures; only 97 CLASS F), so `passengers_FL` is far below its FAA enplanements and `data_confidence = "low"`.
