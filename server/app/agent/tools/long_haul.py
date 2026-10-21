"""Long-haul share tool (D15), with lazy loading of the raw T-100 file."""

import math
from functools import lru_cache

import pandas as pd

from app.core.scoring import REPO_ROOT

from .common import LONG_HAUL_MILES, _clean, _error, _fmt, _identity, _lookup_code, _ok

T100_CSV = REPO_ROOT / "data" / "raw" / "T_T100_SEGMENT_ALL_CARRIER.csv"

MIN_THRESHOLD_MILES = 100
MAX_THRESHOLD_MILES = 10_000

# Threshold sensitivity (D15): shares are always reported at these thresholds,
# and flagged when the 2,500 and 4,000 mile values differ by more than 5 points.
SENSITIVITY_THRESHOLDS = (2500, 3000, 4000)
SENSITIVE_SPREAD = 0.05

# T-100 service classes grouped for the long-haul breakdown (D14).
CLASS_GROUPS = {"F": "scheduled_passenger", "L": "charter_passenger", "G": "cargo", "P": "cargo"}
GROUP_ORDER = ["scheduled_passenger", "charter_passenger", "cargo"]


@lru_cache(maxsize=1)
def _t100() -> pd.DataFrame:
    """Departing segments, filtered exactly as in the metrics pipeline (D13)."""
    t = pd.read_csv(
        T100_CSV,
        usecols=["DEPARTURES_PERFORMED", "PASSENGERS", "DISTANCE", "ORIGIN", "DEST", "CLASS"],
    )
    return t[(t["DEPARTURES_PERFORMED"] > 0) & (t["ORIGIN"] != t["DEST"])]


def _share(part, whole):
    return part / whole if whole > 0 else None


def _compute_long_haul(iata: str, threshold_miles: float) -> dict:
    """Unrounded long-haul shares and long-haul departure counts by class group."""
    t = _t100()
    dep = t[t["ORIGIN"] == iata]
    f = dep[dep["CLASS"] == "F"]
    long_f = f[f["DISTANCE"] >= threshold_miles]
    long_all = dep[dep["DISTANCE"] >= threshold_miles]

    by_group = (
        long_all.groupby(long_all["CLASS"].map(CLASS_GROUPS))["DEPARTURES_PERFORMED"].sum()
        .reindex(GROUP_ORDER, fill_value=0)
    )
    return {
        "share_flights": _share(long_f["DEPARTURES_PERFORMED"].sum(), f["DEPARTURES_PERFORMED"].sum()),
        "share_pax": _share(long_f["PASSENGERS"].sum(), f["PASSENGERS"].sum()),
        "share_all_flights": _share(long_all["DEPARTURES_PERFORMED"].sum(), dep["DEPARTURES_PERFORMED"].sum()),
        "long_haul_departures": {g: int(n) for g, n in by_group.items()},
    }


def _shares_out(raw: dict) -> dict:
    out = {}
    for key in ("share_flights", "share_pax", "share_all_flights"):
        out[key] = _clean(raw[key])
        out[f"{key}_display"] = _fmt(raw[key], "pct")
    return out


def _departures_out(counts: dict) -> dict:
    total = sum(counts.values())
    return {
        "total": total,
        "total_display": _fmt(total, "count"),
        "by_class_group": {
            g: {
                "departures": counts[g],
                "departures_display": _fmt(counts[g], "count"),
                "share_of_long_haul": _clean(_share(counts[g], total)),
                "share_of_long_haul_display": _fmt(_share(counts[g], total), "pct"),
            }
            for g in GROUP_ORDER
        },
    }


