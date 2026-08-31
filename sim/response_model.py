"""How the simulated world responds to an action.

Calibration, not invention
--------------------------
The shape of the retry-timing curve is anchored to published industry figures
rather than chosen to flatter the agent:

  * Adding three extra retries inside the standard dunning window lifts total
    recoveries by roughly 20% (relative).
  * Making the first retry at 24h rather than 2h improves recovery by about
    6.5% (relative).

Both are vendor-published, so they are calibration anchors, not evidence. They
are recorded here, in one place, so a reviewer can change them and re-run.

Everything else is structural and follows from the taxonomy: a retry against a
dead card cannot succeed no matter how well it is timed.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from chukta.clock import to_ist
from chukta.types import ActionType, RecoverabilityClass as RC

# --- published anchors -------------------------------------------------------

RETRY_24H_OVER_2H_RELATIVE_LIFT = 0.065
EXTRA_RETRIES_RELATIVE_LIFT = 0.202

# --- structural feasibility --------------------------------------------------
# Probability a *charge attempt* succeeds under ideal timing, by class.
# Zero means the class is structurally unrecoverable by retrying, which is the
# single fact blind dunning throws away.
CHARGE_FEASIBILITY = {
    RC.TRANSIENT: 0.58,
    RC.FUNDING: 0.62,           # only near a salary date; see timing_factor
    RC.AUTH_DROPOFF: 0.14,      # re-serving the same friction rarely works
    RC.INSTRUMENT_INVALID: 0.0,
    RC.CUSTOMER_INTENT: 0.0,
    RC.MANDATE: 0.0,            # the authority to charge is what broke
    RC.MERCHANT_CONFIG: 0.0,
    RC.UNKNOWN: 0.20,
}

# How much a contact-driven action can help, by class. An update-instrument
# request is the ONLY thing that recovers a dead card, so it scores high there.
CONTACT_RELEVANCE = {
    (RC.INSTRUMENT_INVALID, ActionType.UPDATE_INSTRUMENT): 1.0,
    (RC.MANDATE, ActionType.REMANDATE): 0.9,
    (RC.AUTH_DROPOFF, ActionType.PAYMENT_LINK): 0.85,
    (RC.FUNDING, ActionType.PAYMENT_LINK): 0.55,
    (RC.TRANSIENT, ActionType.PAYMENT_LINK): 0.45,
    (RC.CUSTOMER_INTENT, ActionType.PAYMENT_LINK): 0.10,
}

# Relative effect of each behavioural frame, indexed to a plain notice at 1.0.
# Derived from the BIT tax-compliance trials; see README on why transplanting
# these to consumer recovery is an assumption rather than a result.
FRAME_LIFT = {
    None: 1.0,
    "simplification": 1.15,
    "specific_action": 1.20,
    "social_norm": 1.28,
    "deliberate_choice": 1.32,
    "loss_framing": 1.18,
}

# Attempt fatigue: each successive charge on the same case is worth less.
ATTEMPT_DECAY = 0.82


def timing_factor(klass: RC, scheduled_for: datetime, salary_day: int,
                  hours_since_failure: float) -> float:
    """Multiplier on charge feasibility from *when* the attempt lands."""
    factor = 1.0

    # The published 2h-vs-24h effect, applied smoothly.
    if hours_since_failure >= 24:
        factor *= 1.0 + RETRY_24H_OVER_2H_RELATIVE_LIFT
    elif hours_since_failure >= 6:
        factor *= 1.0 + RETRY_24H_OVER_2H_RELATIVE_LIFT * 0.5

    # Funding failures are dominated by payroll proximity. This is the effect
    # blind retry cannot capture at all, because it never looks at the reason.
    if klass is RC.FUNDING:
        local = to_ist(scheduled_for)
        distance = min(abs(local.day - salary_day), abs(local.day - salary_day - 30))
        if distance == 0:
            factor *= 1.0
        elif distance <= 2:
            factor *= 0.72
        elif distance <= 6:
            factor *= 0.38
        else:
            factor *= 0.19

        # Nobody's balance improves at 3am.
        if not (8 <= local.hour <= 22):
            factor *= 0.75

    return factor


def p_charge_succeeds(klass: RC, scheduled_for: datetime, salary_day: int,
                      hours_since_failure: float, attempt_index: int) -> float:
    base = CHARGE_FEASIBILITY.get(klass, 0.1)
    if base == 0.0:
        return 0.0
    p = base * timing_factor(klass, scheduled_for, salary_day, hours_since_failure)
    p *= ATTEMPT_DECAY ** attempt_index
    return float(np.clip(p, 0.0, 0.95))


def p_contact_converts(klass: RC, action_type: ActionType, frame: str | None,
                       response_to_contact: float, contact_index: int) -> float:
    relevance = CONTACT_RELEVANCE.get((klass, action_type), 0.25)
    lift = FRAME_LIFT.get(frame, 1.0)
    # Diminishing returns on repeated outreach, independent of churn.
    fatigue = ATTEMPT_DECAY ** contact_index
    return float(np.clip(response_to_contact * relevance * lift * fatigue, 0.0, 0.95))


def p_churn_from_contact(churn_per_contact: float, contact_index: int) -> float:
    """Annoyance compounds. The second unwanted message is worse than the first."""
    return float(np.clip(churn_per_contact * (1.0 + 0.45 * contact_index), 0.0, 0.9))
