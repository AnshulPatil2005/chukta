"""Message composition tests.

The guard is the part that matters. Everything else here degrades gracefully;
the guard is what stands between a generative model and a customer who is
already annoyed about a failed payment.

Note what is NOT tested, because nothing here can test it: whether better copy
inside a frame actually converts better. The sensitivity sweep says frames are
the load-bearing assumption in the whole project, and settling that needs live
traffic, not unit tests.
"""

from __future__ import annotations

import pytest

from chukta.compose import (
    COERCIVE_PATTERNS,
    CoercionGuard,
    Composer,
    Message,
    TemplateComposer,
)
from chukta.policy import load_policy

FACTS = {
    "name": "Priya",
    "amount": "Rs 499",
    "merchant": "Kirana Box",
    "link": "https://rzp.io/x/abc",
    "deadline": "5 Sept",
    "service": "weekly delivery",
}


@pytest.fixture
def policy():
    return load_policy()


@pytest.fixture
def guard():
    return CoercionGuard()


# -- the guard ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We will take legal action if you do not pay.", "threat of legal action"),
        ("Pay now or we will sue you.", "threat of legal action"),
        ("This is theft and will be prosecuted.", "implication of criminality"),
        ("We will send this to debt collection.", "threat of escalation to collections"),
        ("This will affect your credit score.", "threat of credit consequences"),
        ("We will contact your employer about this.",
         "threat of contacting family or employer"),
        ("Do not be irresponsible about your bills.", "shaming language"),
        ("FINAL WARNING - pay immediately.", "manufactured urgency"),
        ("Your account will be blacklisted.", "disproportionate threat"),
    ],
)
def test_guard_catches_coercive_copy(guard, text, expected):
    assert expected in guard.check(text)


def test_guard_passes_ordinary_copy(guard):
    clean = (
        "Hi Priya, your Rs 499 payment to Kirana Box did not go through. "
        "You can complete it here: https://rzp.io/x/abc"
    )
    assert guard.check(clean) == []


def test_guard_is_case_insensitive(guard):
    assert guard.check("LEGAL ACTION will follow") != []


def test_guard_blocks_overlong_sms(guard):
    assert any("exceeds 320" in f for f in guard.check("x" * 400))


def test_deliberate_choice_frame_forbids_consequence_language(guard):
    """policy.yaml gives this frame a `must_not_include` contract. The whole
    point of the frame is that non-payment is a choice, so implying a penalty
    contradicts it."""
    text = "You may face a penalty if this is not resolved."
    assert guard.check(text, frame="deliberate_choice") != []
    # The same sentence is not flagged by the baseline patterns alone.
    assert not any("forbidden by frame" in f for f in guard.check(text))


def test_every_prohibition_in_the_policy_has_a_pattern(policy):
    """policy.yaml lists four prohibitions in prose. If someone adds a fifth,
    this fails until a matching pattern exists - otherwise the policy would
    promise something the guard does not enforce."""
    labels = " ".join(label for _, label in COERCIVE_PATTERNS).lower()
    for prohibition in policy["prohibited_language"]:
        head = prohibition.split()[0].lower().rstrip(",")
        assert head in labels or any(
            word in labels for word in prohibition.lower().split()
        ), f"no guard pattern covers: {prohibition}"


# -- templates ---------------------------------------------------------------


def test_every_frame_in_the_policy_has_a_template(policy):
    from chukta.compose import TEMPLATES

    assert set(policy["frames"]) <= set(TEMPLATES), (
        "a frame in policy.yaml has no template; it would silently fall back "
        "to generic copy"
    )


def test_all_shipped_templates_pass_the_guard(policy, guard):
    """The guard runs over template output too. Templates are checked in today
    and could be edited tomorrow."""
    tc = TemplateComposer()
    for frame in policy["frames"]:
        assert guard.check(tc.compose(frame, FACTS), frame) == [], frame


def test_templates_are_deterministic():
    tc = TemplateComposer()
    assert tc.compose("social_norm", FACTS) == tc.compose("social_norm", FACTS)


def test_missing_facts_do_not_crash():
    assert "there" in TemplateComposer().compose("social_norm", {})


def test_unknown_frame_falls_back_rather_than_raising():
    out = TemplateComposer().compose("no_such_frame", FACTS)
    assert "Rs 499" in out


# -- composer wiring ---------------------------------------------------------


def test_without_a_credential_the_template_path_runs(policy):
    c = Composer(policy, use_model=False)
    msg = c.compose("simplification", FACTS)
    assert msg.source == "template"
    assert not msg.was_blocked


def test_model_output_is_used_when_it_passes_the_guard(policy):
    class Stub:
        def compose(self, frame, facts, spec):
            return "Hi Priya, quick one - your Rs 499 Kirana Box payment " \
                   "bounced. Fix it here: https://rzp.io/x/abc"

    msg = Composer(policy, model_composer=Stub(), use_model=True).compose(
        "simplification", FACTS
    )
    assert msg.source == "model"


def test_coercive_model_output_is_replaced_not_sent(policy):
    """The load-bearing test. A model that writes a threat must not reach a
    customer, and the substitution has to be visible in the audit row."""
    class Coercive:
        def compose(self, frame, facts, spec):
            return "Pay Rs 499 now or we will take legal action and inform " \
                   "your employer."

    msg = Composer(policy, model_composer=Coercive(), use_model=True).compose(
        "simplification", FACTS
    )
    assert msg.was_blocked
    assert msg.source == "template_after_guard_block"
    assert "legal action" not in msg.text
    assert "threat of legal action" in msg.guard_findings
    assert "threat of contacting family or employer" in msg.guard_findings
    # The rejected text is retained for review rather than discarded.
    assert msg.model_text_rejected is not None


def test_model_outage_degrades_to_template(policy):
    """A model outage must not stop recovery."""
    class Broken:
        def compose(self, frame, facts, spec):
            raise ConnectionError("api down")

    msg = Composer(policy, model_composer=Broken(), use_model=True).compose(
        "social_norm", FACTS
    )
    assert msg.source == "template"
    assert "Rs 499" in msg.text


def test_a_template_edited_into_something_unsendable_fails_loudly(policy):
    """If the fallback itself is coercive there is no safer text left, so this
    raises rather than sending."""
    class BadTemplates(TemplateComposer):
        def compose(self, frame, facts):
            return "We will take legal action."

    c = Composer(policy, template_composer=BadTemplates(), use_model=False)
    with pytest.raises(ValueError, match="coercion guard"):
        c.compose("simplification", FACTS)


def test_the_composer_cannot_change_what_happens(policy):
    """Composition returns prose and nothing else. If a Message ever carries an
    action, channel or schedule, the model has been handed a decision."""
    msg = Composer(policy, use_model=False).compose("simplification", FACTS)
    assert isinstance(msg, Message)
    assert set(vars(msg)) == {
        "text", "frame", "source", "guard_findings", "model_text_rejected"
    }
