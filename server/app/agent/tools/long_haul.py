"""Long-haul share tool (D15), with lazy loading of the raw T-100 file."""

import math
from functools import lru_cache

import pandas as pd

from app.core.scoring import REPO_ROOT

from .common import LONG_HAUL_MILES, _clean, _error, _identity, _lookup_code, _ok

T100_CSV = REPO_ROOT / "data" / "raw" / "T_T100_SEGMENT_ALL_CARRIER.csv"

MIN_THRESHOLD_MILES = 100
MAX_THRESHOLD_MILES = 10_000


@lru_cache(maxsize=1)
def _t100() -> pd.DataFrame:
    """Departing segments, filtered exactly as in the metrics pipeline (D13)."""
    t = pd.read_csv(
        T100_CSV,
        usecols=["DEPARTURES_PERFORMED", "PASSENGERS", "DISTANCE", "ORIGIN", "DEST", "CLASS"],
    )
    return t[(t["DEPARTURES_PERFORMED"] > 0) & (t["ORIGIN"] != t["DEST"])]


def long_haul_share(code: str, threshold_miles=LONG_HAUL_MILES, include_cargo: bool = False) -> dict:
    """Share of departures that are long haul (DISTANCE >= threshold), D15.

    Stored values are used for the default 3,000 miles; other thresholds are
    computed from the raw T-100 file with the same filters as the pipeline.
    """
    if isinstance(threshold_miles, bool) or not isinstance(threshold_miles, (int, float)) \
            or not math.isfinite(threshold_miles) \
            or not MIN_THRESHOLD_MILES <= threshold_miles <= MAX_THRESHOLD_MILES:
        return _error(
            f"threshold_miles must be a number between {MIN_THRESHOLD_MILES} and "
            f"{MAX_THRESHOLD_MILES:,}; got {threshold_miles!r}."
        )
    if not isinstance(include_cargo, bool):
        return _error("include_cargo must be true or false.")
    row = _lookup_code(code)
    if row is None:
        return _error(f"Unknown airport code '{code}'. Use find_airport to resolve names or cities.")

    definition = {
        "threshold_miles": threshold_miles,
        "rule": f"segment DISTANCE >= {threshold_miles:,} miles",
        "side": "departing flights (ORIGIN = airport) (D13)",
        "share_flights": "scheduled passenger departures only (CLASS F) (D14)",
        "share_pax": "passengers on scheduled passenger departures (CLASS F) (D14)",
        "share_all_flights": "all departures incl. charter and all-cargo (CLASS F+L+G+P)",
        "headline": "share_all_flights (cargo included)" if include_cargo else "share_flights (passenger, CLASS F)",
    }
    flags = []

    if not row["has_t100"]:
        shares = {"share_flights": None, "share_pax": None, "share_all_flights": None}
        flags.append("no T-100 flight data for this airport: long-haul share unavailable (D9).")
        source = "none"
    elif threshold_miles == LONG_HAUL_MILES:
        shares = {
            "share_flights": _clean(row["long_haul_share_flights"]),
            "share_pax": _clean(row["long_haul_share_pax"]),
            "share_all_flights": _clean(row["long_haul_share_all_flights"]),
        }
        source = "stored (airport_scores.csv)"
    else:
        shares = _compute_long_haul(row["iata"], threshold_miles)
        source = "computed from T-100 Segment All Carriers, CY2025"

    if row["has_t100"] and shares["share_flights"] is None:
        flags.append("no scheduled passenger (CLASS F) departures in T-100: passenger shares unavailable.")

    cargo = _clean(row["cargo_departure_share"])
    if cargo is not None and cargo >= 0.25:
        flags.append(
            f"all-cargo flights are {cargo:.1%} of departures, so including cargo changes "
            "the long-haul share substantially; state which definition is used (D15)."
        )
    if threshold_miles != LONG_HAUL_MILES:
        flags.append(f"non-default threshold; the scoring documentation uses {LONG_HAUL_MILES:,} miles (D15).")

    headline = shares["share_all_flights"] if include_cargo else shares["share_flights"]
    return _ok(
        airport=_identity(row),
        units={"shares": "fraction 0-1", "threshold_miles": "statute miles"},
        definition=definition,
        include_cargo=include_cargo,
        long_haul_share=headline,
        **shares,
        cargo_departure_share=cargo,
        source=source,
        flags=flags,
    )


def _compute_long_haul(iata: str, threshold_miles: float) -> dict:
    t = _t100()
    dep = t[t["ORIGIN"] == iata]
    f = dep[dep["CLASS"] == "F"]
    long_f = f[f["DISTANCE"] >= threshold_miles]
    long_all = dep[dep["DISTANCE"] >= threshold_miles]

    def share(part, whole):
        return _clean(part / whole) if whole > 0 else None

    return {
        "share_flights": share(long_f["DEPARTURES_PERFORMED"].sum(), f["DEPARTURES_PERFORMED"].sum()),
        "share_pax": share(long_f["PASSENGERS"].sum(), f["PASSENGERS"].sum()),
        "share_all_flights": share(long_all["DEPARTURES_PERFORMED"].sum(), dep["DEPARTURES_PERFORMED"].sum()),
    }
