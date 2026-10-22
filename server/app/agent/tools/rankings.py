"""Multi-airport tools: rank_airports, compare_airports."""

from .common import (
    DELAY_CAVEAT,
    HUB_LABELS,
    HUB_ORDER,
    REGIONS,
    UNITS,
    METRIC_SPEC,
    _clean,
    _components,
    _error,
    _flags,
    _fmt,
    _identity,
    _is_true,
    _lookup_code,
    _ok,
    _profile,
    _rank_text,
    _scores,
    _values,
)

# The metric behind each score component (D16).
RANK_METRIC_SPEC = {
    k: METRIC_SPEC[k]
    for k in ("enpl_2025", "cagr_19_25", "pct_change_24_25", "load_factor", "nas_delay_rate")
}

MAX_TOP_N = 50

HUB_ALIASES = {
    "l": "L", "large": "L",
    "m": "M", "medium": "M",
    "s": "S", "small": "S",
    "n": "N", "nonhub": "N", "non-hub": "N",
}


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
    if states is None:
        region_label = "US"
    elif region_name:
        region_label = region_name
    else:
        region_label = "/".join(state_list) if len(state_list) <= 4 else "selected-state"

    groups = []
    for h in hubs:
        in_group = region[region["hub"] == h]
        if in_group.empty:
            continue
        ranked = in_group[in_group["ranked"]].sort_values(["rank_in_hub", "code"])
        n_national = int(df.loc[df["hub"] == h, "ranked"].sum())
        shown = min(top_n, len(ranked))
        top = f"top {region_label} airport" if shown == 1 else f"top {shown} {region_label} airports"
        groups.append({
            "hub": h,
            "hub_label": HUB_LABELS[h],
            "ranked_nationally_in_hub": n_national,
            "ranked_in_region": len(ranked),
            # Ready-made heading so "top 5 of 79" is not misread as 79 airports in the region.
            "heading_display": (
                f"{HUB_LABELS[h]}s ({top}; ranks are among {n_national} "
                f"{HUB_LABELS[h].lower()}s nationally)"
            ),
            "airports": [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "city": r["city"],
                    "state": r["state"],
                    "investment_score": _clean(r["investment_score"]),
                    "investment_score_display": _fmt(r["investment_score"], "decimal"),
                    "national_rank_in_hub": _rank_text(r),
                    "rank_in_hub_number": int(r["rank_in_hub"]),
                    "rank_range_sensitivity": r["rank_range_sensitivity"],
                    "weight_sensitive": _is_true(r["weight_sensitive"]),
                    # Components and their metrics, so the score can be explained
                    # without a profile call per airport.
                    "components": _components(r),
                    "metrics": _values(r, RANK_METRIC_SPEC),
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
        delay_caveat=DELAY_CAVEAT,
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
