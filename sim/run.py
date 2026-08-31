"""Two-arm harness.

Runs the identical seeded population through the blind-retry control and
through Chukta, and reports the difference. Payment execution is stubbed here;
`chukta/execute.py` drives the real test-mode API for the live demo path.

Usage:
    python -m sim.run --n 300 --seed 20260829
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from chukta.audit import AuditLog
from chukta.gates import CaseState, blocking, evaluate, passed
from chukta.policy import PolicyEngine, load_policy
from chukta.taxonomy import classify
from chukta.types import (
    Action,
    ActionType,
    Channel,
    Customer,
    Decision,
    FailureEvent,
    TerminalState,
)

from .baselines import policy_for_arm
from .control_policy import BlindRetryPolicy
from .corpus import build_corpus
from .population import SimCustomer, build_population, quadrant_counts
from .response_model import (
    p_charge_succeeds,
    p_contact_converts,
    p_churn_from_contact,
)

HORIZON_DAYS = 14
CONTACT_CHANNELS = {Channel.SMS, Channel.WHATSAPP, Channel.EMAIL}
START = datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc)


@dataclass
class CaseOutcome:
    event_id: str
    customer_id: str
    quadrant: str
    arm: str
    amount_rupees: float
    klass: str
    recovered: bool = False
    recovered_by: str | None = None
    churned: bool = False
    attempts: int = 0
    contacts: int = 0
    blocked_actions: int = 0
    terminal_state: str | None = None
    hours_to_recovery: float | None = None
    # Logged for off-policy evaluation. Only the control arm populates
    # propensities; see eval/dr.py.
    log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def recovered_rupees(self) -> float:
        return self.amount_rupees if self.recovered else 0.0


def _self_recovery_time(person: SimCustomer, event: FailureEvent) -> datetime | None:
    """When this customer would resolve unaided, if they would at all.

    Drawn from the shared uniform stream so it is identical in both arms - a
    Sure Thing pays on the same day whichever policy is running.
    """
    if person.u_self >= person.p_self_recover:
        return None
    hours = 6.0 + 150.0 * float(person.u_charge[7])
    return event.occurred_at + timedelta(hours=hours)


def run_case(
    person: SimCustomer,
    event: FailureEvent,
    arm: str,
    engine: PolicyEngine,
    blind: BlindRetryPolicy,
    policy: dict,
    audit: AuditLog,
    decider: Any = None,
) -> CaseOutcome:
    """Run one case under one arm.

    `decider`, when given, replaces the built-in two-arm dispatch and must
    expose `decide(event, klass, step_index, now)`. That is how
    `eval/compare_systems.py` runs the competing strategies through the
    identical harness - a comparison where each arm has its own loop is not a
    comparison.
    """
    klass, evidence = classify(event)
    outcome = CaseOutcome(
        event_id=event.event_id,
        customer_id=event.customer_id,
        quadrant=person.quadrant,
        arm=arm,
        amount_rupees=event.amount_rupees,
        klass=klass.value,
    )

    customer = Customer(
        customer_id=person.customer_id,
        dnd_registered=person.dnd_registered,
        quadrant=person.quadrant,
    )
    state = CaseState()
    now = event.occurred_at
    horizon = event.occurred_at + timedelta(days=HORIZON_DAYS)
    self_at = _self_recovery_time(person, event)

    step = 0
    terminal: TerminalState | None = None

    while terminal is None and now <= horizon and step < 8:
        if decider is not None:
            action = decider.decide(event, klass, step, now)
        elif arm == "chukta":
            action = engine.decide(event, klass, step, now)
        else:
            action = blind.decide(step, now)
        if action is None:
            terminal = (
                TerminalState.HARD_DECLINE
                if engine.is_hard_decline(klass)
                else TerminalState.ATTEMPT_CAP
            )
            break

        when = action.scheduled_for or now

        # Did they resolve on their own before this action would have landed?
        if self_at is not None and self_at <= when and not outcome.recovered:
            outcome.recovered = True
            outcome.recovered_by = "self"
            outcome.hours_to_recovery = (
                self_at - event.occurred_at
            ).total_seconds() / 3600.0
            terminal = TerminalState.RECOVERED
            break

        gates = evaluate(action, event, customer, klass, state, policy, when)
        decision = Decision(
            event_id=event.event_id,
            customer_id=event.customer_id,
            arm=arm,
            decided_at=when,
            klass=klass,
            evidence=evidence,
            action=action,
            gates=gates,
        )

        if passed(gates) and action.type is not ActionType.NO_ACTION:
            result = _execute(
                action, event, klass, person, state, when, outcome
            )
            decision.executed = True
            decision.execution_result = result
            if result.get("recovered"):
                outcome.recovered = True
                outcome.recovered_by = result["via"]
                outcome.hours_to_recovery = (
                    when - event.occurred_at
                ).total_seconds() / 3600.0
                terminal = TerminalState.RECOVERED
            elif result.get("churned"):
                outcome.churned = True
                customer.opted_out = True
                terminal = TerminalState.OPTED_OUT
        elif not passed(gates):
            outcome.blocked_actions += 1

        outcome.log.append(
            {
                "step": step,
                "action": action.type.value,
                "channel": action.channel.value,
                "frame": action.message_frame,
                "p_action": action.p_action,
                "executed": decision.executed,
                "blocked_by": blocking(gates),
                "recovered": bool((decision.execution_result or {}).get("recovered")),
            }
        )

        decision.terminal_state = terminal
        audit.write(decision)

        now = when + timedelta(minutes=1)
        step += 1

    # A Sure Thing pays even after we stop working the case. Both arms get
    # credit for it, which is exactly why gross recovery rate is misleading.
    if not outcome.recovered and self_at is not None and self_at <= horizon:
        outcome.recovered = True
        outcome.recovered_by = "self"
        outcome.hours_to_recovery = (
            self_at - event.occurred_at
        ).total_seconds() / 3600.0
        terminal = terminal or TerminalState.RECOVERED

    outcome.terminal_state = terminal.value if terminal else "horizon"
    outcome.attempts = state.attempts
    outcome.contacts = state.contacts
    return outcome


def _execute(
    action: Action,
    event: FailureEvent,
    klass,
    person: SimCustomer,
    state: CaseState,
    when: datetime,
    outcome: CaseOutcome,
) -> dict[str, Any]:
    """Simulated execution. The real API path lives in chukta/execute.py."""
    hours_since = (when - event.occurred_at).total_seconds() / 3600.0

    if action.type is ActionType.RETRY_CHARGE:
        idx = min(state.attempts, 7)
        p = p_charge_succeeds(klass, when, person.salary_day, hours_since, idx)
        state.attempts += 1
        state.exposure_rupees += event.amount_rupees
        hit = float(person.u_charge[idx]) < p
        return {"kind": "charge", "p": round(p, 4), "recovered": hit, "via": "retry"}

    if action.channel in CONTACT_CHANNELS:
        idx = min(state.contacts, 7)
        state.contacts += 1
        state.last_contact_at = when

        p = p_contact_converts(
            klass, action.type, action.message_frame, person.response_to_contact, idx
        )
        if float(person.u_contact[idx]) < p:
            return {
                "kind": "contact",
                "p": round(p, 4),
                "recovered": True,
                "via": action.type.value,
            }

        churn_p = p_churn_from_contact(person.churn_per_contact, idx)
        if float(person.u_churn[idx]) < churn_p:
            return {
                "kind": "contact",
                "p": round(p, 4),
                "recovered": False,
                "churned": True,
                "churn_p": round(churn_p, 4),
                "via": action.type.value,
            }
        return {"kind": "contact", "p": round(p, 4), "recovered": False,
                "via": action.type.value}

    if action.type is ActionType.MERCHANT_ALERT:
        return {"kind": "internal_alert", "recovered": False, "via": "merchant_alert"}

    return {"kind": "noop", "recovered": False, "via": "none"}


def _environment() -> dict[str, Any]:
    """What produced these numbers. Seeds alone are not enough - a numpy change
    to a sampling routine would move results with the seed held fixed."""
    import platform

    import numpy

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": numpy.__version__,
        "policy_sha256": hashlib.sha256(
            Path("policy.yaml").read_bytes()
        ).hexdigest()[:16],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260829)
    ap.add_argument("--out", default="runs")
    args = ap.parse_args()

    run_id = uuid.uuid4().hex[:8]
    out = Path(args.out)
    policy = load_policy()
    engine = PolicyEngine(policy)

    people = build_population(args.n, seed=args.seed)
    events = build_corpus(people, START, seed=args.seed)
    by_id = {p.customer_id: p for p in people}

    audit = AuditLog(out / f"audit_{run_id}.jsonl", run_id)
    audit.note(
        "run_start",
        n=args.n,
        seed=args.seed,
        horizon_days=HORIZON_DAYS,
        policy_version=policy["version"],
        quadrants=quadrant_counts(people),
        disclosure="all events replayed from sim/corpus.py; none triggered live",
        # Pinned so a number can be traced to the environment that produced it.
        # Without this a result is not reproducible, only repeatable-here.
        environment=_environment(),
    )

    results: dict[str, list[CaseOutcome]] = {"control": [], "chukta": []}
    for arm in ("control", "chukta"):
        blind = BlindRetryPolicy(seed=args.seed)
        # The control arm runs under the compliance and operational gates - a
        # real blind-retry integration is still bound by RBI and TRAI - but NOT
        # under G-OPS-08. That gate is the thing being evaluated; handing it to
        # the baseline would measure it against itself.
        arm_policy = policy_for_arm(policy, "chukta" if arm == "chukta" else "control")
        for event in events:
            person = by_id[event.customer_id]
            results[arm].append(
                run_case(person, event, arm, engine, blind, arm_policy, audit)
            )

    audit.note("run_end", decisions=len(audit))

    payload = {
        "run_id": run_id,
        "seed": args.seed,
        "n": args.n,
        "horizon_days": HORIZON_DAYS,
        "environment": _environment(),
        "audit_path": str(out / f"audit_{run_id}.jsonl"),
        "quadrants": quadrant_counts(people),
        "arms": {
            arm: [
                {k: v for k, v in vars(o).items() if k != "log"}
                for o in outcomes
            ]
            for arm, outcomes in results.items()
        },
        "logs": {
            arm: [{"event_id": o.event_id, "log": o.log} for o in outcomes]
            for arm, outcomes in results.items()
        },
    }
    latest = out / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"run {run_id}: {args.n} cases x 2 arms, {len(audit)} audit rows")
    print(f"  -> {latest}")
    print(f"  -> {payload['audit_path']}")


if __name__ == "__main__":
    main()
