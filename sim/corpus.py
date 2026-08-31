"""Failure corpus.

Razorpay test mode will not naturally produce a diverse spread of failure
reasons - you can trigger a decline, but not a bank outage, an expired-card
decline and a revoked mandate on demand. So the batch used for measurement is
generated here, against Razorpay's real (source, step, reason) axes.

Which events were genuinely triggered in test mode and which were replayed
from this corpus is disclosed in the run manifest. Do not blur that line.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from chukta.types import FailureEvent, MandateContext, PaymentType

from .population import SimCustomer

# (source, step, reason, payment_type_bias) with the share of the batch each
# takes. Shares are shaped to look like an Indian merchant's failure mix:
# funding and authentication drop-off dominate, merchant bugs are rare.
FAILURE_MIX = [
    (0.28, "issuer", "payment_authorization", "insufficient_funds", 0.45),
    (0.16, "customer", "payment_authentication", "incorrect_otp", 0.05),
    (0.09, "customer", "payment_response", "collect_request_expired", 0.05),
    (0.11, "gateway", "payment_response", "gateway_technical_error", 0.30),
    (0.07, "bank", "payment_authorization", "bank_down", 0.30),
    (0.08, "issuer", "payment_authorization", "card_expired", 0.55),
    (0.04, "issuer", "payment_authorization", "card_blocked", 0.30),
    (0.06, "customer", "payment_initiation", "payment_cancelled", 0.02),
    (0.05, "bank", "payment_authorization", "mandate_revoked", 1.0),
    (0.03, "issuer", "payment_authorization", "mandate_limit_exceeded", 1.0),
    (0.02, "business", "payment_initiation", "input_validation_failed", 0.20),
    # An unrecognised slug, on purpose: the tier-2 fallback must carry it.
    (0.01, "gateway", "payment_authorization", "acquirer_route_unavailable", 0.20),
]

MANDATE_CATEGORIES = ("general", "general", "general", "sip", "insurance",
                      "credit_card_bill")


def build_corpus(people: list[SimCustomer], start, seed: int = 20260829) -> list[FailureEvent]:
    """One at-risk event per customer, staggered across a week."""
    rng = np.random.default_rng(seed + 1)
    weights = np.array([row[0] for row in FAILURE_MIX], dtype=float)
    weights = weights / weights.sum()

    events: list[FailureEvent] = []
    for i, person in enumerate(people):
        idx = int(rng.choice(len(FAILURE_MIX), p=weights))
        _, source, step, reason, mandate_bias = FAILURE_MIX[idx]

        is_mandate = bool(rng.random() < mandate_bias)
        # Log-normal amounts: mostly small-ticket, a thin tail of large ones
        # that will exercise the AFA and human-approval gates.
        amount_rupees = float(np.clip(rng.lognormal(mean=6.6, sigma=1.05), 49, 180000))

        mandate = None
        if is_mandate:
            category = str(rng.choice(MANDATE_CATEGORIES))
            # Most merchants do serve the pre-debit notice; some do not, and
            # those cases must be blocked rather than silently retried.
            notified = bool(rng.random() < 0.86)
            mandate = MandateContext(
                afa_completed=bool(rng.random() < 0.34),
                pre_debit_notified_at=(
                    start - timedelta(hours=float(rng.uniform(24, 96)))
                    if notified
                    else None
                ),
                category=category,
                revoked=reason in ("mandate_revoked",),
            )

        events.append(
            FailureEvent(
                event_id=f"evt_{i:04d}",
                customer_id=person.customer_id,
                amount_paise=int(round(amount_rupees * 100)),
                occurred_at=start + timedelta(hours=float(rng.uniform(0, 168))),
                source=source,
                step=step,
                reason=reason,
                code="GATEWAY_ERROR" if source == "gateway" else "BAD_REQUEST_ERROR",
                payment_type=PaymentType.MANDATE if is_mandate else PaymentType.ONE_TIME,
                mandate=mandate,
            )
        )
    return events
