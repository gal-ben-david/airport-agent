# Data Validation Log

Each check: what was tested, the result, and what was fixed (if anything).

## FAA enplanements
| Check | Result | Action |
|---|---|---|
| Summary rows in the Excel file | 6 non-airport rows (e.g., "Total Primary Airports") | Removed |
| Primary airport count vs FAA's own total | 402 = 402 | ✓ |
| Hub counts vs FAA Terminal Area Forecast [3] | Large 30 = 30, Medium 35 = 35, Small 79 vs 78 | ✓ (minor preliminary-data difference) |
| Correct year column detected per file | 2019 → "CY 19", 2025 → "CY 25" (not the prior-year column) | ✓ |
| Airports with no 2019 record | 11 flagged; one was a 4.26M-passenger Medium hub | Code change found (PBI → DJT), alias added (D9) |

## T-100 vs FAA (cross-source)
| Check | Result | Action |
|---|---|---|
| T-100 passengers vs FAA enplanements | Large gaps at international gateways (LAX −32%, SFO −29%, JFK −54%) | Revealed domestic-only table; switched to combined table (D10) |
| After switching | BOS, LAX, SFO, SNA, ANC all within 1% | ✓ |
| Row count consistency | 571,205 rows = sum of the four service classes | ✓ |
| Remaining large gaps | All in Nonhub airports (Alaska multi-stop routes, air tour operators) | Marked low confidence |
| Primary airports missing from T-100 | 14, almost all FAA vs IATA code mismatches (e.g., Mesa IWA / AZA) | Verified by passenger totals; mapping added (D9) |

## Code mapping (OurAirports)
| Check | Result | Action |
|---|---|---|
| Duplicate FAA codes in mapping | 0 | ✓ |
| Mapping vs 14 independently verified airports | 13 / 14 match | Manual override for DJT → PBI |
| Regression: airports that matched T-100 directly before mapping | 1 broken (Grand Canyon West, 1G4 → GCW) | Fallback rule: use mapped code only if it exists in T-100. Regression check now returns 0 |
| Final coverage | 401 / 402 airports have T-100 data (Ceiba, PR has none) | ✓ |

## Delay causes
| Check | Result | Action |
|---|---|---|
| Delay minutes by cause sum to total | Exact match (e.g., 2,003 = 2,003) | ✓ |
| Missing values | 11 of 22,687 rows (empty rows, or single cancelled flights) | Negligible; sums skip them |
| Coverage | 358 / 402 airports; missing ones are 43 Nonhub + 1 Small hub | Marked "no delay data", never treated as zero |
| Small hub without delay data | New Haven (HVN): ~88% of passengers on Avelo, ~12% on Breeze, carriers not included in on-time reporting | Genuine coverage gap, not a code mismatch. Congestion is unknown for HVN, not zero |

## Sanity checks against real-world knowledge
| Check | Result | Action |
|---|---|---|
| Long-haul share at LAX / SFO | First run showed 0%, impossible for international gateways | Found the notebook was still using the old domestic table; reloaded |
| Top 10 airports by raw CAGR | 7 of 10 are tiny Nonhub airports with <11,000 2019 passengers | Evidence for peer-group scoring (D3) |
| Final table | 402 rows, unique codes; metrics for 5 key airports match step-by-step computations | ✓ |