def long_haul_share(code: str, threshold_miles=LONG_HAUL_MILES, include_cargo: bool = False) -> dict:
    """Share of departures that are long haul (DISTANCE >= threshold), D15.

    Computed from the raw T-100 file with the same filters as the pipeline (at
    3,000 miles this matches the stored airport_metrics values). Also returns
    long-haul departure counts by class group and shares at 2,500 / 3,000 /
    4,000 miles with a threshold_sensitive flag.
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

    headline_key = "share_all_flights" if include_cargo else "share_flights"
    definition = {
        "threshold_miles": threshold_miles,
        "rule": f"segment DISTANCE >= {threshold_miles:,} miles",
        "side": "departing flights (ORIGIN = airport) (D13)",
        "share_flights": "scheduled passenger departures only (CLASS F) (D14)",
        "share_pax": "passengers on scheduled passenger departures (CLASS F) (D14)",
        "share_all_flights": "all departures incl. charter and all-cargo (CLASS F+L+G+P)",
        "long_haul_departures": (
            "long-haul departures by class group: scheduled_passenger (F), "
            "charter_passenger (L), cargo (G+P)"
        ),
        "headline": "share_all_flights (cargo included)" if include_cargo else "share_flights (passenger, CLASS F)",
    }
    cargo = row["cargo_departure_share"]
    flags = []

    if not row["has_t100"]:
        flags.append("no T-100 flight data for this airport: long-haul share unavailable (D9).")
        empty = {"share_flights": None, "share_pax": None, "share_all_flights": None}
        return _ok(
            airport=_identity(row),
            units={"shares": "fraction 0-1", "threshold_miles": "statute miles"},
            definition=definition,
            include_cargo=include_cargo,
            long_haul_share=None,
            long_haul_share_display=None,
            **_shares_out(empty),
            long_haul_departures=None,
            threshold_sensitivity=None,
            threshold_sensitive=None,
            cargo_departure_share=None,
            cargo_departure_share_display=None,
            source="none",
            flags=flags,
        )

    raw = _compute_long_haul(row["iata"], threshold_miles)
    shares = _shares_out(raw)
    if raw["share_flights"] is None:
        flags.append("no scheduled passenger (CLASS F) departures in T-100: passenger shares unavailable.")

    # --- Threshold sensitivity on the headline share (D15)
    by_threshold = {m: _compute_long_haul(row["iata"], m) for m in SENSITIVITY_THRESHOLDS}
    low, high = by_threshold[SENSITIVITY_THRESHOLDS[0]][headline_key], by_threshold[SENSITIVITY_THRESHOLDS[-1]][headline_key]
    spread = None if low is None or high is None else low - high
    sensitive = None if spread is None else bool(spread > SENSITIVE_SPREAD)
    if sensitive:
        flags.append(
            f"threshold-sensitive: the {headline_key} is {_fmt(low, 'pct')} at "
            f"{SENSITIVITY_THRESHOLDS[0]:,} miles but {_fmt(high, 'pct')} at "
            f"{SENSITIVITY_THRESHOLDS[-1]:,} miles; the answer depends on the long-haul definition (D15)."
        )

    if cargo >= 0.25:
        flags.append(
            f"all-cargo flights are {_fmt(cargo, 'pct')} of departures, so including cargo changes "
            "the long-haul share substantially; state which definition is used (D15)."
        )
    if threshold_miles != LONG_HAUL_MILES:
        flags.append(f"non-default threshold; the scoring documentation uses {LONG_HAUL_MILES:,} miles (D15).")

    return _ok(
        airport=_identity(row),
        units={"shares": "fraction 0-1", "threshold_miles": "statute miles", "departures": "count, CY2025"},
        definition=definition,
        include_cargo=include_cargo,
        long_haul_share=shares[headline_key],
        long_haul_share_display=shares[f"{headline_key}_display"],
        **shares,
        long_haul_departures=_departures_out(raw["long_haul_departures"]),
        threshold_sensitivity={
            "basis": headline_key,
            "by_threshold": [
                {"threshold_miles": m, **_shares_out(by_threshold[m])} for m in SENSITIVITY_THRESHOLDS
            ],
            "spread_display": None if spread is None else f"{spread * 100:.1f} percentage points",
            "rule": (
                f"threshold_sensitive when the {SENSITIVITY_THRESHOLDS[0]:,} and "
                f"{SENSITIVITY_THRESHOLDS[-1]:,} mile values differ by more than "
                f"{SENSITIVE_SPREAD * 100:.0f} percentage points"
            ),
        },
        threshold_sensitive=sensitive,
        cargo_departure_share=_clean(cargo),
        cargo_departure_share_display=_fmt(cargo, "pct"),
        source="computed from T-100 Segment All Carriers, CY2025",
        flags=flags,
    )
