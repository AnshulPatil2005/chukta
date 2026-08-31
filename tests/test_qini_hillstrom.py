"""Qini validated against a published external experiment.

Everything else in this project is scored on a simulator I wrote. That is the
trap the prior-art review flagged: *a benchmark you invent will flatter your own
design*. So the metric is also checked against data nobody here generated.

Kevin Hillstrom's MineThatData e-mail challenge (2008) is a real randomised
experiment: 64,000 customers split three ways between a men's-merchandise
e-mail, a women's e-mail, and no e-mail. Randomised assignment means the
treatment effect is identified without any modelling assumption, and the
headline result is public and widely reproduced - the men's e-mail lifts the
two-week visit rate by roughly 7.7 percentage points.

Unlike this project's simulator, the arms contain DIFFERENT customers, so this
exercises `qini_scaled` rather than the paired form - which is the code path
live traffic would actually use.

The dataset is not committed (4 MB). Fetch it with:

    python -m eval.fetch_hillstrom

These tests skip if it is absent, so the suite still runs offline.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from eval.uplift import qini_coefficient, qini_scaled

DATA = Path(__file__).resolve().parent.parent / "data" / "hillstrom.csv"

# Pinned so a silently changed file fails loudly rather than shifting results.
EXPECTED_ROWS = 64000
EXPECTED_SHA256 = "0e5893329d8b93cefecc571777672028290ab69865718020c78c7284f291aece"

pytestmark = pytest.mark.skipif(
    not DATA.exists(),
    reason="run `python -m eval.fetch_hillstrom` to enable the external benchmark",
)


@pytest.fixture(scope="module")
def hillstrom() -> list[dict]:
    with open(DATA, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def mens_vs_holdout(raw: list[dict], outcome: str = "visit") -> list[dict]:
    """Men's e-mail against the no-e-mail holdout. The women's arm is dropped:
    two treatments against one control is a different estimand."""
    out = []
    for r in raw:
        if r["segment"] == "Mens E-Mail":
            treated = True
        elif r["segment"] == "No E-Mail":
            treated = False
        else:
            continue
        out.append(
            {
                "treated": treated,
                "outcome": float(r[outcome]),
                "recency": float(r["recency"]),
                "history": float(r["history"]),
                "mens": float(r["mens"]),
            }
        )
    return out


# -- the dataset is what we think it is --------------------------------------


def test_dataset_is_the_pinned_one(hillstrom):
    import hashlib

    assert len(hillstrom) == EXPECTED_ROWS
    assert hashlib.sha256(DATA.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_arms_are_balanced_as_a_randomised_trial_should_be(hillstrom):
    rows = mens_vs_holdout(hillstrom)
    n_t = sum(1 for r in rows if r["treated"])
    n_c = len(rows) - n_t
    assert n_t == 21307 and n_c == 21306


# -- the endpoint invariant --------------------------------------------------


def test_curve_endpoint_equals_the_published_incremental_effect(hillstrom):
    """At k = N the scaled Qini reduces to the classic incremental-responders
    estimate, so the end of the curve must equal it exactly regardless of how
    the population was ordered. This anchors the whole curve.

        R_t - R_c * N_t/N_c  =  3894 - 2262 * 21307/21306  =  1631.89
    """
    rows = mens_vs_holdout(hillstrom)
    curve = qini_scaled([{**r, "score": r["history"]} for r in rows])
    assert curve.final_value == pytest.approx(1631.8938, abs=0.01)


def test_endpoint_is_invariant_to_the_ranking(hillstrom):
    """Different scores reshape the curve but cannot move where it ends."""
    rows = mens_vs_holdout(hillstrom)
    by_history = qini_scaled([{**r, "score": r["history"]} for r in rows])
    by_recency = qini_scaled([{**r, "score": -r["recency"]} for r in rows])
    assert by_history.final_value == pytest.approx(by_recency.final_value, abs=0.01)


def test_lift_matches_the_published_seven_point_seven_points(hillstrom):
    rows = mens_vs_holdout(hillstrom)
    t = [r["outcome"] for r in rows if r["treated"]]
    c = [r["outcome"] for r in rows if not r["treated"]]
    lift_pp = (sum(t) / len(t) - sum(c) / len(c)) * 100
    assert lift_pp == pytest.approx(7.66, abs=0.05)


# -- the metric behaves on real data -----------------------------------------


def test_random_ranking_scores_near_zero(hillstrom):
    """A score carrying no information about who responds must not be credited
    with targeting skill. This is the test that would catch a curve that
    flatters every ranking."""
    import random

    rng = random.Random(20260830)
    rows = mens_vs_holdout(hillstrom)
    coef = qini_coefficient(
        qini_scaled([{**r, "score": rng.random()} for r in rows])
    )
    assert abs(coef) < 0.05, f"random ranking scored {coef:+.3f}"


def test_oracle_ranking_beats_random_on_real_data(hillstrom):
    """Ranking by the realised outcome is not a legitimate model - it peeks -
    but it must score higher than noise, or the coefficient is not measuring
    ranking quality at all."""
    import random

    rng = random.Random(20260830)
    rows = mens_vs_holdout(hillstrom)
    rand = qini_coefficient(qini_scaled([{**r, "score": rng.random()} for r in rows]))
    oracle = qini_coefficient(
        qini_scaled([{**r, "score": r["outcome"]} for r in rows])
    )
    assert oracle > rand


def test_the_womens_arm_has_a_weaker_effect_than_the_mens_arm(hillstrom):
    """A published qualitative fact about this dataset, used as a second
    independent check that the arms are being read correctly."""
    def lift(segment: str) -> float:
        t = [float(r["visit"]) for r in hillstrom if r["segment"] == segment]
        c = [float(r["visit"]) for r in hillstrom if r["segment"] == "No E-Mail"]
        return sum(t) / len(t) - sum(c) / len(c)

    assert lift("Mens E-Mail") > lift("Womens E-Mail") > 0
