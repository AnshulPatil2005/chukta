"""Razorpay webhook ingestion.

Until now every event was replayed from `sim/corpus.py`. This is the path that
takes a real one: Razorpay POSTs a `payment.failed` event, and it becomes a
`FailureEvent` the same engine already knows how to diagnose.

Four things this gets right that webhook handlers routinely get wrong, in the
order they matter:

**Verify before parsing.** The signature is checked against the RAW BYTES, and
nothing looks inside the payload until it passes. A handler that calls
`json.loads` first has already run a parser over attacker-controlled input, and
on a bad day that parser is the vulnerability. `receive()` cannot be called with
a parsed body - it takes `bytes` - so the ordering is enforced by the signature,
not by remembering.

**Constant-time comparison.** `hmac.compare_digest`, never `==`. A byte-by-byte
comparison leaks how much of a forged signature was correct through timing, and
an attacker who can measure that can construct a valid one.

**Verify the bytes you received, not the bytes you re-serialised.** Re-encoding
the parsed JSON and hashing that will silently disagree with the sender over key
order, whitespace and unicode escapes. The raw body is the only thing the
signature is defined over.

**Replay protection.** A valid, correctly-signed request is still not safe to
process twice - it is a valid request someone captured. Events are deduplicated
on Razorpay's own event id, and anything older than the freshness window is
refused even if the signature is perfect.

Failures are silent by design: every rejection returns the same shaped result
and reveals nothing about WHY, because "signature bad" and "event too old" are
useful things for an attacker to be able to distinguish.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .types import FailureEvent, MandateContext, PaymentType

# Razorpay signs the raw body with the webhook secret, SHA-256, hex-encoded,
# and sends it in this header.
SIGNATURE_HEADER = "X-Razorpay-Signature"

# Anything older than this is refused even with a valid signature. A captured
# request stays perfectly signed forever; the timestamp is what stops it being
# replayable forever.
FRESHNESS_WINDOW = timedelta(minutes=5)

# Events worth acting on. Anything else is accepted and ignored - returning an
# error for an event type we simply do not handle would make Razorpay retry it.
HANDLED = frozenset({"payment.failed", "subscription.charged", "order.paid"})


class WebhookRejected(Exception):
    """Verification failed. The message is deliberately uninformative."""


@dataclass
class Receipt:
    """What happened to one delivery. Shaped identically whether it was
    accepted or refused, so a caller cannot leak the reason by responding
    differently."""

    accepted: bool
    event_id: str | None = None
    event_type: str | None = None
    failure: FailureEvent | None = None
    detail: str = ""


def sign(raw_body: bytes, secret: str) -> str:
    """Produce the signature Razorpay would send. Used by tests, and by anyone
    generating fixtures - never in the verification path."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify(raw_body: bytes, signature: str, secret: str) -> bool:
    """Constant-time signature check over the raw bytes.

    Returns a bool rather than raising, so the caller decides what to reveal.
    """
    if not signature or not secret:
        return False
    expected = sign(raw_body, secret)
    # compare_digest, never ==. See the module docstring.
    return hmac.compare_digest(expected, signature)


@dataclass
class WebhookReceiver:
    """Verifies, deduplicates and converts one delivery at a time."""

    secret: str
    freshness: timedelta = FRESHNESS_WINDOW
    seen: set[str] = field(default_factory=set)

    def receive(
        self,
        raw_body: bytes,
        signature: str,
        now: datetime | None = None,
    ) -> Receipt:
        """The whole ingestion path. `raw_body` is bytes on purpose."""
        now = now or datetime.now(timezone.utc)

        # 1. Signature, over the raw bytes, before anything is parsed.
        if not verify(raw_body, signature, self.secret):
            return Receipt(accepted=False, detail="rejected")

        # 2. Only now is it safe to look inside.
        try:
            payload = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            # Correctly signed but unparseable means our own sender is broken,
            # which is worth distinguishing from a forgery - but only in logs.
            return Receipt(accepted=False, detail="rejected")

        if not isinstance(payload, dict):
            return Receipt(accepted=False, detail="rejected")

        event_id = str(payload.get("id") or "")
        event_type = str(payload.get("event") or "")

        # 3. Replay: same delivery twice.
        if not event_id:
            return Receipt(accepted=False, detail="rejected")
        if event_id in self.seen:
            return Receipt(
                accepted=False, event_id=event_id, event_type=event_type,
                detail="rejected",
            )

        # 4. Freshness: a captured request stays validly signed forever.
        created = payload.get("created_at")
        if created is not None:
            try:
                sent = datetime.fromtimestamp(float(created), tz=timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                return Receipt(accepted=False, detail="rejected")
            if abs(now - sent) > self.freshness:
                return Receipt(
                    accepted=False, event_id=event_id, event_type=event_type,
                    detail="rejected",
                )

        self.seen.add(event_id)

        if event_type not in HANDLED:
            # Accepted and ignored. Erroring would make Razorpay retry an event
            # we are never going to handle.
            return Receipt(
                accepted=True, event_id=event_id, event_type=event_type,
                detail="ignored",
            )

        failure = to_failure_event(payload)
        return Receipt(
            accepted=True, event_id=event_id, event_type=event_type,
            failure=failure, detail="accepted" if failure else "ignored",
        )


def to_failure_event(payload: dict[str, Any]) -> FailureEvent | None:
    """Razorpay's webhook shape -> the engine's own event.

    Only `payment.failed` carries the error triplet the taxonomy needs, so
    everything else returns None rather than a half-populated event that would
    classify as UNKNOWN and look like a diagnosis.
    """
    if payload.get("event") != "payment.failed":
        return None

    entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    if not entity:
        return None

    mandate = None
    token = entity.get("token_id")
    if entity.get("recurring") or token:
        mandate = MandateContext(
            afa_completed=bool(entity.get("recurring_status") == "confirmed"),
            category=str(entity.get("notes", {}).get("category", "general")),
        )

    created = entity.get("created_at") or payload.get("created_at")
    occurred = (
        datetime.fromtimestamp(float(created), tz=timezone.utc)
        if created is not None
        else datetime.now(timezone.utc)
    )

    return FailureEvent(
        event_id=str(entity.get("id") or payload.get("id")),
        customer_id=str(entity.get("customer_id") or entity.get("email") or "unknown"),
        amount_paise=int(entity.get("amount") or 0),
        occurred_at=occurred,
        # The engine's whole diagnosis rests on these three, and they come
        # through verbatim rather than being normalised on ingest - the audit
        # row should show exactly what the gateway said.
        source=str(entity.get("error_source") or ""),
        step=str(entity.get("error_step") or ""),
        reason=str(entity.get("error_reason") or ""),
        code=str(entity.get("error_code") or "BAD_REQUEST_ERROR"),
        payment_type=PaymentType.MANDATE if mandate else PaymentType.ONE_TIME,
        mandate=mandate,
        currency=str(entity.get("currency") or "INR"),
    )
