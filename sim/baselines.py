"""Competing strategies, implemented as arms so they can be measured.

The prior-art review established that reason-aware retrying and smart timing
are commodity - every serious vendor ships them. A comparison table built from
marketing copy cannot say how much of Chukta's measured gain is commodity and
how much is the part that is actually new. Running the strategies as arms can.

Four arms, each adding one capability to the one before it:

    blind          fixed ladder, reason-blind          what most dunning does
    smart_timing   optimal timing, still reason-blind  the Stripe-style claim
    reason_aware   + reason-aware action selection     modern dunning tools
    chukta          + bounded contact and stopping      the contribution

Read the deltas, not the totals. blind -> smart_timing is the value of timing.
smart_timing -> reason_aware is the value of diagnosis. Both are commodity.
Only reason_aware -> chukta is attributable to this project, and it is the one
that has to justify itself.

**These are not the vendors' systems.** Stripe trains on 500+ attributes across
billions of payments; `SmartTimingPolicy` is a hand-written approximation of
the published *strategy*, given every advantage this simulator can give it. It
is a fair upper bound on what timing alone buys on this population, not a
reproduction of anyone's product. Beating it here is not beating Stripe.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import numpy as np

from chukta.clock import next_salary_date
from chukta.policy import PolicyEngine
from chukta.types import Action, ActionType, Channel, FailureEvent, MessageClass, RecoverabilityClass

from .control_policy import ACTION_SPACE, BLIND_SCHEDULE_HOURS, BlindRetryPolicy


class SmartTimingPolicy:
    """Reason-blind, but times every retry as well as this population allows.

    Stands in for Stripe Smart Retries and similar: no knowledge of *why* the
    payment failed, but the retry lands at the moment most likely to succeed.
    It is handed the salary-day effect directly - which is more than a real
    model gets for free - so this is a generous upper bound on timing alone.
    """

    name = "smart_timing"

    def __init__(self, seed: int = 20260829):
        self.rng = np.random.default_rng(seed + 3)

    def decide(
        self,
        event: FailureEvent,
        klass: RecoverabilityClass,
        step_index: int,
        now: datetime,
    ) -> Action | None:
        if step_index >= 3:
            return None

        probs = np.array([row[3] for row in ACTION_SPACE], dtype=float)
        probs = probs / probs.sum()
        idx = int(self.rng.choice(len(ACTION_SPACE), p=probs))
        action_type, channel, message_class, _ = ACTION_SPACE[idx]

        # The timing edge: first attempt on the next salary-adjacent morning
        # rather than an hour later, then spaced well clear of it.
        if step_index == 0:
            when = next_salary_date(now)
        else:
            when = next_salary_date(now) + timedelta(days=2 * step_index)

        return Action(
            type=action_type,
            channel=channel,
            # Service-class copy: a vendor operating in India honours TCCCPR.
            # Leaving it promotional would hand Chukta a win on compliance that
            # a real competitor would not concede.
            message_class=(
                MessageClass.TRANSACTIONAL
                if message_class is not MessageClass.NONE
                else MessageClass.NONE
            ),
            message_frame=None,  # generic copy, no behavioural frame
            scheduled_for=when,
            rule_id=f"smart_timing.step[{step_index}]",
            rationale="optimal retry timing; failure reason not consulted",
            p_action=float(probs[idx]),
        )


class ReasonAwarePolicy:
    """Full diagnosis and the matched intervention ladder - but works every
    case to exhaustion.

    This is the modern dunning tool: it knows an expired card should not be
    re-presented, and it picks the right channel and message. What it does not
    have is a reason to *stop* on a case that is still technically workable.
    It is Chukta minus the bounded-contact idea, which is exactly the delta the
    project has to defend.
    """

    name = "reason_aware"

    def __init__(self, engine: PolicyEngine):
        self.engine = engine

    def decide(
        self,
        event: FailureEvent,
        klass: RecoverabilityClass,
        step_index: int,
        now: datetime,
    ) -> Action | None:
        return self.engine.decide(event, klass, step_index, now)


class BlindAdapter:
    """Gives the existing control arm the same signature as the others."""

    name = "blind"

    def __init__(self, seed: int = 20260829):
        self.inner = BlindRetryPolicy(seed=seed)

    def decide(
        self,
        event: FailureEvent,
        klass: RecoverabilityClass,
        step_index: int,
        now: datetime,
    ) -> Action | None:
        return self.inner.decide(step_index, now)


class ChuktaPolicy:
    name = "chukta"

    def __init__(self, engine: PolicyEngine):
        self.engine = engine

    def decide(
        self,
        event: FailureEvent,
        klass: RecoverabilityClass,
        step_index: int,
        now: datetime,
    ) -> Action | None:
        return self.engine.decide(event, klass, step_index, now)


# Contact budget per arm. Everything else about the policy is shared, so the
# reason_aware/chukta comparison isolates one variable.
#
# All four arms run under the SAME compliance gates. A real competitor selling
# into India complies with TRAI and RBI too - crediting Chukta for guardrails
# every serious vendor also has would be scoring a point against a strawman.
UNBOUNDED_CONTACT_BUDGET = 6


def policy_for_arm(base_policy: dict, arm: str) -> dict:
    """Per-arm policy config.

    Only `chukta` gets the uplift threshold (G-OPS-08). That gate IS the
    contribution, so every other arm has to run without it or the comparison
    is rigged. The compliance gates are shared by all four arms - a real
    competitor selling into India complies too.
    """
    if arm == "chukta":
        return base_policy

    other = copy.deepcopy(base_policy)
    other["defaults"].pop("min_uplift_score", None)
    if arm == "reason_aware":
        # Same diagnosis and ladder as Chukta, but nothing tells it to stop
        # working a case that is still technically workable.
        other["defaults"]["contact_budget"] = UNBOUNDED_CONTACT_BUDGET
        other["defaults"]["contact_cooldown_hours"] = 12
    return other


def build_arms(engine: PolicyEngine, seed: int) -> dict:
    return {
        "blind": BlindAdapter(seed=seed),
        "smart_timing": SmartTimingPolicy(seed=seed),
        "reason_aware": ReasonAwarePolicy(engine),
        "chukta": ChuktaPolicy(engine),
    }


ARM_ORDER = ("blind", "smart_timing", "reason_aware", "chukta")

ARM_LABELS = {
    "blind": "fixed ladder, reason-blind",
    "smart_timing": "optimal timing, reason-blind",
    "reason_aware": "reason-aware, unbounded contact",
    "chukta": "reason-aware, bounded contact",
}
