from datetime import datetime, timezone

from chukta.taxonomy import classify, is_hard_decline
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
