"""Decision layer: recoverability class + case context -> a proposed action.

The engine is a reader for policy.yaml and nothing more. It holds no rules of
its own, which is the property that lets the whole policy go on screen in one
file during the demo.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .clock import next_time_in_window, resolve_when
from .types import (
    Action,
    ActionType,
    Channel,
    FailureEvent,
    MessageClass,
    RecoverabilityClass,
)

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent.parent / "policy.yaml"


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict:
    with io.open(path, encoding="utf-8") as fh:
        policy = yaml.safe_load(fh)
    _validate(policy)
    return policy


def _validate(policy: dict) -> None:
    """Fail loudly at load time rather than mid-run."""
    known_actions = {a.value for a in ActionType}
    known_frames = set(policy.get("frames", {}))
    for name, cfg in policy["classes"].items():
        for i, step in enumerate(cfg.get("steps", [])):
            if step["action"] not in known_actions:
                raise ValueError(
                    f"policy.yaml: class '{name}' step {i} names unknown action "
                    f"'{step['action']}'"
                )
            frame = step.get("frame")
            if frame is not None and frame not in known_frames:
                raise ValueError(
                    f"policy.yaml: class '{name}' step {i} names unknown frame "
                    f"'{frame}'"
                )
    for rc in RecoverabilityClass:
        if rc.value not in policy["classes"]:
            raise ValueError(f"policy.yaml has no entry for class '{rc.value}'")

    # G-OPS-08 scores every contact against these. A missing prior would
    # silently fall back to a default and quietly change who gets contacted,
    # so it fails at load instead.
    priors = policy.get("class_priors", {})
    for rc in RecoverabilityClass:
        if rc.value not in priors:
            raise ValueError(
                f"policy.yaml: class_priors has no entry for '{rc.value}'; "
                "G-OPS-08 would score it on a default"
            )


class PolicyEngine:
    def __init__(self, policy: dict):
        self.policy = policy
        # Quiet hours are expressed as the window in which contact IS allowed.
        self.contact_window = tuple(policy["trai"]["promotional_window_ist"])

    @property
    def defaults(self) -> dict:
        return self.policy["defaults"]

    def steps_for(self, klass: RecoverabilityClass) -> list[dict]:
        return self.policy["classes"][klass.value].get("steps", [])

    def is_hard_decline(self, klass: RecoverabilityClass) -> bool:
        return bool(self.policy["classes"][klass.value].get("hard_decline", False))

    def decide(
        self,
        event: FailureEvent,
        klass: RecoverabilityClass,
        step_index: int,
        now: datetime,
    ) -> Action | None:
        """Propose the action for this case's next step, or None if exhausted."""
        steps = self.steps_for(klass)
        if step_index >= len(steps):
            return None

        step = steps[step_index]
        scheduled = resolve_when(step.get("when", {}), now, self.contact_window)

        # Deadline term. Waiting for the next salary date is the single biggest
        # source of Chukta's recovery-time advantage AND of its slowness: the
        # p50 time to recovery is 2.4x the blind ladder's and the p99 runs past
        # 13 days. That cost was invisible until percentiles were reported.
        #
        # A merchant with a cash-flow constraint would rather have the money
        # later-but-bounded than optimally-timed-but-open-ended, so the wait is
        # capped. Hitting the cap means taking a worse-timed action, which is a
        # deliberate trade of conversion probability for certainty.
        cap_hours = self.defaults.get("max_wait_hours")
        if cap_hours is not None and scheduled is not None:
            deadline = now + timedelta(hours=cap_hours)
            if scheduled > deadline:
                # Land inside the contact window if this reaches a customer;
                # a capped charge retry has no window to respect.
                scheduled = (
                    next_time_in_window(deadline, self.contact_window)
                    if step.get("channel", "none") != "none"
                    else deadline
                )

        return Action(
            type=ActionType(step["action"]),
            channel=Channel(step.get("channel", "none")),
            message_class=MessageClass(step.get("message_class", "none")),
            message_frame=step.get("frame"),
            scheduled_for=scheduled,
            rule_id=f"{klass.value}.steps[{step_index}]",
            rationale=self.policy["classes"][klass.value].get("note", "").strip(),
            # The agent arm is deterministic given (class, step), so its
            # propensity is 1.0. The control arm overrides this with its own
            # sampling probability - see sim/control_policy.py.
            p_action=1.0,
        )
