from datetime import datetime, timezone

import pytest

from chukta.taxonomy import (
    REASON_RULES,
    SOURCE_STEP_DEFAULTS,
    classify,
    is_hard_decline,
)
from chukta.types import (
    FailureEvent,
    MandateContext,
    PaymentType,
    RecoverabilityClass as RC,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def event(**kw) -> FailureEvent:
    base = dict(
        event_id="e1",
        customer_id="c1",
        amount_paise=49900,
        occurred_at=NOW,
        source="customer",
        step="payment_authorization",
        reason="insufficient_funds",
    )
    base.update(kw)
    return FailureEvent(**base)


def test_reason_tier_wins_and_reports_high_confidence():
    klass, ev = classify(event(reason="card_expired"))
    assert klass is RC.INSTRUMENT_INVALID
    assert ev["tier"] == "reason"
    assert ev["confidence"] == "high"


def test_unknown_reason_falls_back_to_source_step():
    """The whole point of the two-tier design: an unrecognised slug degrades to
    a coarser class rather than falling out of the taxonomy."""
    klass, ev = classify(
        event(reason="some_slug_razorpay_added_last_tuesday", source="gateway",
              step="payment_response")
    )
    assert klass is RC.TRANSIENT
    assert ev["tier"] == "source_step"
    assert ev["confidence"] == "medium"


def test_unrecognised_on_both_tiers_is_unknown_not_a_crash():
    klass, ev = classify(event(reason="???", source="martian", step="teleport"))
    assert klass is RC.UNKNOWN
    assert ev["confidence"] == "low"


def test_revoked_mandate_overrides_whatever_the_gateway_said():
    klass, ev = classify(
        event(
            reason="insufficient_funds",
            payment_type=PaymentType.MANDATE,
            mandate=MandateContext(revoked=True),
        )
    )
    assert klass is RC.MANDATE
    assert ev["matched"] == "mandate.revoked"


def test_merchant_config_is_diagnosed_from_our_own_bad_request():
    klass, _ = classify(event(source="business", step="payment_initiation",
                              reason="input_validation_failed"))
    assert klass is RC.MERCHANT_CONFIG


def test_hard_decline_set():
    assert is_hard_decline(RC.INSTRUMENT_INVALID)
    assert is_hard_decline(RC.CUSTOMER_INTENT)
    assert is_hard_decline(RC.MERCHANT_CONFIG)
    assert not is_hard_decline(RC.FUNDING)
    assert not is_hard_decline(RC.TRANSIENT)


def test_evidence_always_carries_the_raw_triplet():
    """The audit row must show what the gateway actually said, not our
    normalised view of it."""
    _, ev = classify(event(source="ISSUER", step="Payment_Authorization",
                           reason="INSUFFICIENT_FUNDS"))
    assert ev["raw"]["source"] == "issuer"
    assert ev["raw"]["reason"] == "insufficient_funds"


# -- verified against Razorpay's published error documentation ---------------
#
# 4 Sept 2026. The first version of this module was transcribed from memory and
# most of its slugs were wrong. These tests pin the corrections so the same
# class of error cannot creep back.

CORRECTED = {
    # what was there before  ->  what Razorpay actually documents
    "payment_timeout": "payment_timed_out",
    "collect_request_expired": "payment_collect_request_expired",
    "invalid_card_number": "card_number_invalid",
    "card_blocked": "debit_instrument_blocked",
    "issuer_down": "issuer_technical_error",
    "payment_limit_exceeded": "transaction_limit_exceeded",
}


@pytest.mark.parametrize("wrong,right", sorted(CORRECTED.items()))
def test_the_documented_slug_is_the_one_we_match(wrong, right):
    """The dangerous kind of error: a near-miss looks right, never matches, and
    every affected payment quietly degrades to tier 2."""
    assert right in REASON_RULES, f"{right} should be a tier-1 rule"
    assert wrong not in REASON_RULES, f"{wrong} is not a documented slug"


@pytest.mark.parametrize("slug", [
    "expired_card", "card_disabled", "network_error", "bank_down",
    "payment_declined_by_customer", "upi_collect_timeout", "mandate_revoked",
])
def test_invented_slugs_are_gone(slug):
    """These do not appear anywhere in Razorpay's documentation. Keeping them
    is false precision - they can never fire."""
    assert slug not in REASON_RULES


def test_razorpay_is_a_recognised_source():
    """Razorpay documents four source values: customer, business, gateway,
    razorpay. The first version omitted the last, so every failure Razorpay
    attributed to itself missed tier 2 and landed in UNKNOWN."""
    for step in ("payment_initiation", "payment_authentication",
                 "payment_authorization", "payment_response"):
        assert ("razorpay", step) in SOURCE_STEP_DEFAULTS


def test_all_four_documented_sources_are_covered():
    documented = {"customer", "business", "gateway", "razorpay"}
    covered = {src for src, _ in SOURCE_STEP_DEFAULTS}
    assert documented <= covered


def test_payment_failed_is_deliberately_not_a_tier_one_rule():
    """It is the most common reason Razorpay returns and it means nothing more
    than 'it failed'. A tier-1 rule would attach HIGH confidence to a string
    that carries no information."""
    from chukta.taxonomy import UNINFORMATIVE

    assert "payment_failed" in UNINFORMATIVE
    assert "payment_failed" not in REASON_RULES

    ev = FailureEvent("e", "c", 10000, NOW, "customer",
                      "payment_authentication", "payment_failed")
    klass, evidence = classify(ev)
    assert evidence["tier"] == "source_step"
    assert evidence["confidence"] == "medium"
    assert klass is RC.AUTH_DROPOFF
    assert "no diagnostic information" in evidence["note"]


def test_every_recoverability_class_is_reachable_from_a_real_slug():
    """A class no documented slug maps to would be dead policy."""
    reachable = set(REASON_RULES.values()) | set(SOURCE_STEP_DEFAULTS.values())
    for rc in RC:
        if rc is RC.UNKNOWN:
            continue
        assert rc in reachable, f"{rc.value} is unreachable"


def test_the_rule_table_is_substantially_larger_than_before():
    """Verification added coverage rather than just fixing names: ~30 slugs
    before, over 90 documented ones now. Every one is a payment that gets a
    high-confidence diagnosis instead of a coarse one."""
    assert len(REASON_RULES) > 80
