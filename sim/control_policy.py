"""The control arm: industry-default blind retry.

Fixed schedule, same rail, generic message, no reference to why the payment
failed. This is what most dunning does today and it is the thing the agent has
to beat.

Why it is stochastic
--------------------
A deterministic control arm logs no action propensities, and without
propensities the doubly robust estimator (Dudik, Langford & Li 2011) cannot be
computed at all - not later, not with a patch. Retrofitting means re-running
the whole batch. So the control samples from a fixed distribution and records
`p_action` on every decision, even though nothing reads it on the day it is
written.

The sampling does not make the control smarter. It still ignores the failure
reason entirely; it just does so with a known probability.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from chukta.types import Action, ActionType, Channel, MessageClass

# Industry-standard dunning ladder.
BLIND_SCHEDULE_HOURS = (1, 24, 72)

# Fixed action distribution at every step. Deliberately reason-blind.
ACTION_SPACE = (
    (ActionType.RETRY_CHARGE, Channel.NONE, MessageClass.NONE, 0.60),
    (ActionType.PAYMENT_LINK, Channel.SMS, MessageClass.PROMOTIONAL, 0.35),
    (ActionType.NO_ACTION, Channel.NONE, MessageClass.NONE, 0.05),
)


class BlindRetryPolicy:
    def __init__(self, seed: int = 20260829):
        self.rng = np.random.default_rng(seed + 2)

    def decide(self, step_index: int, now: datetime) -> Action | None:
        if step_index >= len(BLIND_SCHEDULE_HOURS):
            return None

        probs = np.array([row[3] for row in ACTION_SPACE], dtype=float)
        probs = probs / probs.sum()
        idx = int(self.rng.choice(len(ACTION_SPACE), p=probs))
        action_type, channel, message_class, _ = ACTION_SPACE[idx]

        return Action(
            type=action_type,
            channel=channel,
            message_class=message_class,
            message_frame=None,  # generic copy, no behavioural frame
            scheduled_for=now + timedelta(hours=BLIND_SCHEDULE_HOURS[step_index]),
            rule_id=f"blind.schedule[{step_index}]",
            rationale="fixed dunning ladder; failure reason not consulted",
            p_action=float(probs[idx]),
        )
