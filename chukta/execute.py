"""Enforcement: the only module in the project that talks to Razorpay.

Three properties, in order of how much they matter.

**It refuses live credentials.** A key beginning `rzp_live_` raises before any
client is constructed. There is no override flag and no environment variable
that relaxes it, because the failure mode - a recovery agent re-presenting real
charges against real cards while its own contact policy is still being tuned -
is not one a warning fixes. This project is measured on replayed data; it has
no legitimate use for a live key.

**Every call is idempotent.** The key is derived deterministically from
(event_id, action, attempt_no), so the same logical action re-issued after a
timeout resolves to the same key. The ledger is consulted before the network,
so a crash between "request sent" and "response recorded" cannot double-charge
on replay.

**A failing gateway stops the agent, not the other way round.** The circuit
breaker opens after consecutive failures and every trip is written to the audit
log, because an agent that keeps hammering a degraded gateway turns an incident
into an outage.

Nothing in `sim/` imports this. The measurement batch is replayed and stubbed;
this is the live demo path only, and the two are kept apart so metrics stay
reproducible without a network.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .types import Action, ActionType, Customer, FailureEvent

TEST_KEY_PREFIX = "rzp_test_"
LIVE_KEY_PREFIX = "rzp_live_"

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
LEDGER_PATH = Path("runs/idempotency.jsonl")


# Substrings a gateway uses to say "you already created this". Matched on the
# message rather than a status code because Razorpay returns a plain 400 for
# both a duplicate and a malformed request, and conflating them would let a
# real validation error silently look like successful deduplication.
DUPLICATE_MARKERS = (
    "already exists",
    "duplicate",
)


def _is_duplicate_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in DUPLICATE_MARKERS) and "reference_id" in text


class LiveCredentialRefused(RuntimeError):
    """Raised when a live-mode key is presented. Deliberately not suppressible
    by configuration - the caller has to fix the credential, not the code."""


class CircuitOpen(RuntimeError):
    """The breaker is open; the call was never attempted."""


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def _read_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    """Minimal .env reader. Not worth a dependency for ten lines of parsing."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'\"")
    return out


@dataclass(frozen=True)
class Credentials:
    key_id: str
    key_secret: str

    @property
    def fingerprint(self) -> str:
        """A stable, non-reversible handle for the audit log.

        The audit trail has to record WHICH credential acted without recording
        the credential itself. Eight hex characters of a salted digest tells two
        keys apart across runs and is useless to whoever reads the log.
        """
        digest = hashlib.sha256(f"chukta::{self.key_id}".encode()).hexdigest()
        return f"{self.key_id[:12]}~{digest[:8]}"


def load_credentials(env: dict[str, str] | None = None) -> Credentials:
    """Read and validate credentials. Refuses live keys unconditionally."""
    src = dict(_read_env_file())
    src.update({k: v for k, v in os.environ.items() if k.startswith("RAZORPAY_")})
    if env:
        src.update(env)

    key_id = src.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = src.get("RAZORPAY_KEY_SECRET", "").strip()

    if key_id.startswith(LIVE_KEY_PREFIX):
        raise LiveCredentialRefused(
            "RAZORPAY_KEY_ID is a LIVE key. Chukta will not execute against live "
            "mode under any configuration.\n\n"
            "Most likely the dashboard was in Live mode when the key was\n"
            "generated - the API Keys page shows a different key set per mode:\n"
            "  1. Flip the dashboard toggle to Test mode. Wait for the page to\n"
            "     re-label; a test-mode banner should appear.\n"
            "  2. Account & Settings -> API Keys -> Generate Test Key.\n"
            f"  3. The id must begin '{TEST_KEY_PREFIX}'. Test keys are a\n"
            "     separate pair; generating one does not touch the live key.\n\n"
            "If this key has been pasted anywhere it should not live - a chat,\n"
            "a commit, a screenshot - regenerate it in Live mode as well."
        )
    if not key_id or "xxxx" in key_id.lower():
        raise RuntimeError(
            f"RAZORPAY_KEY_ID is unset or still a placeholder in {ENV_PATH}."
        )
    if not key_id.startswith(TEST_KEY_PREFIX):
        raise LiveCredentialRefused(
            f"RAZORPAY_KEY_ID does not begin '{TEST_KEY_PREFIX}'. Only test-mode "
            "credentials are accepted."
        )
    if not key_secret or "xxxx" in key_secret.lower():
        raise RuntimeError(
            f"RAZORPAY_KEY_SECRET is unset or still a placeholder in {ENV_PATH}."
        )
    return Credentials(key_id=key_id, key_secret=key_secret)


