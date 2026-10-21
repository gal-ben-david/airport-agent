"""
Deterministic data tools for the airport investment agent.

Pure functions over data/processed/airport_scores.csv (and the raw T-100 file
for long-haul thresholds other than 3,000 miles). No LLM here: every number the
agent reports comes from these functions (CLAUDE.md, key principles).

Every tool returns a JSON-serializable dict:
  - success: {"ok": True, "data_year": 2025, "data_status": ..., "units": ..., "flags": [...], ...}
  - failure: {"ok": False, "error": "..."}  (tools never raise on bad input)

Fractions are returned as 0-1 decimals (0.817 = 81.7%); formatting is left to
the agent. Missing values are None, with a flag explaining why (D12, D18).
"""

import math
from functools import lru_cache

import pandas as pd

from app.core.scoring import NEW_ENGLAND, REPO_ROOT, SCORES_CSV, WEIGHTS

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_YEAR = 2025
DATA_STATUS = (
    "Calendar year 2025. FAA 2025 enplanements are preliminary and may be "
    "revised (D4, D11)."
)

T100_CSV = REPO_ROOT / "data" / "raw" / "T_T100_SEGMENT_ALL_CARRIER.csv"

LONG_HAUL_MILES = 3000  # D15: default threshold, stored in airport_scores.csv
MIN_THRESHOLD_MILES = 100
MAX_THRESHOLD_MILES = 10_000

MAX_TOP_N = 50
MAX_CANDIDATES = 10

HUB_LABELS = {"L": "Large hub", "M": "Medium hub", "S": "Small hub", "N": "Nonhub"}
HUB_ORDER = ["L", "M", "S", "N"]
HUB_ALIASES = {
    "l": "L", "large": "L",
    "m": "M", "medium": "M",
    "s": "S", "small": "S",
    "n": "N", "nonhub": "N", "non-hub": "N",
}

# Only predefined region (D1). For any other region the agent passes an
# explicit list of states.
REGIONS = {"new england": NEW_ENGLAND}

# Informal names that don't match a single city or code. Each carries the
# assumption the agent must state when it uses the match.
LA_NOTE = (
    "Assumed 'Los Angeles' means LAX, the only Primary airport in the city of "
    "Los Angeles. The wider LA area also has Burbank (BUR), Long Beach (LGB), "
    "Ontario (ONT) and Santa Ana / John Wayne (SNA)."
)
ALIASES = {
    "la": ("LAX", LA_NOTE),
    "los angeles": ("LAX", LA_NOTE),
}

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


@lru_cache(maxsize=1)
def _t100() -> pd.DataFrame:
    """Departing segments, filtered exactly as in the metrics pipeline (D13)."""
    t = pd.read_csv(
        T100_CSV,
        usecols=["DEPARTURES_PERFORMED", "PASSENGERS", "DISTANCE", "ORIGIN", "DEST", "CLASS"],
    )
    return t[(t["DEPARTURES_PERFORMED"] > 0) & (t["ORIGIN"] != t["DEST"])]


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


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def find_airport(query: str) -> dict:
    """Resolve a code, city or name to a Primary airport.

    Order: FAA code -> IATA code -> known alias -> exact city -> partial
    name/city match. Several hits return `candidates` instead of a guess.
    """
    if not isinstance(query, str) or not query.strip():
        return _error("query must be a non-empty string (airport code, city or name).")
    q = query.strip()
    df = _scores()

    if q.lower() in REGIONS:
        return _error(
            f"'{q}' is a region, not an airport. Use rank_airports(states='{q}') "
            f"(states: {', '.join(REGIONS[q.lower()])})."
        )

    row = _lookup_code(q)
    if row is not None:
        match_type = "faa_code" if row["code"].upper() == q.upper() else "iata_code"
        return _ok(query=q, match=_identity(row), match_type=match_type, note=None, candidates=[])

    if q.lower() in ALIASES:
        code, note = ALIASES[q.lower()]
        return _ok(query=q, match=_identity(_lookup_code(code)), match_type="alias", note=note, candidates=[])

    hits = df[df["city"].str.lower() == q.lower()]
    match_type = "city"
    if hits.empty and len(q) >= 3:
        pattern = q.lower()
        hits = df[
            df["city"].str.lower().str.contains(pattern, regex=False)
            | df["name"].str.lower().str.contains(pattern, regex=False)
        ]
        match_type = "partial_name_or_city"

    if len(hits) == 1:
        return _ok(query=q, match=_identity(hits.iloc[0]), match_type=match_type, note=None, candidates=[])
    if len(hits) > 1:
        hits = hits.sort_values("enpl_2025", ascending=False)
        return _ok(
            query=q,
            match=None,
            match_type="ambiguous",
            note=f"{len(hits)} airports match '{q}'. Ask the user which one they mean.",
            candidates=[_identity(r) for _, r in hits.head(MAX_CANDIDATES).iterrows()],
        )
    return _error(
        f"No FAA Primary airport matches '{q}'. Only airports with more than "
        "10,000 annual enplanements are covered (D2)."
    )


def get_airport_profile(code: str) -> dict:
    """Full metrics, score, components, rank and flags for one airport."""
    row = _lookup_code(code)
    if row is None:
        return _error(f"Unknown airport code '{code}'. Use find_airport to resolve names or cities.")
    return _ok(units=UNITS, airport=_profile(row))


