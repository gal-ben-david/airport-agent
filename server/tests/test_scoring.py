"""
Scoring checks, moved unchanged from Part 1 (automatic checks) of
notebooks/scoring_review.ipynb. Reads data/processed/airport_scores.csv only.

Run from server/:
    pytest
"""

import pandas as pd
import pytest

from app.core.scoring import SCORES_CSV


@pytest.fixture(scope="module")
def s():
    return pd.read_csv(SCORES_CSV)


# Check 1: 402 rows, all scores between 0 and 1
def test_402_rows_scores_between_0_and_1(s):
    assert len(s) == 402
    assert s["investment_score"].between(0, 1).all()


# HVN: completeness 3, congestion imputed to the neutral 0.5
def test_hvn_completeness_and_neutral_congestion(s):
    hvn = s.set_index("code").loc["HVN"]
    assert hvn["score_completeness"] == 3 and hvn["congestion_pct"] == 0.5


# SFO scores higher than LAX
def test_sfo_scores_higher_than_lax(s):
    sfo_lax = s[s["code"].isin(["SFO", "LAX"])].set_index("code")
    assert sfo_lax.loc["SFO", "investment_score"] > sfo_lax.loc["LAX", "investment_score"]