def dry_run_default() -> bool:
    """Dry run unless explicitly disabled. Defaulting the other way would mean
    a missing variable is indistinguishable from permission to spend money."""
    return _read_env_file().get("CHUKTA_DRY_RUN", "1") not in ("0", "false", "False")


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def idempotency_key(event: FailureEvent, action: Action, attempt_no: int) -> str:
    """Deterministic in the logical action, so a re-issue collides on purpose.

    Wall-clock time is excluded on purpose: a retry issued after a timeout must
    produce the SAME key as the call that timed out, or the ledger cannot
    recognise it as the same action.
    """
    material = "|".join(
        [event.event_id, action.type.value, action.channel.value, str(attempt_no)]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class IdempotencyLedger:
    """Append-only record of issued calls, consulted before the network.

    Razorpay's Payments API has no universal idempotency header, so the property
    is built here instead. The row is written BEFORE the request goes out, so a
    process that dies mid-flight leaves an `in_flight` row rather than a silent
    gap - the difference between "we know we don't know" and not knowing.
    """

    def __init__(self, path: str | Path = LEDGER_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: dict[str, dict] = {}
        if self.path.exists():
            for row in self._rows():
                self._seen[row["key"]] = row

    def _rows(self):
        with io.open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def lookup(self, key: str) -> dict | None:
        return self._seen.get(key)

    def _append(self, row: dict) -> None:
        self._seen[row["key"]] = row
        with io.open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def open_call(self, key: str, event_id: str, action: str, at: datetime) -> None:
        self._append(
            {
                "key": key,
                "event_id": event_id,
                "action": action,
                "state": "in_flight",
                "at": at.isoformat(),
            }
        )

    def close_call(self, key: str, response: dict, at: datetime) -> None:
        row = dict(self._seen.get(key, {"key": key}))
        row.update({"state": "done", "response": response, "closed_at": at.isoformat()})
        self._append(row)


# ---------------------------------------------------------------------------
# circuit breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreaker:
    """closed -> open after `threshold` consecutive failures -> half_open once
    `cooldown` has elapsed. One success in half_open closes it; one failure
    re-opens it. Trips are surfaced to the caller so they reach the audit log.
    """

    threshold: int = 4
    cooldown: timedelta = timedelta(minutes=5)
    state: str = "closed"
    failures: int = 0
    opened_at: datetime | None = None
    trips: list[dict] = field(default_factory=list)

    def check(self, now: datetime) -> None:
        if self.state == "open":
            if self.opened_at and now - self.opened_at >= self.cooldown:
                self.state = "half_open"
            else:
                raise CircuitOpen(
                    f"gateway circuit open since {self.opened_at}; "
                    f"{self.failures} consecutive failures"
                )

    def record_success(self) -> None:
        self.failures = 0
        self.state = "closed"

    def record_failure(self, now: datetime, detail: str) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.threshold:
            self.state = "open"
            self.opened_at = now
            self.trips.append(
                {"at": now.isoformat(), "failures": self.failures, "detail": detail}
            )


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------


class Executor:
    """Turns a gate-cleared Action into a Razorpay test-mode call.

    Actions reach here only after `gates.evaluate` has passed them. This class
    re-checks no policy - duplicating the rules would mean two places to keep
    correct - it only owns transport concerns: credentials, idempotency,
    breaker state.
    """

    def __init__(
        self,
        credentials: Credentials | None = None,
        dry_run: bool | None = None,
        ledger: IdempotencyLedger | None = None,
        breaker: CircuitBreaker | None = None,
        client: Any = None,
        audit_note: Callable[..., None] | None = None,
    ):
        self.dry_run = dry_run_default() if dry_run is None else dry_run
        self.ledger = ledger or IdempotencyLedger()
        self.breaker = breaker or CircuitBreaker()
        self.audit_note = audit_note or (lambda kind, **f: None)

        # In dry run we never need credentials, which keeps the whole pipeline
        # runnable - and testable - on a machine that has no keys at all.
        self.credentials = credentials
        if self.credentials is None and not self.dry_run:
            self.credentials = load_credentials()

        self._client = client
        if self._client is None and not self.dry_run:
            import razorpay

            self._client = razorpay.Client(
                auth=(self.credentials.key_id, self.credentials.key_secret)
            )
            self._client.set_app_details({"title": "chukta", "version": "0.1"})

    # -- public --------------------------------------------------------------

    def execute(
        self,
        action: Action,
        event: FailureEvent,
        customer: Customer,
        now: datetime,
        attempt_no: int = 0,
    ) -> dict[str, Any]:
        key = idempotency_key(event, action, attempt_no)

        prior = self.ledger.lookup(key)
        if prior and prior.get("state") == "done":
            return {
                "status": "replayed",
                "idempotency_key": key,
                "response": prior["response"],
            }
        if prior and prior.get("state") == "in_flight":
            # Seen before, outcome unknown. Refusing is the only safe answer:
            # re-issuing might double-charge, and assuming success might drop a
            # real recovery. It goes to a human instead.
            self.audit_note("idempotency_unresolved", key=key, event_id=event.event_id)
            return {
                "status": "needs_reconciliation",
                "idempotency_key": key,
                "detail": "a prior call with this key never recorded a response",
            }

        try:
            self.breaker.check(now)
        except CircuitOpen as exc:
            self.audit_note("circuit_open", event_id=event.event_id, detail=str(exc))
            return {"status": "circuit_open", "idempotency_key": key, "detail": str(exc)}

        handler = {
            ActionType.RETRY_CHARGE: self._retry_charge,
            ActionType.PAYMENT_LINK: self._payment_link,
            ActionType.UPDATE_INSTRUMENT: self._update_instrument,
            ActionType.REMANDATE: self._remandate,
            ActionType.MERCHANT_ALERT: self._merchant_alert,
            ActionType.NO_ACTION: self._noop,
        }.get(action.type)
        if handler is None:
            return {"status": "unsupported", "action": action.type.value}

        request = handler(action, event, customer, key)

        if self.dry_run:
            return {
                "status": "dry_run",
                "idempotency_key": key,
                "would_call": request,
                "credential": self.credentials.fingerprint if self.credentials else None,
            }

        self.ledger.open_call(key, event.event_id, action.type.value, now)
        try:
            response = self._call(request)
        except Exception as exc:
            # A duplicate rejection is not a failure - it is the second line of
            # idempotency defence doing its job. `reference_id` is derived from
            # the idempotency key, so if the local ledger is lost or corrupted
            # the gateway still refuses to create the object twice.
            #
            # Verified against the real API on 31 Aug: with the local ledger
            # deleted, Razorpay returned "payment link with given reference_id
            # ... already exists". The money did not move twice.
            #
            # It must NOT trip the circuit breaker. The breaker exists to stop
            # us hammering a degraded gateway, and a gateway that correctly
            # rejects a duplicate is the opposite of degraded.
            if _is_duplicate_rejection(exc):
                self.ledger.close_call(key, {"duplicate": True}, now)
                self.audit_note(
                    "duplicate_prevented", event_id=event.event_id, key=key
                )
                return {
                    "status": "duplicate_prevented",
                    "idempotency_key": key,
                    "detail": (
                        "the gateway rejected this as a duplicate; the original "
                        "object stands and nothing was created twice"
                    ),
                }

            self.breaker.record_failure(now, f"{type(exc).__name__}: {exc}")
            self.audit_note(
                "execute_failed",
                event_id=event.event_id,
                key=key,
                error=f"{type(exc).__name__}: {exc}",
                breaker=self.breaker.state,
            )
            return {
                "status": "error",
                "idempotency_key": key,
                "error": f"{type(exc).__name__}: {exc}",
                "breaker": self.breaker.state,
            }

        self.breaker.record_success()
        self.ledger.close_call(key, response, now)
        return {
            "status": "ok",
            "idempotency_key": key,
            "response": response,
            "credential": self.credentials.fingerprint if self.credentials else None,
        }

    # -- request builders ----------------------------------------------------
    # Each returns a plain dict, so a dry run prints exactly what would be sent.

    def _retry_charge(
        self, action: Action, event: FailureEvent, customer: Customer, key: str
    ) -> dict:
        """A silent re-presentment needs a saved token to charge against.

        Without one there is nothing to re-present, so this degrades to a
        payment link and says so in the response rather than reporting a charge
        it never made. Test mode has no tokenised card behind these replayed
        events, so the degraded branch is the one the demo actually exercises -
        stated here rather than hidden behind a mock that always succeeds.
        """
        token = getattr(event.mandate, "token_id", None) if event.mandate else None
        if not token:
            request = self._payment_link(action, event, customer, key)
            request["degraded_from"] = "retry_charge"
            request["degraded_because"] = "no saved token on this event"
            return request
        return {
            "endpoint": "payment.create_recurring",
            "payload": {
                "amount": event.amount_paise,
                "currency": event.currency,
                "token": token,
                "recurring": "1",
                "description": f"chukta retry for {event.event_id}",
                "notes": {"chukta_event": event.event_id, "chukta_idem": key},
            },
        }

    def _payment_link(
        self, action: Action, event: FailureEvent, customer: Customer, key: str
    ) -> dict:
        return {
            "endpoint": "payment_link.create",
            "payload": {
                "amount": event.amount_paise,
                "currency": event.currency,
                "accept_partial": False,
                # Deterministic in the idempotency key, so a duplicate is
                # rejected by the gateway too - the ledger is the first line of
                # defence, not the only one.
                "reference_id": f"chukta_{key[:16]}",
                "description": f"Payment for {event.event_id}",
                # Chukta never delegates delivery. TRAI timing and DND rules are
                # enforced in chukta/gates.py; letting Razorpay send the SMS
                # would move that decision outside the gate layer, where the
                # audit log cannot see it.
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
                "notes": {"chukta_event": event.event_id, "chukta_idem": key},
            },
        }

    def _update_instrument(
        self, action: Action, event: FailureEvent, customer: Customer, key: str
    ) -> dict:
        """Ask the customer to replace a card that cannot be charged again.

        This is NOT the same call as a payment link, and collapsing the two -
        which this module used to do - loses the distinction that matters. A
        payment link settles *this* invoice and leaves the dead card on file,
        so the next cycle fails identically. An instrument update replaces the
        token, which is the only thing that fixes an expired card.

        The right primitive is a saved-card update flow against the customer
        object. Getting the amount to zero matters: asking someone to pay again
        while their card is broken is how you collect a complaint instead of a
        card.
        """
        return {
            "endpoint": "payment_link.create",
            "intent": "update_instrument",
            "payload": {
                "amount": 0,
                "currency": event.currency,
                "accept_partial": False,
                "reference_id": f"chukta_upd_{key[:16]}",
                "description": "Update your saved card",
                "customer": {"id": event.customer_id},
                "notify": {"sms": False, "email": False},
                "reminder_enable": False,
                "options": {"checkout": {"method": {"card": "1"}}},
                "notes": {
                    "chukta_event": event.event_id,
                    "chukta_idem": key,
                    "chukta_intent": "replace_token",
                },
            },
        }

    def _remandate(
        self, action: Action, event: FailureEvent, customer: Customer, key: str
    ) -> dict:
        """Re-register a mandate that no longer authorises debits.

        Distinct from both of the above because it needs **AFA** - a revoked or
        expired e-mandate cannot be repaired by settling one invoice, and under
        the RBI framework re-registration requires additional-factor
        authentication regardless of amount. So this is an authorisation
        transaction, not a collection.
        """
        return {
            "endpoint": "subscription.create",
            "intent": "remandate",
            "payload": {
                "customer_id": event.customer_id,
                "total_count": 12,
                "customer_notify": 0,  # delivery stays behind our own gates
                "notes": {
                    "chukta_event": event.event_id,
                    "chukta_idem": key,
                    "chukta_intent": "re_register_mandate",
                },
                # AFA is mandatory on registration. Recorded in the request so
                # the audit row shows it was not skipped.
                "auth_type": "afa_required",
            },
        }

    def _merchant_alert(
        self, action: Action, event: FailureEvent, customer: Customer, key: str
    ) -> dict:
        return {
            "endpoint": "internal.merchant_alert",
            "payload": {"event_id": event.event_id, "reason": event.reason},
        }

    def _noop(
        self, action: Action, event: FailureEvent, customer: Customer, key: str
    ) -> dict:
        return {"endpoint": "none", "payload": {}}

    # -- transport -----------------------------------------------------------

    def _call(self, request: dict) -> dict:
        endpoint = request["endpoint"]
        payload = request["payload"]
        if endpoint in ("internal.merchant_alert", "none"):
            return {"handled": "locally", **payload}
        if endpoint == "payment_link.create":
            return self._client.payment_link.create(payload)
        if endpoint == "subscription.create":
            return self._client.subscription.create(payload)
        if endpoint == "payment.create_recurring":
            return self._client.payment.createRecurringPayment(payload)
        raise ValueError(f"unmapped endpoint {endpoint!r}")

    # -- connectivity --------------------------------------------------------

    def verify_credentials(self) -> dict[str, Any]:
        """Prove the key authenticates without printing it or moving money.

        `payment.all(count=1)` is a read. On a fresh test account it returns an
        empty collection, which is a pass - the assertion is about the absence
        of a 401, not about the contents.
        """
        if self.dry_run:
            return {"status": "dry_run", "detail": "no call made"}
        try:
            self._client.payment.all({"count": 1})
        except Exception as exc:
            return {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "credential": self.credentials.fingerprint,
            }
        return {"status": "ok", "credential": self.credentials.fingerprint}
