"""Webhook ingestion.

This is the only place in the project that accepts input from outside, so it is
the only place with an adversary. The tests are written accordingly: most of
them are about what must NOT happen.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from chukta.taxonomy import classify
from chukta.types import PaymentType, RecoverabilityClass as RC
from chukta.webhook import (
    HANDLED,
    Receipt,
    WebhookReceiver,
    sign,
    to_failure_event,
)

SECRET = "whsec_test_abcdefghijklmnop"
NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def payment_failed(event_id="evt_001", reason="card_expired", amount=89900,
                   created=None, recurring=False) -> dict:
    ts = int((created or NOW).timestamp())
    entity = {
        "id": "pay_ABC123",
        "amount": amount,
        "currency": "INR",
        "customer_id": "cust_001",
        "created_at": ts,
        "error_code": "BAD_REQUEST_ERROR",
        "error_source": "issuer",
        "error_step": "payment_authorization",
        "error_reason": reason,
    }
    if recurring:
        entity["recurring"] = True
        entity["token_id"] = "token_XYZ"
    return {
        "id": event_id,
        "event": "payment.failed",
        "created_at": ts,
        "payload": {"payment": {"entity": entity}},
    }


def body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


@pytest.fixture
def receiver():
    return WebhookReceiver(secret=SECRET)


# -- signature ---------------------------------------------------------------


def test_a_correctly_signed_delivery_is_accepted(receiver):
    raw = body(payment_failed())
    out = receiver.receive(raw, sign(raw, SECRET), now=NOW)
    assert out.accepted
    assert out.failure is not None


def test_a_forged_signature_is_refused(receiver):
    raw = body(payment_failed())
    assert not receiver.receive(raw, "f" * 64, now=NOW).accepted


def test_a_missing_signature_is_refused(receiver):
    raw = body(payment_failed())
    assert not receiver.receive(raw, "", now=NOW).accepted


def test_the_wrong_secret_is_refused():
    raw = body(payment_failed())
    other = WebhookReceiver(secret="whsec_a_different_secret")
    assert not other.receive(raw, sign(raw, SECRET), now=NOW).accepted


def test_a_tampered_body_invalidates_the_signature(receiver):
    """The attack this exists to stop: capture a real delivery, change the
    amount, replay it."""
    original = payment_failed(amount=100)
    signature = sign(body(original), SECRET)

    tampered = payment_failed(amount=10_000_000)
    assert not receiver.receive(body(tampered), signature, now=NOW).accepted


def test_verification_is_over_raw_bytes_not_reserialised_json(receiver):
    """Re-encoding parsed JSON disagrees with the sender over key order and
    whitespace. Two byte-strings that parse identically must not share a
    signature."""
    payload = payment_failed()
    compact = json.dumps(payload, separators=(",", ":")).encode()
    spaced = json.dumps(payload, indent=2).encode()
    assert json.loads(compact) == json.loads(spaced)
    assert sign(compact, SECRET) != sign(spaced, SECRET)
    assert not receiver.receive(spaced, sign(compact, SECRET), now=NOW).accepted


def test_comparison_is_constant_time():
    """hmac.compare_digest, never ==. A byte-by-byte comparison leaks how much
    of a forged signature was correct through timing, and an attacker who can
    measure that can construct a valid one."""
    import inspect

    import chukta.webhook as mod

    src = inspect.getsource(mod.verify)
    assert "compare_digest" in src


# -- replay ------------------------------------------------------------------


def test_the_same_delivery_twice_is_refused(receiver):
    """A valid, correctly-signed request is still a request someone captured."""
    raw = body(payment_failed(event_id="evt_replay"))
    sig = sign(raw, SECRET)
    assert receiver.receive(raw, sig, now=NOW).accepted
    assert not receiver.receive(raw, sig, now=NOW).accepted


def test_a_stale_delivery_is_refused_even_when_correctly_signed(receiver):
    """A captured request stays validly signed forever. The timestamp is what
    stops it being replayable forever."""
    old = payment_failed(event_id="evt_old", created=NOW - timedelta(hours=2))
    raw = body(old)
    assert not receiver.receive(raw, sign(raw, SECRET), now=NOW).accepted


def test_a_delivery_from_the_future_is_refused(receiver):
    """Clock skew cuts both ways; an unbounded future window is a replay hole."""
    ahead = payment_failed(event_id="evt_future", created=NOW + timedelta(hours=2))
    raw = body(ahead)
    assert not receiver.receive(raw, sign(raw, SECRET), now=NOW).accepted


def test_distinct_events_are_both_accepted(receiver):
    for i in range(3):
        raw = body(payment_failed(event_id=f"evt_{i}"))
        assert receiver.receive(raw, sign(raw, SECRET), now=NOW).accepted


# -- what a rejection reveals ------------------------------------------------


def test_every_rejection_looks_the_same(receiver):
    """A bad signature and a stale event are useful things for an attacker to
    tell apart. They must not be distinguishable from the result."""
    raw = body(payment_failed(event_id="evt_x"))
    stale = body(payment_failed(event_id="evt_y", created=NOW - timedelta(days=1)))

    forged = receiver.receive(raw, "0" * 64, now=NOW)
    too_old = receiver.receive(stale, sign(stale, SECRET), now=NOW)
    receiver.receive(raw, sign(raw, SECRET), now=NOW)          # accept once
    replayed = receiver.receive(raw, sign(raw, SECRET), now=NOW)

    assert {forged.detail, too_old.detail, replayed.detail} == {"rejected"}


# -- malformed input ---------------------------------------------------------


@pytest.mark.parametrize("raw", [
    b"not json at all",
    b"[]",
    b"null",
    b'"a string"',
    b"{}",
    b"\xff\xfe\x00",
])
def test_malformed_but_correctly_signed_bodies_do_not_crash(receiver, raw):
    """Our own sender misbehaving must not take the endpoint down."""
    out = receiver.receive(raw, sign(raw, SECRET), now=NOW)
    assert isinstance(out, Receipt)
    assert not out.accepted


def test_an_unhandled_event_type_is_accepted_and_ignored(receiver):
    """Erroring would make Razorpay retry an event we will never handle."""
    payload = {"id": "evt_z", "event": "payout.processed",
               "created_at": int(NOW.timestamp())}
    raw = body(payload)
    out = receiver.receive(raw, sign(raw, SECRET), now=NOW)
    assert out.accepted and out.failure is None and out.detail == "ignored"


# -- conversion --------------------------------------------------------------


def test_the_error_triplet_survives_ingest_verbatim():
    """The whole diagnosis rests on these three, and the audit row should show
    what the gateway actually said rather than a normalised version."""
    ev = to_failure_event(payment_failed(reason="card_expired"))
    assert (ev.source, ev.step, ev.reason) == (
        "issuer", "payment_authorization", "card_expired")


def test_an_ingested_event_classifies_end_to_end():
    ev = to_failure_event(payment_failed(reason="card_expired"))
    klass, evidence = classify(ev)
    assert klass is RC.INSTRUMENT_INVALID
    assert evidence["tier"] == "reason"


def test_a_recurring_payment_becomes_a_mandate_event():
    ev = to_failure_event(payment_failed(recurring=True))
    assert ev.payment_type is PaymentType.MANDATE
    assert ev.mandate is not None


def test_a_non_failure_event_yields_no_failure_event():
    """A half-populated event would classify as UNKNOWN and look like a
    diagnosis rather than an absence of one."""
    assert to_failure_event({"event": "order.paid"}) is None


def test_handled_types_are_a_closed_set():
    assert "payment.failed" in HANDLED
    assert "payout.processed" not in HANDLED


# -- the HTTP endpoint -------------------------------------------------------


def _client(secret: str = SECRET):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import web.app as app_mod
    from chukta.webhook import WebhookReceiver

    app_mod.RECEIVER = WebhookReceiver(secret=secret)
    return TestClient(app_mod.app), app_mod


def test_the_endpoint_accepts_a_signed_delivery_and_diagnoses_it():
    client, _ = _client()
    raw = body(payment_failed(event_id="evt_http_1",
                              created=datetime.now(timezone.utc)))
    res = client.post("/api/webhook", content=raw,
                      headers={"X-Razorpay-Signature": sign(raw, SECRET)})
    assert res.status_code == 200
    out = res.json()
    assert out["accepted"] and out["handled"]
    assert out["diagnosis"]["klass"] == "instrument_invalid"


def test_the_endpoint_refuses_a_forged_delivery():
    client, _ = _client()
    raw = body(payment_failed(event_id="evt_http_2",
                              created=datetime.now(timezone.utc)))
    res = client.post("/api/webhook", content=raw,
                      headers={"X-Razorpay-Signature": "0" * 64})
    assert res.status_code == 400


def test_an_unconfigured_secret_refuses_everything():
    """A machine that was never meant to receive deliveries should not accept
    them. An empty secret must fail closed, not verify trivially."""
    client, _ = _client(secret="")
    raw = body(payment_failed(event_id="evt_http_3",
                              created=datetime.now(timezone.utc)))
    for sig in ("", sign(raw, ""), "0" * 64):
        res = client.post("/api/webhook", content=raw,
                          headers={"X-Razorpay-Signature": sig})
        assert res.status_code == 400


def test_the_endpoint_reads_raw_bytes_not_a_parsed_model():
    """If this handler ever takes a Pydantic model, FastAPI parses and
    validates attacker-controlled input BEFORE the signature is checked."""
    import inspect

    import web.app as app_mod

    src = inspect.getsource(app_mod.webhook)
    assert "await request.body()" in src


def test_a_replayed_delivery_is_refused_over_http():
    client, _ = _client()
    raw = body(payment_failed(event_id="evt_http_replay",
                              created=datetime.now(timezone.utc)))
    headers = {"X-Razorpay-Signature": sign(raw, SECRET)}
    assert client.post("/api/webhook", content=raw, headers=headers).status_code == 200
    assert client.post("/api/webhook", content=raw, headers=headers).status_code == 400
