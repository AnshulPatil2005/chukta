"""Executor tests.

The first group is not about correctness, it is about blast radius: a live
credential must never reach a client constructor. Those tests exist because a
live key was in fact pasted into this project during the build, and a rule that
lives only in a README is not a rule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chukta.execute import (
    CircuitBreaker,
    CircuitOpen,
    Credentials,
    Executor,
    IdempotencyLedger,
    LiveCredentialRefused,
    idempotency_key,
    load_credentials,
)
from chukta.types import (
    Action,
    ActionType,
    Channel,
    Customer,
    FailureEvent,
    MandateContext,
    MessageClass,
    PaymentType,
)

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
TEST_ENV = {
    "RAZORPAY_KEY_ID": "rzp_test_ABCDEFGHIJKL",
    "RAZORPAY_KEY_SECRET": "abcdefghijklmnopqrstuvwx",
}
CUSTOMER = Customer(customer_id="cust_001")


def make_event(**kw) -> FailureEvent:
    base = dict(
        event_id="evt_001",
        customer_id="cust_001",
        amount_paise=249900,
        occurred_at=NOW,
        source="issuer",
        step="authorization",
        reason="payment_failed",
    )
    base.update(kw)
    return FailureEvent(**base)


def make_action(**kw) -> Action:
    base = dict(
        type=ActionType.PAYMENT_LINK,
        channel=Channel.SMS,
        message_class=MessageClass.TRANSACTIONAL,
    )
    base.update(kw)
    return Action(**base)


class _StubClient:
    def __init__(self):
        self.calls = 0
        self.payment_link = self._Endpoint(self)
        self.payment = self._Endpoint(self)

    class _Endpoint:
        def __init__(self, parent):
            self.parent = parent

        def create(self, payload):
            self.parent.calls += 1
            return {"id": "plink_stub", "status": "created"}

        def createRecurringPayment(self, payload):
            self.parent.calls += 1
            return {"id": "pay_stub", "status": "created"}


class _FailingClient(_StubClient):
    class _Endpoint(_StubClient._Endpoint):
        def create(self, payload):
            raise ConnectionError("gateway 502")


def _stub_executor(tmp_path, **kw) -> Executor:
    kw.setdefault("client", _StubClient())
    return Executor(
        dry_run=False,
        ledger=IdempotencyLedger(tmp_path / "idem.jsonl"),
        credentials=Credentials("rzp_test_ABCDEFGHIJKL", "secret"),
        **kw,
    )


# -- credential refusal ------------------------------------------------------


def test_live_key_is_refused():
    with pytest.raises(LiveCredentialRefused) as exc:
        load_credentials(
            {"RAZORPAY_KEY_ID": "rzp_live_ABCDEFGHIJKL", "RAZORPAY_KEY_SECRET": "x" * 24}
        )
    assert "LIVE" in str(exc.value)


def test_live_refusal_cannot_be_configured_away():
    """No flag, variable or argument permits live mode."""
    for extra in (
        {"CHUKTA_ALLOW_LIVE": "1"},
        {"CHUKTA_DRY_RUN": "0"},
        {"CHUKTA_FORCE": "true"},
    ):
        env = {
            "RAZORPAY_KEY_ID": "rzp_live_ABCDEFGHIJKL",
            "RAZORPAY_KEY_SECRET": "x" * 24,
            **extra,
        }
        with pytest.raises(LiveCredentialRefused):
            load_credentials(env)


def test_placeholder_is_rejected_but_not_as_a_live_key():
    with pytest.raises(RuntimeError) as exc:
        load_credentials(
            {"RAZORPAY_KEY_ID": "rzp_test_xxxxxxxxxxxx", "RAZORPAY_KEY_SECRET": "x" * 24}
        )
    assert not isinstance(exc.value, LiveCredentialRefused)


def test_unprefixed_key_is_refused():
    with pytest.raises(LiveCredentialRefused):
        load_credentials(
            {"RAZORPAY_KEY_ID": "some_other_key", "RAZORPAY_KEY_SECRET": "x" * 24}
        )


def test_test_key_loads():
    assert load_credentials(TEST_ENV).key_id.startswith("rzp_test_")


def test_fingerprint_does_not_leak_the_credential():
    creds = load_credentials(TEST_ENV)
    fp = creds.fingerprint
    assert TEST_ENV["RAZORPAY_KEY_SECRET"] not in fp
    assert creds.key_id not in fp  # truncated, never reproduced whole
    assert fp == load_credentials(TEST_ENV).fingerprint  # stable across runs


# -- idempotency -------------------------------------------------------------


def test_key_is_stable_across_reissue():
    """A timed-out call retried later must produce the same key, which is why
    wall-clock time is absent from the material."""
    event, action = make_event(), make_action()
    assert idempotency_key(event, action, 0) == idempotency_key(event, action, 0)


def test_key_differs_by_attempt_and_by_action():
    event = make_event()
    a, b = make_action(), make_action(type=ActionType.RETRY_CHARGE)
    assert idempotency_key(event, a, 0) != idempotency_key(event, a, 1)
    assert idempotency_key(event, a, 0) != idempotency_key(event, b, 0)


def test_completed_call_replays_instead_of_reissuing(tmp_path):
    ex = _stub_executor(tmp_path)
    first = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    second = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert first["status"] == "ok"
    assert second["status"] == "replayed"
    assert ex._client.calls == 1  # the network saw it exactly once


def test_in_flight_call_goes_to_a_human_not_to_a_retry(tmp_path):
    """The dangerous case: request sent, response never recorded. Re-issuing
    might double-charge; assuming success might drop a real recovery."""
    path = tmp_path / "idem.jsonl"
    key = idempotency_key(make_event(), make_action(), 0)
    IdempotencyLedger(path).open_call(key, "evt_001", "payment_link", NOW)

    ex = Executor(
        dry_run=False,
        ledger=IdempotencyLedger(path),
        client=_StubClient(),
        credentials=Credentials("rzp_test_ABCDEFGHIJKL", "secret"),
    )
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert out["status"] == "needs_reconciliation"
    assert ex._client.calls == 0


def test_ledger_survives_a_process_restart(tmp_path):
    path = tmp_path / "idem.jsonl"
    key = idempotency_key(make_event(), make_action(), 0)
    IdempotencyLedger(path).close_call(key, {"id": "plink_1"}, NOW)
    assert IdempotencyLedger(path).lookup(key)["state"] == "done"


# -- circuit breaker ---------------------------------------------------------


def test_breaker_opens_after_consecutive_failures():
    cb = CircuitBreaker(threshold=3)
    for _ in range(3):
        cb.record_failure(NOW, "502")
    assert cb.state == "open"
    with pytest.raises(CircuitOpen):
        cb.check(NOW)


def test_success_resets_the_failure_count():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure(NOW, "502")
    cb.record_failure(NOW, "502")
    cb.record_success()
    cb.record_failure(NOW, "502")
    assert cb.state == "closed"


def test_breaker_half_opens_then_reopens_on_one_failure():
    cb = CircuitBreaker(threshold=2, cooldown=timedelta(minutes=5))
    cb.record_failure(NOW, "502")
    cb.record_failure(NOW, "502")
    later = NOW + timedelta(minutes=6)
    cb.check(later)
    assert cb.state == "half_open"
    cb.record_failure(later, "502")
    assert cb.state == "open"


def test_open_breaker_short_circuits_execute(tmp_path):
    cb = CircuitBreaker(threshold=1)
    cb.record_failure(NOW, "502")
    ex = _stub_executor(tmp_path, breaker=cb)
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert out["status"] == "circuit_open"
    assert ex._client.calls == 0


def test_gateway_failure_is_reported_not_raised(tmp_path):
    ex = _stub_executor(tmp_path, client=_FailingClient())
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert out["status"] == "error"
    assert ex.breaker.failures == 1


# -- request shape -----------------------------------------------------------


def test_dry_run_needs_no_credentials_and_makes_no_call(tmp_path):
    ex = Executor(dry_run=True, ledger=IdempotencyLedger(tmp_path / "i.jsonl"))
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert out["status"] == "dry_run"
    assert out["would_call"]["endpoint"] == "payment_link.create"
    assert out["credential"] is None


def test_chukta_never_delegates_delivery_to_the_gateway(tmp_path):
    """TRAI timing and DND are enforced in gates.py. If Razorpay sent the SMS,
    that decision would happen outside the gate layer, where the audit log
    cannot see it."""
    ex = Executor(dry_run=True, ledger=IdempotencyLedger(tmp_path / "i.jsonl"))
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    payload = out["would_call"]["payload"]
    assert payload["notify"] == {"sms": False, "email": False}
    assert payload["reminder_enable"] is False


def test_retry_without_a_token_degrades_and_says_so(tmp_path):
    ex = Executor(dry_run=True, ledger=IdempotencyLedger(tmp_path / "i.jsonl"))
    event = make_event(payment_type=PaymentType.MANDATE, mandate=MandateContext())
    out = ex.execute(
        make_action(type=ActionType.RETRY_CHARGE, channel=Channel.NONE),
        event,
        CUSTOMER,
        NOW,
    )
    call = out["would_call"]
    assert call["endpoint"] == "payment_link.create"
    assert call["degraded_from"] == "retry_charge"


# -- duplicate rejection is a defence, not a failure -------------------------
#
# Verified against the real Razorpay test API on 31 Aug 2026: with the local
# ledger deleted, a second create returned "payment link with given
# reference_id ... already exists". Two independent defences held - the ledger,
# and a reference_id derived from the same idempotency key.

class _DuplicateClient(_StubClient):
    class _Endpoint(_StubClient._Endpoint):
        def create(self, payload):
            raise ValueError(
                "BadRequestError: payment link with given reference_id: "
                "chukta_85af3f52136d60b2 already exists. Please create a payment "
                "link with a different reference_id"
            )


def test_a_gateway_duplicate_rejection_is_reported_as_prevention(tmp_path):
    ex = _stub_executor(tmp_path, client=_DuplicateClient())
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert out["status"] == "duplicate_prevented"
    assert "duplicate" in out["detail"]


def test_a_duplicate_rejection_does_not_trip_the_breaker(tmp_path):
    """The breaker exists to stop us hammering a degraded gateway. One that
    correctly refuses a duplicate is the opposite of degraded - counting it as
    a failure would open the breaker on a system that is working perfectly."""
    ex = _stub_executor(tmp_path, client=_DuplicateClient())
    ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert ex.breaker.failures == 0
    assert ex.breaker.state == "closed"


def test_a_genuine_gateway_error_still_trips_the_breaker(tmp_path):
    """The duplicate carve-out must not swallow real failures."""
    ex = _stub_executor(tmp_path, client=_FailingClient())
    out = ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert out["status"] == "error"
    assert ex.breaker.failures == 1


def test_a_validation_error_is_not_mistaken_for_a_duplicate(tmp_path):
    """Razorpay returns a plain 400 for both a duplicate and a malformed
    request. Treating every 400 as deduplication would let real validation bugs
    look like success, so the match requires `reference_id` too."""
    from chukta.execute import _is_duplicate_rejection

    assert not _is_duplicate_rejection(ValueError("BadRequestError: amount must be at least 100"))
    assert not _is_duplicate_rejection(ValueError("customer already exists"))
    assert _is_duplicate_rejection(
        ValueError("payment link with given reference_id: x already exists")
    )


def test_a_duplicate_closes_the_ledger_row(tmp_path):
    """It must not leave an in_flight row - the outcome IS known: the original
    object stands and nothing was created twice."""
    path = tmp_path / "idem.jsonl"
    ex = Executor(
        dry_run=False,
        ledger=IdempotencyLedger(path),
        client=_DuplicateClient(),
        credentials=Credentials("rzp_test_ABCDEFGHIJKL", "secret"),
    )
    key = idempotency_key(make_event(), make_action(), 0)
    ex.execute(make_action(), make_event(), CUSTOMER, NOW)
    assert IdempotencyLedger(path).lookup(key)["state"] == "done"
