"""
Rebuild all processed data from data/raw/ in one command:
airport_metrics.csv, airport_scores.csv and airport_codes.csv.

The logic is moved from notebooks/explore.ipynb without changes (same filters,
definitions and no rounding); scoring and the ICAO codes are imported from
app/core/scoring.py and build_airport_codes.py. Outputs are written to a
temporary folder and compared with the committed files in data/processed/.

Run from the repo root:
    python server/scripts/build_data.py           # build and compare only
    python server/scripts/build_data.py --write   # also overwrite data/processed/ if every file matches
"""

import argparse
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# Make `app` and the sibling script importable: this file is <repo>/server/scripts/build_data.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.scoring import REPO_ROOT, add_rank_sensitivity, compute_scores, load_metrics  # noqa: E402
from build_airport_codes import build_codes  # noqa: E402

RAW = REPO_ROOT / "data" / "raw"
PROCESSED = REPO_ROOT / "data" / "processed"

FAA_XLSX = RAW / "arp-cy{year}-commercial-service-enplanements.xlsx"
T100_CSV = RAW / "T_T100_SEGMENT_ALL_CARRIER.csv.gz"
DELAY_CSV = RAW / "Airline_Delay_Cause.csv.gz"
OURAIRPORTS_CSV = RAW / "airports.csv.gz"

OUTPUT_FILES = ["airport_metrics.csv", "airport_scores.csv", "airport_codes.csv"]

KEEP_COLS = ["Locid", "ST", "City", "Airport Name", "S/L", "Hub"]
CODE_ALIASES = {"PBI": "DJT"}        # D9: old FAA code -> current FAA code
N_YEARS = 6                          # D5: CAGR 2019 -> 2025
LONG_HAUL_MILES = 3000               # D15
CONFIDENCE_THRESHOLD = 0.10          # T-100 vs FAA gap for "high" confidence

# D9: OurAirports mapping, validated against airports confirmed by passenger totals
US_REGIONS = ["US", "PR", "GU", "MP", "VI", "AS"]
VERIFIED_IATA = {
    "IWA": "AZA", "GPI": "FCA", "DJT": "PBI", "JQF": "USA", "UNV": "SCE",
    "HXD": "HHH", "CRQ": "CLD", "SAW": "MQT", "JKA": "GUF", "TNI": "TIQ",
    "AKW": "KLW", "FHR": "FRD", "NYL": "YUM", "GSN": "SPN",
}
MANUAL_OVERRIDES = {"DJT": "PBI"}    # T-100 2025 still reports West Palm Beach as PBI

FLOAT_RTOL = 1e-9


def check(label: str, value, expected=None) -> None:
    """Print a validation number, with ✓/✗ when there is an expected value."""
    mark = "" if expected is None else ("  ✓" if value == expected else f"  ✗ expected {expected}")
    print(f"  {label}: {value}{mark}")


# ---------------------------------------------------------------------------
# 1. FAA enplanements and CAGR (explore.ipynb cells 7, 10, 12, 14)
# ---------------------------------------------------------------------------

