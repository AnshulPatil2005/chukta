"""The simulated customer population.

The uplift literature (Radcliffe & Surry 2011; Gutierrez & Gerardy 2017) splits
any treated population into four groups. All four exist in payment recovery,
and modelling them is what makes restraint measurable:

    persuadable   low self-recovery, large positive response to outreach
    sure_thing    high self-recovery, ~zero response - contacting them buys
                  nothing but is credited as a "recovery" by naive systems
    lost_cause    no self-recovery, no response
    sleeping_dog  moderate self-recovery, NEGATIVE response - outreach reminds
                  a lukewarm subscriber they are paying for something they do
                  not use, and they cancel

Without the sleeping-dog hazard, stopping rules cost nothing and therefore
demonstrate nothing. With it, every unnecessary contact shows up in the P&L.

Common random numbers
---------------------
Each case pre-draws its uniforms ONCE and both arms consume the same stream.
A customer who would have self-recovered does so in both arms; a churn draw
that would fire on the second contact fires on the second contact in whichever
arm reaches it. Differences between arms are therefore attributable to policy
rather than to noise - the standard paired-comparison variance reduction.

The quadrant label is ground truth for scoring only. Nothing in chukta/ may
read it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

QUADRANTS = ("persuadable", "sure_thing", "lost_cause", "sleeping_dog")

# Mixture weights. Deliberately not uniform: most at-risk payments in the wild
# are recoverable-with-help or self-resolving, and true sleeping dogs are a
# minority - but an expensive one.
QUADRANT_WEIGHTS = (0.34, 0.30, 0.24, 0.12)

QUADRANT_PARAMS = {
    #                 self-recovery   response to a contact   churn per contact
    "persuadable":   dict(p_self=0.08, response=0.42, churn=0.010),
    "sure_thing":    dict(p_self=0.71, response=0.03, churn=0.008),
    "lost_cause":    dict(p_self=0.02, response=0.02, churn=0.004),
    "sleeping_dog":  dict(p_self=0.29, response=0.04, churn=0.150),
}

MAX_DRAWS = 8  # more than any policy can consume


@dataclass
class SimCustomer:
    customer_id: str
    quadrant: str
    p_self_recover: float
    response_to_contact: float
    churn_per_contact: float
    dnd_registered: bool
    salary_day: int
    # Common random numbers - drawn once, shared by both arms.
    u_self: float = 0.0
    u_charge: np.ndarray = field(default_factory=lambda: np.zeros(MAX_DRAWS))
    u_contact: np.ndarray = field(default_factory=lambda: np.zeros(MAX_DRAWS))
    u_churn: np.ndarray = field(default_factory=lambda: np.zeros(MAX_DRAWS))


def build_population(n: int, seed: int = 20260829) -> list[SimCustomer]:
    rng = np.random.default_rng(seed)
    quadrants = rng.choice(QUADRANTS, size=n, p=QUADRANT_WEIGHTS)

    people: list[SimCustomer] = []
    for i, q in enumerate(quadrants):
        p = QUADRANT_PARAMS[q]
        # Per-customer heterogeneity around the quadrant mean, clipped to
        # stay inside [0, 1].
        jitter = rng.normal(1.0, 0.18, size=3).clip(0.35, 1.9)
        people.append(
            SimCustomer(
                customer_id=f"cust_{i:04d}",
                quadrant=str(q),
                p_self_recover=float(np.clip(p["p_self"] * jitter[0], 0.0, 0.97)),
                response_to_contact=float(np.clip(p["response"] * jitter[1], 0.0, 0.95)),
                churn_per_contact=float(np.clip(p["churn"] * jitter[2], 0.0, 0.6)),
                dnd_registered=bool(rng.random() < 0.22),
                salary_day=int(rng.choice([1, 7], p=[0.72, 0.28])),
                u_self=float(rng.random()),
                u_charge=rng.random(MAX_DRAWS),
                u_contact=rng.random(MAX_DRAWS),
                u_churn=rng.random(MAX_DRAWS),
            )
        )
    return people


def quadrant_counts(people: list[SimCustomer]) -> dict[str, int]:
    out = {q: 0 for q in QUADRANTS}
    for person in people:
        out[person.quadrant] += 1
    return out
