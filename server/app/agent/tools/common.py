"""
Shared pieces for the agent tools: data loading/cache, the response wrapper,
flags, units and the airport profile builder.

Every tool returns a JSON-serializable dict:
  - success: {"ok": True, "data_year": 2025, "data_status": ..., "units": ..., "flags": [...], ...}
  - failure: {"ok": False, "error": "..."}  (tools never raise on bad input)

Fractions are returned as 0-1 decimals (0.817 = 81.7%); formatting is left to
the agent. Missing values are None, with a flag explaining why (D12, D18).
"""

import math
from functools import lru_cache

import pandas as pd

from app.core.scoring import NEW_ENGLAND, SCORES_CSV, WEIGHTS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_YEAR = 2025
DATA_STATUS = (
    "Calendar year 2025. FAA 2025 enplanements are preliminary and may be "
    "revised (D4, D11)."
)

LONG_HAUL_MILES = 3000  # D15: default threshold, stored in airport_scores.csv

HUB_LABELS = {"L": "Large hub", "M": "Medium hub", "S": "Small hub", "N": "Nonhub"}
HUB_ORDER = ["L", "M", "S", "N"]

# Only predefined region (D1). For any other region the agent passes an
# explicit list of states.
REGIONS = {"new england": NEW_ENGLAND}

# Which raw column feeds each score component; NaN there = component imputed
# with the neutral 0.5 (mirrors compute_scores in core/scoring.py, D18).
COMPONENT_SOURCES = {
    "growth": "cagr_19_25",
    "seat_demand": "load_factor",
    "congestion": "nas_delay_rate",
    "scale": "enpl_2025",
}

