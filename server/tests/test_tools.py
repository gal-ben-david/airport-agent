"""
Sanity checks on the agent data tools, moved unchanged from the PASS/FAIL section
of scripts/check_tools.py. Reads local files only (no Claude API, no OpenSky).

Run from server/:
    pytest
"""

import pytest

from app.agent import tools
from app.agent.tools.common import _components, _lookup_code, _scores
from app.agent.tools.long_haul import _compute_long_haul

CODES = ["LAX", "SNA", "SFO", "BOS", "ANC", "HVN"]


# Recomputing 3,000 mi from raw T-100 matches the stored values in airport_scores.csv
@pytest.mark.parametrize("code", CODES)
def test_long_haul_recompute_matches_stored(code):
    stored_cols = {
        "share_flights": "long_haul_share_flights",
        "share_pax": "long_haul_share_pax",
        "share_all_flights": "long_haul_share_all_flights",
    }
    row = _lookup_code(code)
    computed = _compute_long_haul(row["iata"], 3000)
    assert all(abs((computed[k] or 0) - row[col]) < 1e-9 for k, col in stored_cols.items())


# Long-haul departure counts add up to the all-flights share
@pytest.mark.parametrize("code", CODES)
def test_long_haul_departures_add_up(code):
    r = tools.long_haul_share(code)
    by_group = r["long_haul_departures"]["by_class_group"]
    assert sum(g["departures"] for g in by_group.values()) == r["long_haul_departures"]["total"]


# Display values are computed from unrounded data (SFO load factor 0.82349... -> 82.3%)
def test_sfo_load_factor_display():
    sfo = tools.get_airport_profile("SFO")["airport"]["metrics"]
    assert sfo["load_factor_display"] == "82.3%"


# Top percentile reads 'highest of N', others stay 'X%'
def test_hvn_percentile_displays():
    hvn = tools.get_airport_profile("HVN")["airport"]["score"]["components"]
    assert hvn["growth"]["percentile_pct_display"] == "highest of 79 small hubs"
    assert hvn["seat_demand"]["percentile_pct_display"] == "24.1%"


# Every percentile of exactly 1.0 in the data, across all hub groups and components
def test_every_top_percentile_reads_highest_of():
    tops = [(r["code"], c, d["percentile_pct_display"])
            for _, r in _scores().iterrows() for c, d in _components(r).items()
            if d["percentile"] == 1 and not d["imputed_neutral"]]
    for code, comp, disp in tops:
        assert disp.startswith("highest of "), f"{code} {comp}: {disp}"


# find_airport ignores filler words
@pytest.mark.parametrize("query, expected", [
    ("Anchorage airport", ["ANC", "MRI"]),
    ("SFO airport", "SFO"),
    ("Santa Ana airport", "SNA"),
    ("Los Angeles International", "LAX"),
])
def test_find_airport_ignores_filler_words(query, expected):
    r = tools.find_airport(query)
    got = r["match"]["code"] if r.get("match") else [x["code"] for x in r.get("candidates", [])]
    assert got == expected


# ANC reproduces the D15 sensitivity table (CLASS F, by flights)
@pytest.mark.parametrize("miles, expected", [(2500, "10.1%"), (3000, "2.8%"), (4000, "0.2%")])
def test_anc_d15_sensitivity(miles, expected):
    assert tools.long_haul_share("ANC", miles)["share_flights_display"] == expected


# ANC incl. cargo at 3,000 mi (D15: 31.1%)
def test_anc_including_cargo_d15():
    assert tools.long_haul_share("ANC", include_cargo=True)["long_haul_share_display"] == "31.1%"


# Different hub groups (D20): components in a per-airport block, raw-metric comparison in code
def test_compare_mixed_hubs_fair_comparison():
    result = tools.compare_airports(["LAX", "SNA"])
    assert result["same_hub_group"] is False
    assert "NAS delay rate: LAX 4.6% vs SNA 4.3% (LAX higher by 0.3 points)" in result["fair_comparison"]
    for airport in result["airports"]:
        assert "components" not in airport["score"]
        block = airport["score"]["components_within_own_hub_group"]
        assert block["label"] == "within its own hub group (not comparable across groups)"
        assert set(block["components"]) == {"growth", "seat_demand", "congestion", "scale"}


# Same hub group: components stay side by side, no fair_comparison
def test_compare_same_hub_unchanged():
    result = tools.compare_airports(["SFO", "LAX"])
    assert result["same_hub_group"] is True
    assert "fair_comparison" not in result
    assert all("components" in a["score"] for a in result["airports"])
