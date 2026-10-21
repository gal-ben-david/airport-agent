"""
Manual check of the agent data tools: calls each tool and prints the JSON.

Run from the repo root:
    python server/scripts/test_tools.py
"""

import json
import sys
from pathlib import Path

# Make `app` importable: this file is <repo>/server/scripts/test_tools.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import tools  # noqa: E402
from app.agent.tools.common import _lookup_code  # noqa: E402
from app.agent.tools.long_haul import _compute_long_haul  # noqa: E402

QUERIES = ["LAX", "SNA", "SFO", "BOS", "ANC", "HVN", "Los Angeles", "New England", "XXX"]
CODES = ["LAX", "SNA", "SFO", "BOS", "ANC", "HVN"]


def show(title: str, result: dict) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    json.dumps(result, allow_nan=False)  # fails if anything is not strict JSON


def check(label: str, condition: bool) -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    return condition


# --- 1. find_airport on every query
for q in QUERIES:
    show(f"find_airport({q!r})", tools.find_airport(q))

# --- 2. get_airport_profile: test codes, a resolved city, an unranked airport, an invalid code
for c in CODES:
    show(f"get_airport_profile({c!r})", tools.get_airport_profile(c))
la = tools.find_airport("Los Angeles")["match"]["code"]
show(f"get_airport_profile({la!r})  # via find_airport('Los Angeles')", tools.get_airport_profile(la))
show("get_airport_profile('YNG')  # unranked, 1 of 4 components", tools.get_airport_profile("YNG"))
show("get_airport_profile('XXX')", tools.get_airport_profile("XXX"))

# --- 3. rank_airports
show("rank_airports('New England')", tools.rank_airports("New England"))
show("rank_airports(['CA'], hub='L', top_n=3)", tools.rank_airports(["CA"], hub="L", top_n=3))
show("rank_airports(hub='medium', top_n=3)  # national", tools.rank_airports(hub="medium", top_n=3))
show("rank_airports(['XX'])", tools.rank_airports(["XX"]))
show("rank_airports(hub='huge')", tools.rank_airports(hub="huge"))
show("rank_airports(top_n=0)", tools.rank_airports(top_n=0))

# --- 4. compare_airports
show("compare_airports(['SFO', 'LAX'])  # same hub", tools.compare_airports(["SFO", "LAX"]))
show("compare_airports(['BOS', 'SNA', 'HVN'])  # mixed hubs", tools.compare_airports(["BOS", "SNA", "HVN"]))
show("compare_airports(['LAX', 'XXX'])", tools.compare_airports(["LAX", "XXX"]))
show("compare_airports(['LAX'])", tools.compare_airports(["LAX"]))

# --- 5. long_haul_share
for c in CODES:
    show(f"long_haul_share({c!r})", tools.long_haul_share(c))
show("long_haul_share('ANC', include_cargo=True)", tools.long_haul_share("ANC", include_cargo=True))
show("long_haul_share('ANC', 2500)", tools.long_haul_share("ANC", 2500))
show("long_haul_share('XXX')", tools.long_haul_share("XXX"))
show("long_haul_share('LAX', -5)", tools.long_haul_share("LAX", -5))
show("long_haul_share('LAX', 'far')", tools.long_haul_share("LAX", "far"))

# --- 6. Sanity checks
print(f"\n{'=' * 80}\nSANITY CHECKS\n{'=' * 80}")
results = []

print("Recomputing 3,000 mi from raw T-100 matches the stored values in airport_scores.csv:")
stored_cols = {
    "share_flights": "long_haul_share_flights",
    "share_pax": "long_haul_share_pax",
    "share_all_flights": "long_haul_share_all_flights",
}
for c in CODES:
    row = _lookup_code(c)
    computed = _compute_long_haul(row["iata"], 3000)
    same = all(abs((computed[k] or 0) - row[col]) < 1e-9 for k, col in stored_cols.items())
    shown = {k: round(float(computed[k]), 4) for k in stored_cols}
    results.append(check(f"{c}: {shown}", same))

print("Long-haul departure counts add up to the all-flights share:")
for c in CODES:
    r = tools.long_haul_share(c)
    by_group = r["long_haul_departures"]["by_class_group"]
    total_ok = sum(g["departures"] for g in by_group.values()) == r["long_haul_departures"]["total"]
    results.append(check(f"{c}: total {r['long_haul_departures']['total_display']}, "
                         f"threshold_sensitive={r['threshold_sensitive']}", total_ok))

print("Display values are computed from unrounded data (SFO load factor 0.82349... -> 82.3%):")
sfo = tools.get_airport_profile("SFO")["airport"]["metrics"]
results.append(check(f"SFO load_factor_display = {sfo['load_factor_display']}", sfo["load_factor_display"] == "82.3%"))

print("find_airport ignores filler words:")
for q, code in [("Anchorage airport", None), ("SFO airport", "SFO"), ("Santa Ana airport", "SNA"),
                ("Los Angeles International", "LAX")]:
    r = tools.find_airport(q)
    got = r["match"]["code"] if r.get("match") else [x["code"] for x in r.get("candidates", [])]
    expected = code if code else ["ANC", "MRI"]
    results.append(check(f"{q!r} -> {got}", got == expected))

print("ANC reproduces the D15 sensitivity table (CLASS F, by flights):")
for miles, expected in [(2500, "10.1%"), (3000, "2.8%"), (4000, "0.2%")]:
    got = tools.long_haul_share("ANC", miles)["share_flights_display"]
    results.append(check(f"{miles:,} mi: {got} (D15: {expected})", got == expected))

got = tools.long_haul_share("ANC", include_cargo=True)["long_haul_share_display"]
results.append(check(f"ANC incl. cargo at 3,000 mi: {got} (D15: 31.1%)", got == "31.1%"))

print(f"\n{sum(results)} of {len(results)} checks passed.")
