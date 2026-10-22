"""
Build data/processed/airport_codes.csv: FAA code, IATA code and ICAO code for
the 402 Primary airports. The ICAO code is what the OpenSky API expects.

Source: OurAirports (data/raw/airports.csv.gz). Rows are matched on the IATA code
already used in airport_scores.csv (D9 mapping), falling back to the FAA code
(OurAirports `local_code`). ICAO = `icao_code`, or `gps_code` when empty.

Run from the repo root:
    python server/scripts/build_airport_codes.py
"""

import sys
from pathlib import Path

import pandas as pd

# Make `app` importable: this file is <repo>/server/scripts/build_airport_codes.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.scoring import REPO_ROOT, SCORES_CSV  # noqa: E402

OURAIRPORTS_CSV = REPO_ROOT / "data" / "raw" / "airports.csv.gz"  # committed; pandas decompresses
CODES_CSV = REPO_ROOT / "data" / "processed" / "airport_codes.csv"

# Codes that changed after the data period (as in D9): OurAirports already shows
# KDJT for West Palm Beach, but OpenSky still records its flights under KPBI
# (checked: KDJT -> HTTP 404, KPBI -> 160 departures on one day, Oct 2026).
ICAO_OVERRIDES = {"DJT": "KPBI"}


def _blank_to_na(s: pd.Series) -> pd.Series:
    return s.str.strip().replace("", pd.NA)


def load_ourairports() -> pd.DataFrame:
    oa = pd.read_csv(
        OURAIRPORTS_CSV,
        usecols=["type", "scheduled_service", "icao_code", "iata_code", "gps_code", "local_code"],
        dtype=str,
        keep_default_na=False,
    )
    for col in ("icao_code", "iata_code", "gps_code", "local_code"):
        oa[col] = _blank_to_na(oa[col])
    oa["icao"] = oa["icao_code"].fillna(oa["gps_code"])
    oa = oa[oa["icao"].notna() & (oa["type"] != "closed")]
    # If a code matches several rows, prefer the one with scheduled service.
    return oa.sort_values("scheduled_service", ascending=False)


def build_codes() -> pd.DataFrame:
    airports = pd.read_csv(SCORES_CSV, usecols=["code", "iata", "name", "state"])
    oa = load_ourairports()

    by_iata = oa.dropna(subset=["iata_code"]).drop_duplicates("iata_code").set_index("iata_code")["icao"]
    by_local = oa.dropna(subset=["local_code"]).drop_duplicates("local_code").set_index("local_code")["icao"]

    airports["icao"] = airports["iata"].map(by_iata).fillna(airports["code"].map(by_local))
    for code, icao in ICAO_OVERRIDES.items():
        airports.loc[airports["code"] == code, "icao"] = icao
    return airports


def main() -> None:
    airports = build_codes()
    airports[["code", "iata", "icao"]].to_csv(CODES_CSV, index=False)
    print(f"Saved {len(airports)} rows -> {CODES_CSV.relative_to(REPO_ROOT)}")

    missing = airports[airports["icao"].isna()]
    print(f"Airports with no ICAO code: {len(missing)}")
    if not missing.empty:
        print(missing[["code", "iata", "name", "state"]].to_string(index=False))

    dupes = airports[airports["icao"].notna() & airports["icao"].duplicated(keep=False)]
    print(f"ICAO codes shared by more than one airport: {len(dupes)}")
    if not dupes.empty:
        print(dupes.to_string(index=False))

    # ICAO prefix by region: K = contiguous US, P = Alaska/Hawaii/Pacific, T = Caribbean.
    print("\nICAO prefix counts:")
    print(airports["icao"].str[0].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
