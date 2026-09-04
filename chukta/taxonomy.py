"""Diagnosis layer: Razorpay's error triplet -> recoverability class.

Razorpay ships a structured failure taxonomy on every failed payment:

    source  - customer | business | gateway | razorpay
    step    - payment_initiation | payment_authentication
              | payment_authorization | payment_response
    reason  - a slug, e.g. "card_expired"

The mapping below is two-tier:

  1. `REASON_RULES` matches on the reason slug. Precise, high confidence.
  2. `SOURCE_STEP_DEFAULTS` matches on (source, step) alone. Coarser, but those
     axes are small and stable, so an unrecognised reason still lands in a
     sensible class instead of falling off a cliff.

That fallback is the point. An unknown reason downgrades confidence, it does not
break diagnosis - and the tier that fired is written to the audit row so a
reviewer can see which classifications rest on which evidence.

VERIFIED 4 Sept 2026 against Razorpay's published error documentation
(docs/errors/payments/list, /cards, /upi). Every slug below appears in those
pages verbatim. That verification mattered: the first version of this file was
transcribed from memory and **most of its slugs were wrong** -

    payment_timeout            -> payment_timed_out
    collect_request_expired    -> payment_collect_request_expired
    invalid_card_number        -> card_number_invalid
    card_blocked               -> debit_instrument_blocked
    issuer_down                -> issuer_technical_error
    payment_limit_exceeded     -> transaction_limit_exceeded
    expired_card, card_disabled, network_error, bank_down  -> do not exist

Near-misses like `payment_timeout` are the dangerous kind: they look right, they
never match, and every affected payment quietly degrades to tier 2 while the
audit row still reads "medium confidence" rather than "we had the wrong string".

The `source` vocabulary was wrong too. It previously listed issuer/bank/network
and omitted **razorpay**, so any failure Razorpay attributed to itself missed
tier 2 entirely and landed in UNKNOWN.
"""

from __future__ import annotations

from .types import FailureEvent, RecoverabilityClass as RC

# --- tier 1: reason slug -----------------------------------------------------
#
# Grouped by what can be DONE about the failure, which is the only question the
# policy asks. Two slugs meaning almost the same thing to a human can sit in
# different classes if the right intervention differs.