def rank_airports(states=None, hub: str | None = None, top_n: int = 5) -> dict:
    """Top ranked airports, grouped by hub size, with national rank in hub (D6, D20).

    states: None (all US), a region name ("New England"), a state code ("TX"),
            or a list of state codes.
    hub:    None (all groups), or L / M / S / N (or large / medium / small / nonhub).
    """
    df = _scores()
    all_states = set(df["state"])

    # --- Region -> list of states
    region_name = None
    if states is None:
        state_list = sorted(all_states)
        region_name = "United States (all states and territories)"
    elif isinstance(states, str):
        key = states.strip().lower()
        if key in REGIONS:
            state_list = REGIONS[key]
            region_name = states.strip().title()
        else:
            state_list = [states]
    elif isinstance(states, (list, tuple)) and states:
        state_list = list(states)
    else:
        return _error("states must be None, a region name ('New England'), a state code, or a list of state codes.")

    if not all(isinstance(s, str) for s in state_list):
        return _error("Every state must be a two-letter code string, e.g. 'MA'.")
    state_list = sorted({s.strip().upper() for s in state_list})
    unknown = [s for s in state_list if s not in all_states]
    if unknown:
        return _error(
            f"Unknown state code(s): {', '.join(unknown)}. Use two-letter codes (e.g. 'MA'). "
            f"Predefined regions: {', '.join(r.title() for r in REGIONS)}."
        )

    # --- Hub filter
    if hub is None:
        hubs = HUB_ORDER
    elif isinstance(hub, str) and hub.strip().lower() in HUB_ALIASES:
        hubs = [HUB_ALIASES[hub.strip().lower()]]
    else:
        return _error(f"Unknown hub '{hub}'. Use L, M, S or N (large, medium, small, nonhub).")

    # --- top_n
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= MAX_TOP_N:
        return _error(f"top_n must be an integer between 1 and {MAX_TOP_N}.")

    region = df[df["state"].isin(state_list) & df["hub"].isin(hubs)]

    groups = []
    for h in hubs:
        in_group = region[region["hub"] == h]
        if in_group.empty:
            continue
        ranked = in_group[in_group["ranked"]].sort_values(["rank_in_hub", "code"])
        groups.append({
            "hub": h,
            "hub_label": HUB_LABELS[h],
            "ranked_nationally_in_hub": int(df.loc[df["hub"] == h, "ranked"].sum()),
            "ranked_in_region": len(ranked),
            "airports": [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "city": r["city"],
                    "state": r["state"],
                    "investment_score": _clean(r["investment_score"]),
                    "national_rank_in_hub": _rank_text(r),
                    "rank_in_hub_number": int(r["rank_in_hub"]),
                    "rank_range_sensitivity": r["rank_range_sensitivity"],
                    "weight_sensitive": _is_true(r["weight_sensitive"]),
                    "score_completeness": f"{int(r['score_completeness'])} of 4",
                    "data_confidence": r["data_confidence"],
                    "has_delays": bool(r["has_delays"]),
                    "flags": _flags(r),
                }
                for _, r in ranked.head(top_n).iterrows()
            ],
        })

    unranked = region[~region["ranked"]]
    return _ok(
        region=region_name,
        states_used=state_list,
        hub_filter=hubs,
        top_n=top_n,
        groups=groups,
        unranked_in_region=[
            {
                **_identity(r),
                "score_completeness": f"{int(r['score_completeness'])} of 4",
                "reason": "not ranked: insufficient data (D18)",
            }
            for _, r in unranked.iterrows()
        ],
        note=(
            "Scores are percentiles within each FAA hub group, computed nationally, "
            "so they are comparable only inside a group, not across groups (D6, D20)."
        ),
    )


def compare_airports(codes) -> dict:
    """Side-by-side profiles for 2-5 airports."""
    if not isinstance(codes, (list, tuple)) or not all(isinstance(c, str) for c in codes):
        return _error("codes must be a list of 2-5 airport codes, e.g. ['LAX', 'SFO'].")
    codes = list(dict.fromkeys(c.strip().upper() for c in codes))  # dedupe, keep order
    if not 2 <= len(codes) <= 5:
        return _error(f"compare_airports needs 2-5 distinct codes; got {len(codes)}.")

    rows, unknown = [], []
    for c in codes:
        row = _lookup_code(c)
        (unknown.append(c) if row is None else rows.append(row))
    if unknown:
        return _error(f"Unknown airport code(s): {', '.join(unknown)}. Use find_airport to resolve names or cities.")

    hubs = sorted({r["hub"] for r in rows}, key=HUB_ORDER.index)
    same_hub = len(hubs) == 1
    note = None if same_hub else (
        "These airports are in different FAA hub groups ("
        + ", ".join(HUB_LABELS[h] for h in hubs)
        + "). Scores are percentiles within each group, so they are not directly "
        "comparable; compare raw metrics or each airport's rank within its own group (D20)."
    )
    return _ok(
        units=UNITS,
        same_hub_group=same_hub,
        note=note,
        airports=[_profile(r) for r in rows],
    )


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
