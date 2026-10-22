"""
Deterministic investment scoring for US Primary airports.

Pure module: pandas only. No FastAPI, no LLM. Same input -> same output.

How the score is built (see docs/decisions.md):
  1. Each of 4 components is turned into a percentile rank (0-1) within the
     airport's FAA hub group (L / M / S / N), computed across all 402 airports
     nationally (D3, D6, D17).
  2. A missing component gets the neutral value 0.5, never 0 (D18).
  3. investment_score = weighted sum of the 4 percentiles (D19).

Run directly to write data/processed/airport_scores.csv and print a summary:
    python server/app/core/scoring.py
"""

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Config (D16, D19)
# ---------------------------------------------------------------------------

# Component weights. Must sum to 1 so the score stays on a 0-1 scale.
WEIGHTS = {"growth": 0.35, "seat_demand": 0.25, "congestion": 0.25, "scale": 0.15}

# How the growth component blends long-term trend and recent momentum (D5, D16).
GROWTH_MIX = {"cagr": 0.7, "momentum": 0.3}

assert abs(sum(WEIGHTS.values()) - 1) < 1e-9, "WEIGHTS must sum to 1"
assert abs(sum(GROWTH_MIX.values()) - 1) < 1e-9, "GROWTH_MIX must sum to 1"

# Neutral value for a missing component: the peer-group median (D18).
NEUTRAL = 0.5

# Minimum number of components with real data for an airport to be ranked (D18).
MIN_COMPLETENESS = 3

# Weight sensitivity (D19): each weight is moved by +/- this amount.
SENSITIVITY_DELTA = 0.10
# An airport is "weight sensitive" if its rank_in_hub range across the
# scenarios spans more than this share of its hub group (hub_size_n).
# Relative, not a fixed number of places: 10 places is a third of the 30
# Large hubs but only 4% of the 251 ranked Nonhubs.
SENSITIVE_RANGE_SHARE = 0.20

# Which input column feeds each component (higher value = stronger case).
COMPONENT_INPUTS = {
    "seat_demand": "load_factor",     # how full scheduled flights are
    "congestion": "nas_delay_rate",   # NAS-attributed arrival delays (D12)
    "scale": "enpl_2025",             # size of the passenger base
}

# Paths are resolved from this file's location, not the working directory.
# This file is <repo>/server/app/core/scoring.py, so the repo root is 3 levels up.
REPO_ROOT = Path(__file__).resolve().parents[3]
METRICS_CSV = REPO_ROOT / "data" / "processed" / "airport_metrics.csv"
SCORES_CSV = REPO_ROOT / "data" / "processed" / "airport_scores.csv"

NEW_ENGLAND = ["CT", "ME", "MA", "NH", "RI", "VT"]  # D1


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def pct_rank_in_hub(df: pd.DataFrame, values: pd.Series) -> pd.Series:
    """Percentile rank (0-1] of `values` within each hub group.

    Missing values stay NaN: they are not ranked and do not affect the ranks
    of other airports. Ties share the average rank, which is deterministic.
    """
    return values.groupby(df["hub"]).rank(pct=True, method="average")


