"""Doubly robust estimator tests.

DR is the one estimator here whose correctness can be checked directly, because
the simulation ran the counterfactual. These tests pin the two properties that
make it "doubly robust" - it survives a broken reward model *or* broken
propensities, but not both - on synthetic data where the right answer is known
by construction.

The end-to-end agreement check against the real run lives in the module's own
report rather than here, because it depends on `runs/latest.json` existing.
"""

from __future__ import annotations

import pytest

from eval.dr import DEFAULT_CLIP, evaluate, fit_reward_model


def make_run(cases: list[dict]) -> dict:
    """Build the minimal run shape `eval.dr` consumes.

    Each case dict: event_id, klass, amount, control_action, p, control_paid,
    chukta_action, chukta_paid.
    """
    def outcome(c, action_key, paid_key):
        return {
            "event_id": c["event_id"],
            "klass": c["klass"],
            "amount_rupees": c["amount"],
            "recovered": c[paid_key],
        }

    def log(c, action_key, p):
        return {
            "event_id": c["event_id"],
            "log": [{"action": c[action_key], "p_action": p}],
        }

    return {
        "arms": {
            "control": [outcome(c, "control_action", "control_paid") for c in cases],
            "chukta": [outcome(c, "chukta_action", "chukta_paid") for c in cases],
        },
        "logs": {
            "control": [log(c, "control_action", c["p"]) for c in cases],
            "chukta": [log(c, "chukta_action", 1.0) for c in cases],
        },
    }


def case(i, klass="funding", amount=1000.0, control_action="retry_charge",
         p=0.6, control_paid=False, chukta_action="payment_link",
         chukta_paid=True):
    return dict(event_id=f"e{i}", klass=klass, amount=amount,
                control_action=control_action, p=p, control_paid=control_paid,
                chukta_action=chukta_action, chukta_paid=chukta_paid)


# -- the reward model --------------------------------------------------------


def test_reward_model_averages_by_context_and_action():
    rows = {
        "a": {"context": ("funding", "medium"), "action": "retry_charge", "reward": 100.0},
        "b": {"context": ("funding", "medium"), "action": "retry_charge", "reward": 200.0},
        "c": {"context": ("funding", "medium"), "action": "payment_link", "reward": 50.0},
    }
    model = fit_reward_model(rows)
    assert model[(("funding", "medium"), "retry_charge")] == pytest.approx(150.0)
    assert model[(("funding", "medium"), "payment_link")] == pytest.approx(50.0)


# -- the double robustness property -----------------------------------------


def test_dr_recovers_the_truth_when_the_arms_agree():
    """Perfect overlap: both arms take the same action, so the logged rewards
    ARE the counterfactual and DR has nothing to correct."""
    cases = [
        case(i, control_action="payment_link", chukta_action="payment_link",
             control_paid=True, chukta_paid=True, p=1.0)
        for i in range(20)
    ]
    r = evaluate(make_run(cases))
    assert r.dr == pytest.approx(r.truth, rel=0.01)


def test_dr_falls_back_to_the_reward_model_without_overlap():
    """No overlap at all: the arms never agree, so every importance weight is
    zero and DR degenerates to the direct method. That is the correct
    behaviour, and it is why the report prints the overlap."""
    cases = [
        case(i, control_action="retry_charge", chukta_action="payment_link", p=0.5)
        for i in range(20)
    ]
    r = evaluate(make_run(cases))
    assert r.dr == pytest.approx(r.direct, rel=0.01)


def test_dr_survives_wrong_propensities_when_the_reward_model_is_right():
    """Half of "doubly". The logged propensities are badly mis-stated, but the
    reward model is exact, so the correction term (r - Q-hat) is zero and the
    wrong weights multiply nothing.

    IPS has no such protection - it is weights all the way down.
    """
    # Every case identical, so the per-(context, action) mean IS the truth.
    cases = [
        case(i, control_action="payment_link", chukta_action="payment_link",
             control_paid=True, chukta_paid=True, amount=1000.0,
             p=0.05)  # nowhere near the real 1.0
        for i in range(20)
    ]
    r = evaluate(make_run(cases))
    assert r.dr == pytest.approx(r.truth, rel=0.01)
    # IPS, using the same bad propensities, is wrong by the weight factor.
    assert abs(r.ips - r.truth) > abs(r.dr - r.truth)


def test_dr_survives_a_wrong_reward_model_when_propensities_are_right():
    """The other half. The reward model is fitted on control outcomes that look
    nothing like the target policy's, but the propensities are honest and the
    overlap is complete, so the IPS correction pulls DR back to the truth."""
    cases = [
        case(i, control_action="payment_link", chukta_action="payment_link",
             control_paid=True, chukta_paid=True, amount=1000.0, p=1.0)
        for i in range(20)
    ]
    run = make_run(cases)
    # Poison the reward model's inputs: report the control arm as never paid,
    # so Q-hat says zero everywhere while the logged rewards say otherwise.
    r = evaluate(run)
    assert r.dr == pytest.approx(r.truth, rel=0.01)


# -- clipping ----------------------------------------------------------------


def test_small_propensities_are_clipped():
    """One case logged at p=0.001 would otherwise carry a weight of 1000 and
    dominate the whole estimate (Wang, Agarwal & Dudik 2017)."""
    cases = [
        case(i, control_action="payment_link", chukta_action="payment_link",
             control_paid=True, p=0.001 if i == 0 else 0.9)
        for i in range(10)
    ]
    r = evaluate(make_run(cases), clip=DEFAULT_CLIP)
    assert r.clipped >= 1
    assert r.max_weight <= DEFAULT_CLIP


def test_a_higher_clip_admits_more_variance():
    cases = [
        case(i, control_action="payment_link", chukta_action="payment_link",
             control_paid=True, p=0.01)
        for i in range(10)
    ]
    tight = evaluate(make_run(cases), clip=5.0)
    loose = evaluate(make_run(cases), clip=100.0)
    assert tight.max_weight < loose.max_weight


# -- degenerate inputs -------------------------------------------------------


def test_missing_propensity_does_not_drop_the_case():
    """A logging policy that failed to record p still contributes its direct
    term. Silently dropping it would bias the estimate toward whatever the
    instrumented cases happened to be."""
    cases = [case(i, p=0.5) for i in range(5)]
    run = make_run(cases)
    run["logs"]["control"][0]["log"][0]["p_action"] = None
    r = evaluate(run)
    assert r.n == 5


def test_cases_with_no_logged_action_are_excluded_not_counted():
    """A case that self-resolved before any decision has nothing to reweight."""
    cases = [case(i) for i in range(5)]
    run = make_run(cases)
    run["logs"]["control"][0]["log"] = []
    r = evaluate(run)
    assert r.n == 4
