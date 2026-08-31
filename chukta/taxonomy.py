"""Diagnosis layer: Razorpay's error triplet -> recoverability class.

Razorpay already ships a structured failure taxonomy on every failed payment:

    source  - customer | business | bank | gateway | issuer | network | NA
    step    - payment_initiation | payment_authentication
              | payment_authorization | payment_response
    reason  - a free-form-ish slug, e.g. "payment_failed"

The mapping below is deliberately two-tier:

  1. `REASON_RULES` matches on the reason slug. Precise, but the slug set is
     large, undocumented in full, and changes. Treat every entry here as
     needing verification against the live error-code docs.
  2. `SOURCE_STEP_DEFAULTS` matches on (source, step) alone. Coarser, but the
     axis values are stable and small, so an unrecognised reason still lands in
     a sensible class instead of falling off a cliff.

That fallback is the point. An unknown reason downgrades our confidence, it
does not break diagnosis - and the confidence level is written to the audit
row so a reviewer can see which tier fired.

NOTE (verify before submission): the reason slugs are transcribed from
Razorpay's public error documentation and have NOT been confirmed against a
live test-mode account. Confirm each one and delete this note. Any slug that
turns out to be wrong degrades to tier 2 rather than misclassifying, which is
why the tiering exists.
"""

from __future__ import annotations

from .types import FailureEvent, RecoverabilityClass as RC

# --- tier 1: reason slug -----------------------------------------------------

REASON_RULES: dict[str, RC] = {
    # funding
    "insufficient_funds": RC.FUNDING,
    "payment_limit_exceeded": RC.FUNDING,
    "card_limit_exceeded": RC.FUNDING,
    # instrument no longer usable - retrying is pure loss
    "card_expired": RC.INSTRUMENT_INVALID,
    "expired_card": RC.INSTRUMENT_INVALID,
    "card_blocked": RC.INSTRUMENT_INVALID,
    "card_disabled": RC.INSTRUMENT_INVALID,
    "invalid_card_number": RC.INSTRUMENT_INVALID,
    "international_transaction_not_allowed": RC.INSTRUMENT_INVALID,
    "payment_method_not_enabled": RC.INSTRUMENT_INVALID,
    # customer started but did not finish authentication
    "incorrect_otp": RC.AUTH_DROPOFF,
    "authentication_failed": RC.AUTH_DROPOFF,
    "payment_timeout": RC.AUTH_DROPOFF,
    "collect_request_expired": RC.AUTH_DROPOFF,
    "upi_collect_timeout": RC.AUTH_DROPOFF,
    # customer actively declined
    "payment_cancelled": RC.CUSTOMER_INTENT,
    "payment_declined_by_customer": RC.CUSTOMER_INTENT,
    # infrastructure
    "gateway_technical_error": RC.TRANSIENT,
    "server_error": RC.TRANSIENT,
    "network_error": RC.TRANSIENT,
    "issuer_down": RC.TRANSIENT,
    "bank_down": RC.TRANSIENT,
    # mandates
    "mandate_revoked": RC.MANDATE,
    "mandate_cancelled": RC.MANDATE,
    "mandate_limit_exceeded": RC.MANDATE,
    "pre_debit_notification_failed": RC.MANDATE,
    "mandate_not_found": RC.MANDATE,
    # our own bug, not the customer's problem
    "input_validation_failed": RC.MERCHANT_CONFIG,
    "invalid_amount": RC.MERCHANT_CONFIG,
    "invalid_request": RC.MERCHANT_CONFIG,
    "order_already_paid": RC.MERCHANT_CONFIG,
}

# --- tier 2: (source, step) --------------------------------------------------

SOURCE_STEP_DEFAULTS: dict[tuple[str, str], RC] = {
    ("business", "payment_initiation"): RC.MERCHANT_CONFIG,
    ("business", "payment_authorization"): RC.MERCHANT_CONFIG,
    ("business", "payment_response"): RC.MERCHANT_CONFIG,
    ("customer", "payment_initiation"): RC.CUSTOMER_INTENT,
    ("customer", "payment_authentication"): RC.AUTH_DROPOFF,
    ("customer", "payment_authorization"): RC.FUNDING,
    ("customer", "payment_response"): RC.AUTH_DROPOFF,
    ("issuer", "payment_authentication"): RC.AUTH_DROPOFF,
    ("issuer", "payment_authorization"): RC.FUNDING,
    ("issuer", "payment_response"): RC.TRANSIENT,
    ("bank", "payment_authentication"): RC.TRANSIENT,
    ("bank", "payment_authorization"): RC.TRANSIENT,
    ("bank", "payment_response"): RC.TRANSIENT,
    ("gateway", "payment_initiation"): RC.TRANSIENT,
    ("gateway", "payment_authentication"): RC.TRANSIENT,
    ("gateway", "payment_authorization"): RC.TRANSIENT,
    ("gateway", "payment_response"): RC.TRANSIENT,
    ("network", "payment_initiation"): RC.TRANSIENT,
    ("network", "payment_authentication"): RC.TRANSIENT,
    ("network", "payment_authorization"): RC.TRANSIENT,
    ("network", "payment_response"): RC.TRANSIENT,
}

# Classes from which no further charge attempt can succeed. Feeds the
# HARD_DECLINE stopping rule.
HARD_DECLINE_CLASSES = frozenset(
    {RC.INSTRUMENT_INVALID, RC.CUSTOMER_INTENT, RC.MERCHANT_CONFIG}
)


def classify(event: FailureEvent) -> tuple[RC, dict]:
    """Return the recoverability class and the evidence behind it.

    The evidence dict is written verbatim into the audit row, so a reviewer can
    reconstruct why this class was chosen without rerunning anything.
    """
    reason = (event.reason or "").strip().lower()
    source = (event.source or "").strip().lower()
    step = (event.step or "").strip().lower()

    # A revoked mandate is a mandate problem no matter what the gateway said
    # about the underlying charge.
    if event.mandate is not None and event.mandate.revoked:
        return RC.MANDATE, {
            "tier": "mandate_state",
            "confidence": "high",
            "matched": "mandate.revoked",
            "raw": {"source": source, "step": step, "reason": reason},
        }

    if reason in REASON_RULES:
        klass = REASON_RULES[reason]
        # A funding or transient failure on a mandate rail is still a mandate
        # decision: the intervention set is different.
        if event.payment_type.value == "mandate" and klass in (RC.FUNDING, RC.TRANSIENT):
            pass  # keep the finer class; policy.yaml branches on payment_type
        return klass, {
            "tier": "reason",
            "confidence": "high",
            "matched": reason,
            "raw": {"source": source, "step": step, "reason": reason},
        }

    default = SOURCE_STEP_DEFAULTS.get((source, step))
    if default is not None:
        return default, {
            "tier": "source_step",
            "confidence": "medium",
            "matched": f"{source}/{step}",
            "note": "reason slug not recognised; classified on stable axes",
            "raw": {"source": source, "step": step, "reason": reason},
        }

    return RC.UNKNOWN, {
        "tier": "none",
        "confidence": "low",
        "matched": None,
        "note": "no rule matched; policy will route to conservative default",
        "raw": {"source": source, "step": step, "reason": reason},
    }


def is_hard_decline(klass: RC) -> bool:
    return klass in HARD_DECLINE_CLASSES