def compute_scores(df: pd.DataFrame, weights: dict = WEIGHTS) -> pd.DataFrame:
    """Add component percentiles, investment_score and ranks to a copy of df."""
    assert set(weights) == set(WEIGHTS), f"weights must have keys {set(WEIGHTS)}"
    assert abs(sum(weights.values()) - 1) < 1e-9, "weights must sum to 1"

    out = df.copy()

    # --- Growth: rank each signal within hub, blend, then rank the blend again.
    # Re-ranking puts growth back on the same 0-1 percentile scale as the others.
    # If either signal is missing, the blend is NaN and growth is treated as
    # missing. In practice only CAGR is missing (no 2019 history, D5:
    # "insufficient history", not scored on momentum alone); momentum is
    # present for all 402 airports.
    cagr_pct = pct_rank_in_hub(out, out["cagr_19_25"])
    momentum_pct = pct_rank_in_hub(out, out["pct_change_24_25"])
    growth_blend = GROWTH_MIX["cagr"] * cagr_pct + GROWTH_MIX["momentum"] * momentum_pct
    raw = {"growth": pct_rank_in_hub(out, growth_blend)}  # NaN stays NaN

    # --- Other components: one input column each, ranked within hub.
    for component, column in COMPONENT_INPUTS.items():
        raw[component] = pct_rank_in_hub(out, out[column])

    # --- Missing data -> neutral 0.5, and count how many components were real.
    out["score_completeness"] = 0
    for component, pct in raw.items():
        out["score_completeness"] += pct.notna().astype(int)
        out[f"{component}_pct"] = pct.fillna(NEUTRAL)

    # --- Weighted sum of the four percentiles (0-1).
    out["investment_score"] = sum(
        weights[c] * out[f"{c}_pct"] for c in weights
    )

    # --- Only airports with enough real data get a rank (D18).
    out["ranked"] = out["score_completeness"] >= MIN_COMPLETENESS
    ranked_scores = out["investment_score"].where(out["ranked"])  # NaN if unranked

    # --- Ranks: 1 = best, among ranked airports only (NaN otherwise).
    # Ties get the same (lowest) rank. Int64 allows integers alongside NaN.
    out["rank_in_hub"] = (
        ranked_scores.groupby(out["hub"])
        .rank(ascending=False, method="min")
        .astype("Int64")
    )
    out["rank_overall"] = (
        ranked_scores.rank(ascending=False, method="min").astype("Int64")
    )

    # Number of ranked airports in the hub group, for "9th of 30" (D20).
    out["hub_size_n"] = out.groupby("hub")["ranked"].transform("sum").astype(int)

    # Stable, deterministic row order: ranked airports first, then best score,
    # then code to break ties.
    return out.sort_values(
        ["ranked", "investment_score", "code"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Weight sensitivity (D19)
# ---------------------------------------------------------------------------

def shift_weight(weights: dict, key: str, delta: float) -> dict:
    """Change one weight by delta; rescale the other three proportionally so
    the total stays 1. Example: growth +0.10 -> growth 0.45, others keep their
    25:25:15 ratio but shrink from 0.65 to 0.55 in total."""
    new_value = weights[key] + delta
    factor = (1 - new_value) / (1 - weights[key])
    return {k: (new_value if k == key else w * factor) for k, w in weights.items()}


def sensitivity_scenarios(weights: dict = WEIGHTS,
                          delta: float = SENSITIVITY_DELTA) -> dict:
    """The 8 scenarios: each of the 4 weights moved by +delta and -delta."""
    return {
        f"{key} {sign * delta:+.2f}": shift_weight(weights, key, sign * delta)
        for key in weights
        for sign in (+1, -1)
    }


def add_rank_sensitivity(scores: pd.DataFrame, df: pd.DataFrame,
                         weights: dict = WEIGHTS) -> pd.DataFrame:
    """Add rank_range_sensitivity ("min–max" rank_in_hub across the 8 weight
    scenarios) and weight_sensitive (range wider than SENSITIVE_RANGE_SHARE
    of the airport's hub group).

    `scores` is the output of compute_scores(df, weights). Unranked airports
    get NA in both columns, since they have no rank to vary.
    """
    # rank_in_hub of every airport under each scenario: one column per scenario.
    ranks = pd.DataFrame({
        name: compute_scores(df, w).set_index("code")["rank_in_hub"]
        for name, w in sensitivity_scenarios(weights).items()
    })
    lo = scores["code"].map(ranks.min(axis=1))
    hi = scores["code"].map(ranks.max(axis=1))

    out = scores.copy()
    out["rank_range_sensitivity"] = (
        lo.astype(str) + "–" + hi.astype(str)
    ).where(out["ranked"])
    out["weight_sensitive"] = (
        (hi - lo) > SENSITIVE_RANGE_SHARE * out["hub_size_n"]
    ).astype("boolean")
    out.loc[~out["ranked"], "weight_sensitive"] = pd.NA
    return out


def load_metrics(path: Path = METRICS_CSV) -> pd.DataFrame:
    """Read the airport_metrics table (one row per FAA Primary airport)."""
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Script entry point: build the scores file and print a summary
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    metrics = load_metrics()
    scores = add_rank_sensitivity(compute_scores(metrics), metrics)
    scores.to_csv(SCORES_CSV, index=False)
    print(f"Saved {len(scores)} rows -> {SCORES_CSV.relative_to(REPO_ROOT)}\n")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    show = ["code", "city", "state", "investment_score",
            "growth_pct", "seat_demand_pct", "congestion_pct", "scale_pct",
            "score_completeness"]

    hub_names = {"L": "Large", "M": "Medium", "S": "Small", "N": "Nonhub"}
    for hub, name in hub_names.items():
        top = scores[scores["hub"] == hub].head(10)
        print(f"=== Top 10 {name} hubs ===")
        print(top[["rank_in_hub"] + show].round(3).to_string(index=False), "\n")

    print("=== New England (ranked by score) ===")
    ne = scores[scores["state"].isin(NEW_ENGLAND)]
    print(ne[["hub", "rank_in_hub"] + show].round(3).to_string(index=False), "\n")

    print("=== Spot checks ===")
    spot = scores[scores["code"].isin(["BOS", "LAX", "SNA", "SFO", "ANC", "HVN"])]
    print(spot[["hub", "rank_in_hub", "hub_size_n", "rank_overall",
                "rank_range_sensitivity", "weight_sensitive"] + show].round(3).to_string(index=False), "\n")

    print("=== score_completeness counts ===")
    print(scores["score_completeness"].value_counts().sort_index().to_string(), "\n")

    print("=== Ranked airports per hub (score_completeness >= 3) ===")
    per_hub = scores.groupby("hub")["ranked"].agg(ranked="sum", total="count")
    print(per_hub.loc[list(hub_names)].to_string(), "\n")

    print("=== Not ranked (insufficient data to score) ===")
    print(scores.loc[~scores["ranked"], ["code", "city", "state", "hub",
                                         "score_completeness"]].to_string(index=False), "\n")

    print(f"=== Weight-sensitive airports (rank range > {SENSITIVE_RANGE_SHARE:.0%} "
          f"of hub group across {len(sensitivity_scenarios())} scenarios) ===")
    for hub, name in hub_names.items():
        in_hub = scores[(scores["hub"] == hub) & scores["ranked"]]
        sens = in_hub[in_hub["weight_sensitive"] == True]  # noqa: E712 (nullable bool)
        print(f"--- {name}: {len(sens)} of {len(in_hub)} ---")
        if len(sens):
            print(sens[["rank_in_hub", "rank_range_sensitivity", "code", "city", "state",
                        "growth_pct", "seat_demand_pct", "congestion_pct", "scale_pct"]]
                  .round(3).to_string(index=False))