def read_faa(year: int) -> pd.DataFrame:
    """FAA file for one year, without summary/subtotal rows (Rank = NaN)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = pd.read_excel(FAA_XLSX.with_name(FAA_XLSX.name.format(year=year)))
    return raw[raw["Rank"].notna()].copy()


def load_year(year: int) -> pd.DataFrame:
    raw = read_faa(year)
    enpl_col = next(c for c in raw.columns if "Enplanements" in c and str(year)[-2:] in c)
    print(f"  {year}: detected enplanement column -> '{enpl_col}'")
    raw = raw[KEEP_COLS + [enpl_col]].rename(columns={enpl_col: "enplanements"})
    raw["year"] = year
    return raw


def build_growth() -> tuple[pd.DataFrame, pd.DataFrame]:
    """CAGR table for 2025 Primary airports (sorted by CAGR) and the 2025 FAA table."""
    long = pd.concat([load_year(2019), load_year(2025)], ignore_index=True)
    long = long.rename(columns={"Locid": "code", "ST": "state", "City": "city",
                                "Airport Name": "name", "S/L": "service_level", "Hub": "hub"})
    long["code"] = long["code"].replace(CODE_ALIASES)

    # D8: eligibility = Primary in 2025; 2019 enplanements regardless of 2019 status
    eligible_codes = set(long.loc[(long["year"] == 2025) & (long["service_level"] == "P"), "code"])
    base = long[(long["year"] == 2025) & (long["code"].isin(eligible_codes))].copy()
    base = base.rename(columns={"enplanements": "enpl_2025"})
    enpl_2019 = (
        long.loc[(long["year"] == 2019) & (long["code"].isin(eligible_codes)),
                 ["code", "enplanements"]]
        .rename(columns={"enplanements": "enpl_2019"})
    )

    cagr_df = base.merge(enpl_2019, on="code", how="left")
    has_2019 = cagr_df["enpl_2019"].notna()
    cagr_df["cagr_19_25"] = (cagr_df["enpl_2025"] / cagr_df["enpl_2019"]) ** (1 / N_YEARS) - 1
    cagr_df["growth_flag"] = "computed"
    cagr_df.loc[~has_2019, "growth_flag"] = "insufficient_history"
    cagr_df = cagr_df.sort_values("cagr_19_25", ascending=False).reset_index(drop=True)

    check("Primary airports in 2025", len(cagr_df), 402)
    check("with 2019 data", int(has_2019.sum()))
    check("without 2019 data, after the PBI -> DJT alias (insufficient_history)", int((~has_2019).sum()), 10)

    # 2024 enplanements and % change, from the 2025 file
    faa = read_faa(2025).rename(columns={
        "Rank": "rank", "RO": "faa_region", "ST": "state", "Locid": "code",
        "City": "city", "Airport Name": "name", "S/L": "service_level",
        "Hub": "hub", "CY 25 Enplanements": "enpl_2025",
        "CY 24 Enplanements": "enpl_2024", "% Change": "pct_change_24_25",
    })
    return cagr_df, faa


# ---------------------------------------------------------------------------
# 2. T-100 metrics (explore.ipynb cells 43, 50; D13 departures, D14 classes)
# ---------------------------------------------------------------------------

def build_t100(t100: pd.DataFrame) -> pd.DataFrame:
    """Per-origin T-100 metrics. Sums first, then ratios (never average rates)."""
    t = t100[(t100["DEPARTURES_PERFORMED"] > 0) & (t100["ORIGIN"] != t100["DEST"])].copy()
    check("T-100 rows before -> after filter (departures > 0, origin != dest)", f"{len(t100):,} -> {len(t):,}")

    fl_rows = t[t["CLASS"].isin(["F", "L"])]            # scheduled + charter pax
    f_rows = t[t["CLASS"] == "F"]                        # scheduled pax only
    lh_f = f_rows[f_rows["DISTANCE"] >= LONG_HAUL_MILES]
    lh_all = t[t["DISTANCE"] >= LONG_HAUL_MILES]
    cargo = t[t["CLASS"].isin(["G", "P"])]               # all-cargo

    fl_agg = fl_rows.groupby("ORIGIN")[["DEPARTURES_PERFORMED", "PASSENGERS"]].sum()
    fl_agg.columns = ["departures_FL", "passengers_FL"]
    f_agg = f_rows.groupby("ORIGIN")[["DEPARTURES_PERFORMED", "SEATS", "PASSENGERS"]].sum()
    f_agg.columns = ["_dep_F", "_seats_F", "_pax_F"]

    t100_m = (
        fl_agg
        .join(f_agg, how="outer")
        .join(t.groupby("ORIGIN")["DEPARTURES_PERFORMED"].sum().rename("tot_departures_all"), how="outer")
        .join(cargo.groupby("ORIGIN")["DEPARTURES_PERFORMED"].sum().rename("_cargo_dep"), how="outer")
        .join(lh_f.groupby("ORIGIN")["DEPARTURES_PERFORMED"].sum().rename("_lh_dep_F"), how="outer")
        .join(lh_f.groupby("ORIGIN")["PASSENGERS"].sum().rename("_lh_pax_F"), how="outer")
        .join(lh_all.groupby("ORIGIN")["DEPARTURES_PERFORMED"].sum().rename("_lh_dep_all"), how="outer")
        .reset_index()
        .rename(columns={"ORIGIN": "iata"})
    )
    t100_m["load_factor"] = t100_m["_pax_F"] / t100_m["_seats_F"]
    t100_m["long_haul_share_flights"] = t100_m["_lh_dep_F"] / t100_m["_dep_F"]
    t100_m["long_haul_share_pax"] = t100_m["_lh_pax_F"] / t100_m["_pax_F"]
    t100_m["long_haul_share_all_flights"] = t100_m["_lh_dep_all"] / t100_m["tot_departures_all"]
    t100_m["cargo_departure_share"] = t100_m["_cargo_dep"] / t100_m["tot_departures_all"]
    t100_m = t100_m.drop(columns=["_dep_F", "_seats_F", "_pax_F",
                                  "_cargo_dep", "_lh_dep_F", "_lh_pax_F", "_lh_dep_all"])
    check("T-100 origin airports", t100_m["iata"].nunique())
    return t100_m


# ---------------------------------------------------------------------------
# 3. Delay metrics (explore.ipynb cell 51; D12)
# ---------------------------------------------------------------------------

def build_delays() -> pd.DataFrame:
    """Per-airport delay rates: sum carriers and months first, then divide."""
    delays = pd.read_csv(DELAY_CSV)
    delay_m = delays.groupby("airport")[["arr_flights", "arr_del15", "nas_ct", "arr_cancelled"]].sum()
    delay_m["delay_rate"] = delay_m["arr_del15"] / delay_m["arr_flights"]
    delay_m["nas_delay_rate"] = delay_m["nas_ct"] / delay_m["arr_flights"]
    delay_m["cancel_rate"] = delay_m["arr_cancelled"] / delay_m["arr_flights"]
    delay_m = (
        delay_m[["arr_flights", "delay_rate", "nas_delay_rate", "cancel_rate"]]
        .reset_index()
        .rename(columns={"airport": "iata"})
    )
    check("delay file rows", f"{len(delays):,}")
    check("airports in delay file", delay_m["iata"].nunique())
    return delay_m


# ---------------------------------------------------------------------------
# 4. FAA -> IATA mapping (explore.ipynb cells 23, 35, 37, 38; D9)
# ---------------------------------------------------------------------------

def build_faa_to_iata(cagr_df: pd.DataFrame, t100: pd.DataFrame) -> dict:
    """OurAirports FAA (local_code) -> IATA mapping, plus manual overrides."""
    # Before mapping: Primary airports with no T-100 (F+L) match by FAA code
    fl_origins = set(t100.loc[t100["CLASS"].isin(["F", "L"]), "ORIGIN"])
    check("Primary airports with no T-100 match by FAA code, before mapping (14 code mismatches + RVR)",
          int((~cagr_df["code"].isin(fl_origins)).sum()), 15)

    oa = pd.read_csv(OURAIRPORTS_CSV)
    codes = oa[
        oa["iso_country"].isin(US_REGIONS)
        & oa["local_code"].notna()
        & oa["iata_code"].notna()
    ][["local_code", "iata_code", "name", "iso_country", "type"]]
    check("duplicate FAA codes in OurAirports mapping", int(codes["local_code"].duplicated().sum()), 0)

    faa_to_iata = dict(zip(codes["local_code"], codes["iata_code"]))
    matches = sum(faa_to_iata.get(faa) == iata for faa, iata in VERIFIED_IATA.items())
    check("verified airports reproduced by OurAirports (DJT/PBI handled by override)",
          f"{matches}/{len(VERIFIED_IATA)}", "13/14")
    faa_to_iata.update(MANUAL_OVERRIDES)
    return faa_to_iata


# ---------------------------------------------------------------------------
# 5-6. Cross-validation, data confidence and airport_metrics (explore.ipynb cell 52)
# ---------------------------------------------------------------------------

def build_metrics(cagr_df, faa, t100_m, delay_m, faa_to_iata) -> pd.DataFrame:
    am = cagr_df[["code", "state", "city", "name", "hub",
                  "enpl_2025", "enpl_2019", "cagr_19_25", "growth_flag"]].copy()

    # D9 fallback: mapped IATA if in T-100, else the FAA code if in T-100, else mapped IATA
    t100_origins = set(t100_m["iata"])

    def map_iata(faa_code):
        mapped = faa_to_iata.get(faa_code, faa_code)
        if mapped in t100_origins:
            return mapped
        if faa_code in t100_origins:
            return faa_code
        return mapped

    am["iata"] = am["code"].map(map_iata)
    direct_match = am["code"].isin(t100_origins)
    reg_fail = am.loc[direct_match & ~am["iata"].isin(t100_origins)]
    check("regression: direct T-100 matches lost by the mapping", len(reg_fail), 0)

    am = am.merge(faa[["code", "enpl_2024", "pct_change_24_25"]], on="code", how="left")
    am = am.merge(t100_m, on="iata", how="left")
    am["has_t100"] = am["iata"].isin(t100_origins)

    # T-100 F+L passengers vs FAA enplanements (fraction of FAA)
    am["t100_vs_faa_diff"] = np.where(
        am["has_t100"],
        (am["passengers_FL"] - am["enpl_2025"]) / am["enpl_2025"],
        np.nan,
    )
    am["data_confidence"] = "no_t100"
    am.loc[am["has_t100"] & (am["t100_vs_faa_diff"].abs() <= CONFIDENCE_THRESHOLD), "data_confidence"] = "high"
    am.loc[am["has_t100"] & (am["t100_vs_faa_diff"].abs() > CONFIDENCE_THRESHOLD), "data_confidence"] = "low"
    am.loc[am["has_t100"] & am["t100_vs_faa_diff"].isna(), "data_confidence"] = "low"

    # 0 = no long-haul / cargo departures; NaN only when the airport has no T-100 data
    for col in ["long_haul_share_flights", "long_haul_share_pax",
                "long_haul_share_all_flights", "cargo_departure_share"]:
        am.loc[am["has_t100"] & am[col].isna(), col] = 0.0

    am = am.merge(delay_m, on="iata", how="left")
    am["has_delays"] = am["iata"].isin(set(delay_m["iata"]))

    conf = am["data_confidence"].value_counts()
    check("airports with T-100 data", int(am["has_t100"].sum()), 401)
    check("without T-100", ", ".join(am.loc[~am["has_t100"], "code"]), "RVR")
    check("data confidence high / low / no_t100",
          f"{conf.get('high', 0)} / {conf.get('low', 0)} / {conf.get('no_t100', 0)}", "383 / 18 / 1")
    check("airports with delay data", int(am["has_delays"].sum()), 358)
    check("airport_metrics shape", am.shape, (402, 28))
    return am


# ---------------------------------------------------------------------------
# 9. Compare generated files with the committed ones
# ---------------------------------------------------------------------------

def compare(name: str, new_path: Path, old_path: Path) -> bool:
    """Same rows, same columns, values equal (floats within FLOAT_RTOL)."""
    new, old = pd.read_csv(new_path), pd.read_csv(old_path)
    problems = []
    if list(new.columns) != list(old.columns):
        problems.append(f"columns differ: only new {sorted(set(new.columns) - set(old.columns))}, "
                        f"only committed {sorted(set(old.columns) - set(new.columns))}")
    if len(new) != len(old):
        problems.append(f"row count {len(new)} vs {len(old)}")
    if set(new["code"]) != set(old["code"]):
        problems.append("different airport codes")
    if problems:
        print(f"  ✗ {name}: " + "; ".join(problems))
        return False

    same_order = list(new["code"]) == list(old["code"])
    new, old = new.set_index("code"), old.set_index("code").loc[new["code"]]
    worst = {}
    for col in new.columns:
        a, b = new[col], old[col]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b) \
                and not pd.api.types.is_bool_dtype(a):
            equal = np.isclose(a, b, rtol=FLOAT_RTOL, atol=0, equal_nan=True)
            diff = (a - b).abs().max()
            if diff > 0:
                worst[col] = diff
        else:
            equal = (a.astype(str) == b.astype(str)) | (a.isna() & b.isna())
        if not equal.all():
            problems.append(f"{col}: {int((~equal).sum())} values differ")

    mark = "✓" if not problems else "✗"
    print(f"  {mark} {name}: {len(new)} rows x {len(new.columns) + 1} columns, "
          f"row order {'identical' if same_order else 'different'}, "
          f"{'all values equal' if not problems else '; '.join(problems)}")
    for col, diff in worst.items():
        print(f"      largest float difference in {col}: {diff:.3g}")
    return not problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true",
                        help="overwrite data/processed/ if every generated file matches")
    args = parser.parse_args()

    out = Path(tempfile.mkdtemp(prefix="airport_build_"))
    print(f"Building into {out}\n")

    print("1. FAA enplanements and CAGR")
    cagr_df, faa = build_growth()

    print("\n2. T-100 metrics")
    t100 = pd.read_csv(T100_CSV)
    t100_m = build_t100(t100)

    print("\n3. Delay metrics")
    delay_m = build_delays()

    print("\n4. FAA -> IATA mapping")
    faa_to_iata = build_faa_to_iata(cagr_df, t100)

    print("\n5-6. Cross-validation, data confidence, airport_metrics")
    metrics = build_metrics(cagr_df, faa, t100_m, delay_m, faa_to_iata)
    metrics.to_csv(out / "airport_metrics.csv", index=False)

    print("\n7. Scores (app/core/scoring.py)")
    metrics = load_metrics(out / "airport_metrics.csv")  # as scoring.py reads it
    scores = add_rank_sensitivity(compute_scores(metrics), metrics)
    scores.to_csv(out / "airport_scores.csv", index=False)
    check("ranked / unranked", f"{int(scores['ranked'].sum())} / {int((~scores['ranked']).sum())}", "395 / 7")
    check("weight-sensitive airports", int(scores["weight_sensitive"].sum()))

    print("\n8. ICAO codes (build_airport_codes.py)")
    codes = build_codes(out / "airport_scores.csv")
    codes[["code", "iata", "icao"]].to_csv(out / "airport_codes.csv", index=False)
    check("airports with no ICAO code", int(codes["icao"].isna().sum()), 0)

    print("\n9. Comparison with data/processed/")
    all_match = all([compare(name, out / name, PROCESSED / name) for name in OUTPUT_FILES])

    print()
    if not all_match:
        print("Generated files differ from data/processed/: nothing written.")
        sys.exit(1)
    if args.write:
        for name in OUTPUT_FILES:
            shutil.copyfile(out / name, PROCESSED / name)
        print(f"All files match. Wrote {', '.join(OUTPUT_FILES)} to data/processed/.")
    else:
        print("All files match. Run with --write to overwrite data/processed/.")


if __name__ == "__main__":
    main()
