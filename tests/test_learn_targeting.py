"""Learned targeting vs hand-written priors.

Competing entries on this track fit models - gradient-boosted recovery
predictors, a LinUCB bandit. This module answers whether that helps HERE, and
the tests exist to keep the answer honest rather than to make it come out a
particular way.

The load-bearing property is the train/test split. A model scored on its own
training data always wins, which is why "our bandit converged" and "our policy
is good" are different claims.
"""

from __future__ import annotations

import pytest

from eval.learn_targeting import band, evaluate, fit, learned_score, report


def test_amount_bands_partition_the_range():
    assert band(1) == "small"
    assert band(499) == "small"
    assert band(500) == "medium"
    assert band(1999) == "medium"
    assert band(2000) == "large"
    assert band(10**7) == "large"


def test_fit_averages_uplift_per_cell():
    rows = [
        {"klass": "funding", "band": "small", "uplift_rupees": 100.0},
        {"klass": "funding", "band": "small", "uplift_rupees": 200.0},
        {"klass": "funding", "band": "small", "uplift_rupees": 300.0},
    ]
    assert fit(rows)[("funding", "small")] == pytest.approx(200.0)


def test_thin_cells_are_dropped_not_trusted():
    """Two observations is not a model. A cell that thin would otherwise hand
    a confident score to whatever noise it happened to see."""
    rows = [
        {"klass": "mandate", "band": "large", "uplift_rupees": 9999.0},
        {"klass": "mandate", "band": "large", "uplift_rupees": 9999.0},
    ]
    assert ("mandate", "large") not in fit(rows)


def test_an_unseen_cell_falls_back_to_the_mean_not_to_zero():
    """Zero means 'definitely not worth contacting', which is a far stronger
    claim than 'never observed'. Falling back to zero would silently suppress
    outreach on every class the fitter happened not to see."""
    model = {("funding", "small"): 100.0}
    score = learned_score(model, 42.0, {"klass": "never_seen", "band": "large"})
    assert score == 42.0


def test_the_evaluation_holds_out_its_test_seeds():
    """The whole point. If train and test overlap the comparison is worthless."""
    result = evaluate(n=60, train_seeds=[1, 2], test_seeds=[3, 4])
    assert not set(result["train_seeds"]) & set(result["test_seeds"])
    assert [p["seed"] for p in result["per_seed"]] == [3, 4]


def test_the_oracle_is_never_beaten_by_either_scorer():
    """Ranking by realised uplift is the ceiling by construction. If a scorer
    ever exceeded it, the Qini implementation would be wrong."""
    result = evaluate(n=60, train_seeds=[1, 2], test_seeds=[3, 4])
    for p in result["per_seed"]:
        assert p["oracle"] >= p["hand"] - 1e-9
        assert p["oracle"] >= p["learned"] - 1e-9


def test_the_report_states_a_verdict_and_its_spread():
    """An earlier version gated the verdict on win count alone and described a
    +0.167 mean improvement as 'no meaningful difference'. The report has to
    distinguish 'no difference' from 'higher mean, unreliable'."""
    text = report(evaluate(n=60, train_seeds=[1, 2], test_seeds=[3, 4]))
    assert "spread" in text
    assert "sd" in text
    # Some verdict is always reached.
    assert any(w in text for w in ("helps", "HURTS", "HIGHER MEAN", "No meaningful"))


def test_the_report_says_what_it_does_not_show():
    """The oracle is a ceiling, not a target, and convergence on one's own
    simulator is not evidence of policy quality. Both caveats stay."""
    text = report(evaluate(n=60, train_seeds=[1], test_seeds=[2]))
    assert "does NOT show" in text
    assert "oracle" in text
