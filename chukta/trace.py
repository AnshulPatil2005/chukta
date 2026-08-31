"""Human-readable decision trace: what Chukta decided, and why.

    python -m chukta.trace

Prints one case per recoverability class, end to end:

    event -> diagnosis (which tier fired) -> chosen action -> every gate
          -> the exact request that would go to Razorpay

Nothing here touches the network. The executor renders the request and stops,
so the payload and its idempotency key are visible without a credential, an
account, or a live call that might behave differently on the day.

The last two sections demonstrate the properties that are hardest to show and
easiest to get wrong - what happens when a call has already been made, and what
happens when the gateway is failing.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .clock import to_ist
from .compose import Composer
from .execute import (
    CircuitBreaker,
    Credentials,
    Executor,
    IdempotencyLedger,
    idempotency_key,
)
from .gates import CaseState, blocking, evaluate
from .policy import PolicyEngine, load_policy
from .taxonomy import classify
from .types import (
    Action,
    ActionType,
    Channel,
    Customer,
    FailureEvent,
    MandateContext,
    MessageClass,
    PaymentType,
    RecoverabilityClass,
)

NOW = datetime(2026, 8, 30, 5, 30, tzinfo=timezone.utc)  # 11:00 IST
W = 76


def rule(char: str = "-") -> str:
    return char * W


def money(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


# One representative event per class. Amounts and rails are chosen so the
# interesting gates actually fire rather than all passing trivially.
CASES: list[tuple[str, FailureEvent]] = [
    (
        "Expired card. Retrying is pure loss - three guaranteed declines.",
        FailureEvent(
            event_id="evt_expired",
            customer_id="cust_01",
            amount_paise=129900,
            occurred_at=NOW,
            source="issuer",
            step="payment_authorization",
            reason="card_expired",
        ),
    ),
    (
        "Insufficient funds on the 28th. The same retry lands on the 1st.",
        FailureEvent(
            event_id="evt_funds",
            customer_id="cust_02",
            amount_paise=49900,
            occurred_at=NOW,
            source="issuer",
            step="payment_authorization",
            reason="insufficient_funds",
        ),
    ),
    (
        "Customer began authentication and dropped. Highest-uplift segment.",
        FailureEvent(
            event_id="evt_otp",
            customer_id="cust_03",
            amount_paise=89900,
            occurred_at=NOW,
            source="customer",
            step="payment_authentication",
            reason="incorrect_otp",
        ),
    ),
    (
        "Customer cancelled. Pursuing this is how you earn a complaint.",
        FailureEvent(
            event_id="evt_cancel",
            customer_id="cust_04",
            amount_paise=59900,
            occurred_at=NOW,
            source="customer",
            step="payment_authorization",
            reason="payment_cancelled",
        ),
    ),
    (
        "Our own malformed request. The customer did nothing - never contact.",
        FailureEvent(
            event_id="evt_config",
            customer_id="cust_05",
            amount_paise=39900,
            occurred_at=NOW,
            source="business",
            step="payment_initiation",
            reason="invalid_request_error",
        ),
    ),
    (
        "Mandate debit above the AFA-free limit, no AFA on file. RBI blocks it.",
        FailureEvent(
            event_id="evt_mandate",
            customer_id="cust_06",
            amount_paise=4500000,  # Rs 45,000
            occurred_at=NOW,
            source="bank",
            step="payment_authorization",
            reason="insufficient_funds",
            payment_type=PaymentType.MANDATE,
            mandate=MandateContext(afa_completed=False, category="general"),
        ),
    ),
    (
        "Unrecognised slug. Tier 2 catches it on the stable axes instead.",
        FailureEvent(
            event_id="evt_unknown",
            customer_id="cust_07",
            amount_paise=74900,
            occurred_at=NOW,
            source="gateway",
            step="payment_authorization",
            reason="acquirer_route_unavailable",
        ),
    ),
]


def trace_case(
    note: str,
    event: FailureEvent,
    engine: PolicyEngine,
    policy: dict,
    executor: Executor,
    composer: Composer | None = None,
) -> None:
    klass, evidence = classify(event)

    print(rule("="))
    print(f"  {event.event_id}   {money(event.amount_paise)}   {note}")
    print(rule("="))

    print("  EVENT")
    print(
        f"    source={event.source}  step={event.step}  reason={event.reason}"
        f"  rail={event.payment_type.value}"
    )

    print("  DIAGNOSIS")
    print(
        f"    class={klass.value}   tier={evidence['tier']}"
        f"   confidence={evidence['confidence']}   matched={evidence['matched']}"
    )
    if engine.is_hard_decline(klass):
        # Hard decline is about the INSTRUMENT, not the case. Re-presenting the
        # same card is a guaranteed decline, so the charge ladder is closed -
        # but asking the customer to fix the instrument, or telling the
        # merchant to fix its own request, is still the right move.
        print("    hard decline - no further charge attempts on this instrument")

    steps = engine.steps_for(klass)
    if not steps:
        print("  DECISION\n    no action defined for this class; case closed")
        print()
        return

    customer = Customer(customer_id=event.customer_id, dnd_registered=False)
    state = CaseState()

    for i in range(len(steps)):
        action = engine.decide(event, klass, i, NOW)
        if action is None:
            break
        when = action.scheduled_for or NOW

        print(f"  STEP {i}  ->  {action.type.value}")
        detail = [f"channel={action.channel.value}"]
        if action.message_class is not MessageClass.NONE:
            detail.append(f"class={action.message_class.value}")
        if action.message_frame:
            detail.append(f"frame={action.message_frame}")
        print(f"    {'  '.join(detail)}")
        print(f"    scheduled {to_ist(when).strftime('%a %d %b %H:%M')} IST")

        gates = evaluate(action, event, customer, klass, state, policy, when)
        print(f"    gates ({len(gates)} evaluated, none short-circuited)")
        for g in gates:
            mark = "pass" if g.passed else "BLOCK"
            line = f"      [{mark:>5}] {g.rule_id}"
            if g.detail:
                line += f"  {g.detail}"
            print(line)

        blocked = blocking(gates)
        if blocked:
            print(f"    -> NOT EXECUTED, blocked by {', '.join(blocked)}")
            print()
            break

        if action.type is ActionType.NO_ACTION:
            print("    -> deliberate no-op")
            print()
            break

        # The message a customer would actually receive. Composed here rather
        # than left as a frame label - a frame name in a demo tells a reviewer
        # nothing about what the person on the other end reads.
        if composer is not None and action.channel in (
            Channel.SMS, Channel.WHATSAPP, Channel.EMAIL
        ):
            msg = composer.compose(action.message_frame, {
                "name": "Priya",
                "amount": f"Rs {event.amount_rupees:,.0f}",
                "merchant": "Kirana Box",
                "link": "https://rzp.io/x/...",
                "deadline": to_ist(when).strftime("%d %b"),
                "service": "subscription",
            })
            print(f"    message ({msg.source})")
            print(f"      \"{msg.text}\"")
            if msg.was_blocked:
                print(f"      GUARD BLOCKED the model text: {', '.join(msg.guard_findings)}")

        result = executor.execute(action, event, customer, when, attempt_no=i)
        call = result.get("would_call", {})
        print(f"    -> would call {call.get('endpoint', 'n/a')}")
        print(f"       idempotency {result['idempotency_key'][:16]}...")
        if "degraded_from" in call:
            print(
                f"       DEGRADED from {call['degraded_from']}"
                f" - {call['degraded_because']}"
            )
        for k in ("amount", "reference_id", "notify"):
            if k in call.get("payload", {}):
                print(f"       {k}: {call['payload'][k]}")
        print()

        # Advance the case as if this had happened, so later steps see real
        # cooldown and budget state rather than a fresh slate.
        if action.channel in (Channel.SMS, Channel.WHATSAPP, Channel.EMAIL):
            state.contacts += 1
            state.last_contact_at = when
        if action.type is ActionType.RETRY_CHARGE:
            state.attempts += 1
            state.exposure_rupees += event.amount_rupees


def demo_idempotency() -> None:
    print(rule("="))
    print("  IDEMPOTENCY - the same logical action, issued twice")
    print(rule("="))

    event = CASES[2][1]
    action = Action(type=ActionType.PAYMENT_LINK, channel=Channel.SMS,
                    message_class=MessageClass.TRANSACTIONAL)

    class Stub:
        def __init__(self):
            self.calls = 0
            self.payment_link = self._E(self)
            self.payment = self._E(self)

        class _E:
            def __init__(self, p):
                self.p = p

            def create(self, payload):
                self.p.calls += 1
                return {"id": f"plink_{self.p.calls}", "status": "created"}

    stub = Stub()
    ex = Executor(
        dry_run=False,
        client=stub,
        credentials=Credentials("rzp_test_DEMO", "secret"),
        ledger=IdempotencyLedger("runs/_trace_idem.jsonl"),
    )

    first = ex.execute(action, event, Customer(event.customer_id), NOW)
    second = ex.execute(action, event, Customer(event.customer_id), NOW)
    print(f"    call 1 -> {first['status']}")
    print(f"    call 2 -> {second['status']}   (key collided, nothing re-sent)")
    print(f"    the gateway saw {stub.calls} request, not 2")
    print()

    print("    The harder case: request sent, response never came back.")
    print("    Re-issuing might double-charge; assuming success drops a real")
    print("    recovery. So it is escalated rather than guessed:")
    ledger = IdempotencyLedger("runs/_trace_inflight.jsonl")
    key = idempotency_key(event, action, 0)
    ledger.open_call(key, event.event_id, "payment_link", NOW)
    ex2 = Executor(
        dry_run=False,
        client=Stub(),
        credentials=Credentials("rzp_test_DEMO", "secret"),
        ledger=IdempotencyLedger("runs/_trace_inflight.jsonl"),
    )
    out = ex2.execute(action, event, Customer(event.customer_id), NOW)
    print(f"    -> {out['status']}: {out['detail']}")
    print()


def demo_circuit_breaker() -> None:
    print(rule("="))
    print("  CIRCUIT BREAKER - the gateway is failing")
    print(rule("="))

    cb = CircuitBreaker(threshold=3, cooldown=timedelta(minutes=5))
    for i in range(1, 4):
        cb.record_failure(NOW, "HTTP 502")
        print(f"    failure {i} -> state={cb.state}")
    print("    an agent that keeps retrying here turns an incident into an outage")

    later = NOW + timedelta(minutes=6)
    cb.check(later)
    print(f"    after cooldown -> state={cb.state}  (one probe allowed)")
    cb.record_failure(later, "HTTP 502")
    print(f"    probe fails    -> state={cb.state}")
    print(f"    trips recorded in the audit log: {len(cb.trips)}")
    print()


def main() -> int:
    # The idempotency demo is only honest if it starts from a clean ledger -
    # otherwise the second run of the trace shows "replayed" where the first
    # showed "ok", and the demo appears to contradict itself on camera.
    for stale in Path("runs").glob("_trace*.jsonl"):
        stale.unlink()

    policy = load_policy()
    engine = PolicyEngine(policy)
    executor = Executor(dry_run=True, ledger=IdempotencyLedger("runs/_trace.jsonl"))
    composer = Composer(policy)

    print()
    print(rule("="))
    print("  CHUKTA decision trace")
    print(f"  policy v{policy['version']}   "
          f"{to_ist(NOW).strftime('%a %d %b %Y %H:%M')} IST")
    print("  No network calls. The executor renders each request and stops.")
    print(rule("="))
    print()

    for note, event in CASES:
        trace_case(note, event, engine, policy, executor, composer)

    demo_idempotency()
    demo_circuit_breaker()

    seen = {classify(e)[0] for _, e in CASES}
    missing = [c.value for c in RecoverabilityClass if c not in seen]
    if missing:
        print(f"  (classes not shown above: {', '.join(missing)})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