REASON_RULES: dict[str, RC] = {
    # -- funding: the money was not there. Timing is the whole intervention. --
    "insufficient_funds": RC.FUNDING,
    "transaction_limit_exceeded": RC.FUNDING,
    "transaction_daily_limit_exceeded": RC.FUNDING,
    "transaction_frequency_limit_exceeded": RC.FUNDING,
    "transaction_daily_count_exceeded": RC.FUNDING,
    "credit_limit_exceeded": RC.FUNDING,
    "mcc_amount_limit_exceeded": RC.FUNDING,
    "funds_blocked_by_mandate": RC.FUNDING,

    # -- instrument is unusable: retrying it is pure loss ---------------------
    "card_expired": RC.INSTRUMENT_INVALID,
    "incorrect_card_expiry_date": RC.INSTRUMENT_INVALID,
    "card_number_invalid": RC.INSTRUMENT_INVALID,
    "card_type_invalid": RC.INSTRUMENT_INVALID,
    "card_not_enrolled": RC.INSTRUMENT_INVALID,
    "card_network_not_enabled": RC.INSTRUMENT_INVALID,
    "debit_instrument_blocked": RC.INSTRUMENT_INVALID,
    "debit_instrument_inactive": RC.INSTRUMENT_INVALID,
    "international_transaction_not_allowed": RC.INSTRUMENT_INVALID,
    "payment_method_not_enabled": RC.INSTRUMENT_INVALID,
    "invalid_vpa": RC.INSTRUMENT_INVALID,
    "vpa_resolution_failed": RC.INSTRUMENT_INVALID,
    "transaction_on_vpa_restricted": RC.INSTRUMENT_INVALID,
    "bank_account_invalid": RC.INSTRUMENT_INVALID,
    "bank_account_validation_failed": RC.INSTRUMENT_INVALID,
    "beneficiary_account_does_not_exist": RC.INSTRUMENT_INVALID,
    "beneficiary_account_dormant": RC.INSTRUMENT_INVALID,
    "credit_limit_expired": RC.INSTRUMENT_INVALID,
    "credit_limit_inactive": RC.INSTRUMENT_INVALID,
    "credit_limit_not_approved": RC.INSTRUMENT_INVALID,
    "credit_not_permitted": RC.INSTRUMENT_INVALID,
    "pin_not_set": RC.INSTRUMENT_INVALID,
    "user_not_registered_for_netbanking": RC.INSTRUMENT_INVALID,
    "psp_not_registered": RC.INSTRUMENT_INVALID,
    "psp_app_not_supported": RC.INSTRUMENT_INVALID,
    # A risk decline is a hard decline: re-presenting the same instrument will
    # be flagged the same way.
    "payment_risk_check_failed": RC.INSTRUMENT_INVALID,

    # -- customer began authenticating and did not finish ---------------------
    # The highest-uplift segment: they wanted to pay. Do not re-serve the same
    # friction - move to a rail with fewer steps.
    "authentication_failed": RC.AUTH_DROPOFF,
    "incorrect_otp": RC.AUTH_DROPOFF,
    "otp_expired": RC.AUTH_DROPOFF,
    "otp_attempts_exceeded": RC.AUTH_DROPOFF,
    "incorrect_cvv": RC.AUTH_DROPOFF,
    "incorrect_pin": RC.AUTH_DROPOFF,
    "incorrect_atm_pin": RC.AUTH_DROPOFF,
    "pin_attempts_exceeded": RC.AUTH_DROPOFF,
    "incorrect_card_details": RC.AUTH_DROPOFF,
    "incorrect_cardholder_name": RC.AUTH_DROPOFF,
    "payment_timed_out": RC.AUTH_DROPOFF,
    "payment_session_expired": RC.AUTH_DROPOFF,
    "payment_collect_request_expired": RC.AUTH_DROPOFF,
    "collect_request_pending": RC.AUTH_DROPOFF,
    "request_timed_out": RC.AUTH_DROPOFF,
    "verification_failed": RC.AUTH_DROPOFF,

    # -- customer actively declined. Pursuing this earns a complaint ----------
    "payment_cancelled": RC.CUSTOMER_INTENT,
    "payment_declined": RC.CUSTOMER_INTENT,
    "debit_declined": RC.CUSTOMER_INTENT,
    "card_declined": RC.CUSTOMER_INTENT,
    "authorisation_declined_by_psp": RC.CUSTOMER_INTENT,
    "user_not_eligible": RC.CUSTOMER_INTENT,

    # -- infrastructure blipped. The instrument is fine; try again ------------
    "gateway_technical_error": RC.TRANSIENT,
    "bank_technical_error": RC.TRANSIENT,
    "issuer_technical_error": RC.TRANSIENT,
    "upi_app_technical_error": RC.TRANSIENT,
    "bank_not_available": RC.TRANSIENT,
    "bank_cutoff_in_progress": RC.TRANSIENT,
    "psp_not_available": RC.TRANSIENT,
    "psp_app_not_available": RC.TRANSIENT,
    "server_error": RC.TRANSIENT,
    "invalid_response_from_gateway": RC.TRANSIENT,
    "payment_declined_due_to_high_traffic": RC.TRANSIENT,
    "payment_pending": RC.TRANSIENT,
    "payment_pending_approval": RC.TRANSIENT,
    "deemed_transaction": RC.TRANSIENT,
    "duplicate_rrn_found": RC.TRANSIENT,
    "credit_failed": RC.TRANSIENT,

    # -- the authority to charge is what broke. Re-mandate, do not retry ------
    "mandate_creation_declined": RC.MANDATE,
    "mandate_creation_expired": RC.MANDATE,
    "mandate_creation_failed": RC.MANDATE,
    "mandate_creation_timeout": RC.MANDATE,
    "reqauth_mandate_not_acknowledged": RC.MANDATE,
    "recurring_payment_not_enabled": RC.MANDATE,
    "upi_autopay_not_supported_on_psp": RC.MANDATE,

    # -- our own bug, not the customer's. Never contact them about it ---------
    "input_validation_failed": RC.MERCHANT_CONFIG,
    "invalid_amount": RC.MERCHANT_CONFIG,
    "invalid_currency": RC.MERCHANT_CONFIG,
    "invalid_request": RC.MERCHANT_CONFIG,
    "invalid_order_id": RC.MERCHANT_CONFIG,
    "invalid_email": RC.MERCHANT_CONFIG,
    "invalid_mobile_number": RC.MERCHANT_CONFIG,
    "mobile_number_invalid": RC.MERCHANT_CONFIG,
    "invalid_device": RC.MERCHANT_CONFIG,
    "invalid_user_details": RC.MERCHANT_CONFIG,
    "order_already_paid": RC.MERCHANT_CONFIG,
    "order_amount_mismatch": RC.MERCHANT_CONFIG,
    "order_payment_method_mismatch": RC.MERCHANT_CONFIG,
    "amount_less_than_minimum_amount": RC.MERCHANT_CONFIG,
    "merchant_not_activated": RC.MERCHANT_CONFIG,
    "live_mode_not_enabled": RC.MERCHANT_CONFIG,
    "bank_not_enabled": RC.MERCHANT_CONFIG,
    "upi_collect_not_enabled": RC.MERCHANT_CONFIG,
    "upi_intent_not_enabled": RC.MERCHANT_CONFIG,
    "collect_on_mcc_blocked": RC.MERCHANT_CONFIG,
    "emi_plan_unavailable": RC.MERCHANT_CONFIG,
    "emi_greater_than_max_amount": RC.MERCHANT_CONFIG,
    "duplicate_request": RC.MERCHANT_CONFIG,
    "duplicate_refund_id": RC.MERCHANT_CONFIG,
    "refund_limit_crossed": RC.MERCHANT_CONFIG,
    "record_not_found": RC.MERCHANT_CONFIG,
    "capture_failed": RC.MERCHANT_CONFIG,
    "payment_amount_tampered": RC.MERCHANT_CONFIG,
    "mismatch_in_transaction_details": RC.MERCHANT_CONFIG,
    "compliance_violation": RC.MERCHANT_CONFIG,
}

