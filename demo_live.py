"""One case, end to end, against the real Razorpay test API.

    python demo_live.py                 # dry run, prints what it would send
    python demo_live.py --live          # actually calls Razorpay test mode

This is the demo path, and it is deliberately the ONLY thing in the repository
that touches the network. Every number in `eval/` still comes from replayed
data with no credential - see docs/adr/0009-live-demo-path.md for why that
separation is kept rather than collapsed now that keys exist.

What it shows, in order:

    a real failure event
      -> diagnosis, and which tier fired
      -> the intervention the policy chose
      -> every gate, with its verdict
      -> a REAL payment link created in test mode
      -> the audit row, hash-chained

The gates run before the call, not after. If any of them blocks, no request is
made and the script says which rule stopped it - which is the point.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from chukta.audit import AuditLog, verify
from chukta.clock import to_ist
from chukta.compose import Composer
from chukta.execute import Executor, IdempotencyLedger, load_credentials
from chukta.gates import CaseState, blocking, evaluate
from chukta.policy import PolicyEngine, load_policy
from chukta.replies import apply_to_case, parse
from chukta.taxonomy import classify
from chukta.types import ActionType, Customer, FailureEvent

W = 72


def rule(c: str = "-") -> str:
    return c * W


# A real-shaped failure: customer started authentication and dropped. Highest
# uplift segment, and the one where a payment link is the right answer.
EVENT = FailureEvent(
    event_id="demo_live_001",
    customer_id="cust_demo_001",
    amount_paise=89900,
    occurred_at=datetime.now(timezone.utc),
    source="customer",
    step="payment_authentication",
    reason="incorrect_otp",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="actually call Razorpay test mode")
    ap.add_argument("--fresh", action="store_true",
                    help="use a new case id, so a new payment link is created")
    args = ap.parse_args()

    # Re-running the same case is SUPPOSED to be refused - that is idempotency
    # working, and it is worth showing. But on camera you also want to see a
    # link get created, so --fresh makes it a genuinely new case rather than
    # weakening the guarantee.
    if args.fresh:
        stamp = datetime.now(timezone.utc).strftime("%m%d%H%M%S")
        EVENT.event_id = f"demo_live_{stamp}"

    policy = load_policy()
    engine = PolicyEngine(policy)
    audit = AuditLog("runs/demo_live.jsonl", "demo")

    print()
    print(rule("="))
    print("  CHUKTA - one case, end to end")
    print(f"  {'LIVE - Razorpay test mode' if args.live else 'DRY RUN - nothing is sent'}")
    print(rule("="))

    # -- credentials -------------------------------------------------------
    creds = None
    if args.live:
        try:
            creds = load_credentials()
        except Exception as exc:
            print(f"\n  {exc}\n")
            return 1
        print(f"\n  credential  {creds.fingerprint}   (test mode, guard passed)")

    # -- 1. the event ------------------------------------------------------
    print(f"\n  EVENT  {EVENT.event_id}   Rs {EVENT.amount_rupees:,.0f}")
    print(f"    source={EVENT.source}  step={EVENT.step}  reason={EVENT.reason}")

    # -- 2. diagnosis ------------------------------------------------------
    klass, evidence = classify(EVENT)
    print("\n  DIAGNOSIS")
    print(f"    class      {klass.value}")
    print(f"    tier       {evidence['tier']}  ({evidence['confidence']} confidence)")
    print(f"    matched    {evidence['matched']}")
    print(f"    rationale  {policy['classes'][klass.value].get('note','').strip()[:60]}")

    # -- 3. decision -------------------------------------------------------
    now = EVENT.occurred_at
    action = engine.decide(EVENT, klass, 0, now)
    if action is None:
        print("\n  No action defined for this class. Case closed.")
        return 0

    when = action.scheduled_for or now
    print("\n  DECISION")
    print(f"    action     {action.type.value}")
    print(f"    channel    {action.channel.value}  ({action.message_class.value})")
    print(f"    frame      {action.message_frame}")
    print(f"    scheduled  {to_ist(when).strftime('%a %d %b %H:%M')} IST")

    # -- 4. gates ----------------------------------------------------------
    customer = Customer(customer_id=EVENT.customer_id)
    state = CaseState()
    gates = evaluate(action, EVENT, customer, klass, state, policy, when)

    print(f"\n  GATES  ({len(gates)} evaluated, none short-circuited)")
    for g in gates:
        mark = "pass " if g.passed else "BLOCK"
        line = f"    [{mark}] {g.rule_id}"
        if g.detail:
            line += f"  {g.detail[:44]}"
        print(line)

    blocked = blocking(gates)
    if blocked:
        print(f"\n  -> NOT EXECUTED. Blocked by {', '.join(blocked)}.")
        print("     No request was made. That is the gate layer working.")
        return 0

    # -- 5. the message ----------------------------------------------------
    msg = Composer(policy).compose(action.message_frame, {
        "name": "Priya",
        "amount": f"Rs {EVENT.amount_rupees:,.0f}",
        "merchant": "Kirana Box",
        "link": "https://rzp.io/x/...",
        "deadline": to_ist(when).strftime("%d %b"),
        "service": "subscription",
    })
    print(f"\n  MESSAGE  (frame: {msg.frame}, source: {msg.source})")
    print(f'    "{msg.text}"')
    if msg.was_blocked:
        print(f"    GUARD BLOCKED the generated text: {', '.join(msg.guard_findings)}")
    else:
        print("    coercion guard: passed")

    # -- 6. execute --------------------------------------------------------
    executor = Executor(
        credentials=creds,
        dry_run=not args.live,
        ledger=IdempotencyLedger("runs/demo_live_idem.jsonl"),
        audit_note=audit.note,
    )
    result = executor.execute(action, EVENT, customer, when, attempt_no=0)

    print("\n  EXECUTION")
    print(f"    status          {result['status']}")
    print(f"    idempotency     {result['idempotency_key'][:24]}...")

    if result["status"] == "dry_run":
        call = result["would_call"]
        print(f"    would call      {call['endpoint']}")
        print(f"    amount          {call['payload'].get('amount')}")
        print(f"    notify          {call['payload'].get('notify')}")
        print("\n  Re-run with --live to actually create this in test mode.")
    elif result["status"] == "ok":
        resp = result["response"]
        print(f"    razorpay id     {resp.get('id')}")
        print(f"    state           {resp.get('status')}")
        url = resp.get("short_url")
        if url:
            print(f"\n    LIVE PAYMENT LINK (test mode):\n      {url}")
    elif result["status"] == "replayed":
        print("    already created earlier; the local idempotency ledger")
        print("    returned the original response rather than calling again.")
    elif result["status"] == "duplicate_prevented":
        print("    the LOCAL ledger was missing, so the call went out - and the")
        print("    gateway refused it as a duplicate. Two independent defences:")
        print("    the ledger, and a reference_id derived from the same key.")
        print("    Nothing was created twice.")
    else:
        print(f"    detail          {result.get('detail') or result.get('error')}")

    # -- 7. the customer replies -------------------------------------------
    # The other half of a conversation, and the only path that can reach
    # TerminalState.PROMISE_TO_PAY. Without it G-OPS-07 is unreachable and one
    # of the five documented stopping rules can never fire.
    print("\n  INBOUND REPLY")
    for text in ('"will pay on the 5th"', '"STOP"'):
        reply = parse(text.strip('"'), when)
        st, cust = CaseState(), Customer(customer_id=EVENT.customer_id)
        note = apply_to_case(reply, st, cust)
        print(f"    {text:26} -> {reply.intent.value:16} ({reply.confidence})")
        if note:
            print(f"      {note}")

        # Prove it actually binds, rather than asserting it does.
        blocked_now = blocking(
            evaluate(action, EVENT, cust, klass, st, policy, when)
        )
        print(f"      next action would be blocked by: {blocked_now or 'nothing'}")

    # -- 8. provenance -----------------------------------------------------
    audit.note(
        "demo_live",
        event_id=EVENT.event_id,
        klass=klass.value,
        action=action.type.value,
        gates_passed=len(gates),
        status=result["status"],
        credential=creds.fingerprint if creds else None,
    )
    ok, problem = verify(audit.path)
    print(f"\n  AUDIT  {audit.path}  ({len(audit)} rows)")
    print(f"    hash chain verified: {ok}{'' if ok else '  ' + str(problem)}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