UNITS = {
    "enplanements": "passengers boarding (count, calendar year)",
    "cagr_19_25": "compound annual growth rate, fraction per year (0.03 = 3%/yr)",
    "pct_change_24_25": "fraction (0.05 = +5%)",
    "load_factor": "fraction 0-1, scheduled passenger flights (CLASS F)",
    "nas_delay_rate": "fraction of arrivals with NAS-attributed delay of 15+ min",
    "delay_rate": "fraction of arrivals delayed 15+ min, any cause",
    "cancel_rate": "fraction of arrivals cancelled",
    "shares": "fraction 0-1",
    "percentiles": "0-1 within FAA hub group (0.85 = better than 85% of peers)",
    "investment_score": "0-1, weighted sum of the 4 percentiles",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _scores() -> pd.DataFrame:
    return pd.read_csv(SCORES_CSV)


def _clean(v):
    """Convert pandas/numpy values to plain JSON types; NaN -> None."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) or hasattr(v, "item"):
        v = v.item() if hasattr(v, "item") else v
        if isinstance(v, float):
            if math.isnan(v):
                return None
            return int(v) if v.is_integer() else round(v, 4)
    return v


def _is_true(v) -> bool:
    """weight_sensitive is read from CSV as True / False / NaN (unranked)."""
    return v is True or v == "True" or (hasattr(v, "item") and v.item() is True)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _ok(**fields) -> dict:
    return {"ok": True, "data_year": DATA_YEAR, "data_status": DATA_STATUS, **fields}


def _error(message: str, **fields) -> dict:
    return {"ok": False, "error": message, **fields}


def _lookup_code(code) -> pd.Series | None:
    """Exact match on FAA code, then IATA code. None if not found."""
    if not isinstance(code, str) or not code.strip():
        return None
    q = code.strip().upper()
    df = _scores()
    for col in ("code", "iata"):
        hit = df[df[col].str.upper() == q]
        if len(hit) == 1:
            return hit.iloc[0]
    return None


def _identity(row: pd.Series) -> dict:
    return {
        "code": row["code"],
        "iata": row["iata"],
        "name": row["name"],
        "city": row["city"],
        "state": row["state"],
        "hub": row["hub"],
        "hub_label": HUB_LABELS[row["hub"]],
    }


def _missing_components(row: pd.Series) -> list[str]:
    return [c for c, col in COMPONENT_SOURCES.items() if pd.isna(row[col])]


def _rank_text(row: pd.Series) -> str | None:
    if not row["ranked"]:
        return None
    return f"{_ordinal(int(row['rank_in_hub']))} of {int(row['hub_size_n'])} {HUB_LABELS[row['hub']].lower()}s"


def _flags(row: pd.Series) -> list[str]:
    """Caveats the agent should mention when it reports this airport."""
    flags = []
    missing = _missing_components(row)
    if not row["ranked"]:
        flags.append(
            f"not ranked: insufficient data (D18). Only {int(row['score_completeness'])} "
            f"of 4 score components have real data (missing: {', '.join(missing)})."
        )
    elif missing:
        flags.append(
            f"score uses the neutral value 0.5 for missing component(s): {', '.join(missing)} (D18)."
        )
    if row["ranked"] and _is_true(row["weight_sensitive"]):
        flags.append(
            f"weight-sensitive: rank ranges {row['rank_range_sensitivity']} of "
            f"{int(row['hub_size_n'])} across the 8 weight scenarios (D19)."
        )
    if row["growth_flag"] == "insufficient_history":
        flags.append("no 2019 enplanements: growth (CAGR) not computed (D5, D8).")
    if not row["has_delays"]:
        flags.append(
            "no BTS delay data: congestion is unknown, not zero (D12)."
        )
    if row["data_confidence"] == "low":
        flags.append(
            "low data confidence: T-100 passengers differ from FAA enplanements by "
            "more than 10%, or T-100 has no scheduled passenger service (D9)."
        )
    elif row["data_confidence"] == "no_t100":
        flags.append("no T-100 flight data: capacity, load factor and route mix unavailable (D9).")
    return flags


def _profile(row: pd.Series) -> dict:
    ranked = bool(row["ranked"])
    missing = _missing_components(row)
    return {
        **_identity(row),
        "metrics": {
            "enpl_2025": _clean(row["enpl_2025"]),
            "enpl_2024": _clean(row["enpl_2024"]),
            "enpl_2019": _clean(row["enpl_2019"]),
            "cagr_19_25": _clean(row["cagr_19_25"]),
            "pct_change_24_25": _clean(row["pct_change_24_25"]),
            "load_factor": _clean(row["load_factor"]),
            "nas_delay_rate": _clean(row["nas_delay_rate"]),
            "passengers_FL": _clean(row["passengers_FL"]),
            "departures_FL": _clean(row["departures_FL"]),
            "tot_departures_all": _clean(row["tot_departures_all"]),
        },
        "context_metrics": {  # reported, not scored (D16)
            "delay_rate": _clean(row["delay_rate"]),
            "cancel_rate": _clean(row["cancel_rate"]),
            "arr_flights_reported": _clean(row["arr_flights"]),
            "long_haul_share_flights": _clean(row["long_haul_share_flights"]),
            "long_haul_share_pax": _clean(row["long_haul_share_pax"]),
            "cargo_departure_share": _clean(row["cargo_departure_share"]),
            "long_haul_threshold_miles": LONG_HAUL_MILES,
        },
        "score": {
            # Unranked airports get no score: it rests on too much imputed data (D18).
            "investment_score": _clean(row["investment_score"]) if ranked else None,
            "components": {
                c: {
                    "percentile": _clean(row[f"{c}_pct"]),
                    "weight": WEIGHTS[c],
                    "imputed_neutral": c in missing,
                }
                for c in WEIGHTS
            },
        },
        "ranked": ranked,
        "rank_in_hub": _rank_text(row),
        "rank_in_hub_number": int(row["rank_in_hub"]) if ranked else None,
        "hub_size_n": int(row["hub_size_n"]),
        "rank_range_sensitivity": row["rank_range_sensitivity"] if ranked else None,
        "weight_sensitive": _is_true(row["weight_sensitive"]) if ranked else None,
        "score_completeness": f"{int(row['score_completeness'])} of 4",
        "data_confidence": row["data_confidence"],
        "has_delays": bool(row["has_delays"]),
        "flags": _flags(row),
    }