# Documented slugs deliberately NOT given a tier-1 rule.
#
# `payment_failed` is the most common reason Razorpay returns and it carries no
# information at all - it means "it failed". Mapping it would attach HIGH
# confidence to a string that says nothing. Letting it fall to tier 2 uses the
# source and step, which do carry signal, and records medium confidence, which
# is the truth.
UNINFORMATIVE = frozenset({"payment_failed"})

# --- tier 2: (source, step) --------------------------------------------------
#
# `source` values are the four Razorpay documents: customer, business, gateway,
# razorpay. The legacy issuer/bank/network keys are retained because older
# payloads and some rails still carry them - an extra key that never matches is
# harmless, a missing one sends a real payment to UNKNOWN.

SOURCE_STEP_DEFAULTS: dict[tuple[str, str], RC] = {
    # the merchant's own request was wrong
    ("business", "payment_initiation"): RC.MERCHANT_CONFIG,
    ("business", "payment_authentication"): RC.MERCHANT_CONFIG,
    ("business", "payment_authorization"): RC.MERCHANT_CONFIG,
    ("business", "payment_response"): RC.MERCHANT_CONFIG,

    # the customer did or did not do something
    ("customer", "payment_initiation"): RC.CUSTOMER_INTENT,
    ("customer", "payment_authentication"): RC.AUTH_DROPOFF,
    ("customer", "payment_authorization"): RC.FUNDING,
    ("customer", "payment_response"): RC.AUTH_DROPOFF,

    # gateway-side: infrastructure
    ("gateway", "payment_initiation"): RC.TRANSIENT,
    ("gateway", "payment_authentication"): RC.TRANSIENT,
    ("gateway", "payment_authorization"): RC.TRANSIENT,
    ("gateway", "payment_response"): RC.TRANSIENT,

    # Razorpay attributing the failure to itself. Absent from the first version
    # of this file, which sent every such payment to UNKNOWN.
    ("razorpay", "payment_initiation"): RC.TRANSIENT,
    ("razorpay", "payment_authentication"): RC.TRANSIENT,
    ("razorpay", "payment_authorization"): RC.TRANSIENT,
    ("razorpay", "payment_response"): RC.TRANSIENT,

    # legacy / rail-specific source values
    ("issuer", "payment_authentication"): RC.AUTH_DROPOFF,
    ("issuer", "payment_authorization"): RC.FUNDING,
    ("issuer", "payment_response"): RC.TRANSIENT,
    ("bank", "payment_authentication"): RC.TRANSIENT,
    ("bank", "payment_authorization"): RC.TRANSIENT,
    ("bank", "payment_response"): RC.TRANSIENT,
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
        return REASON_RULES[reason], {
            "tier": "reason",
            "confidence": "high",
            "matched": reason,
            "raw": {"source": source, "step": step, "reason": reason},
        }

    default = SOURCE_STEP_DEFAULTS.get((source, step))
    if default is not None:
        note = "reason slug not recognised; classified on stable axes"
        if reason in UNINFORMATIVE:
            note = (
                f"'{reason}' carries no diagnostic information; classified on "
                "source and step, which do"
            )
        return default, {
            "tier": "source_step",
            "confidence": "medium",
            "matched": f"{source}/{step}",
            "note": note,
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
