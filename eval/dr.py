"""Doubly robust off-policy evaluation, and a check on whether it works.

    python -m eval.dr

The control arm samples its actions and records `p_action` on every decision.
That was a deliberate one-way door on day 1 - a deterministic logging policy
records no propensities and they cannot be reconstructed afterwards. This is
the module that finally spends them.

**The question DR answers.** Given only logs from the control policy, what
would Chukta have earned? That is what a merchant actually needs before
switching: an estimate of a policy they have not run, from data they already
have, without exposing customers to it.

**Why doubly robust** (Dudik, Langford & Li, ICML 2011). Two naive estimators
each fail in a different way:

  Direct method    fit a reward model, evaluate the target policy under it.
                   Low variance, but wrong if the model is wrong.
  IPS              reweight logged rewards by 1/propensity. Unbiased if the
                   propensities are right, but explodes when they are small.

DR combines them so it stays consistent if EITHER the reward model or the
propensity model is right - hence "doubly". It is not magic: if both are wrong
it is wrong too.

**What makes this worth having.** In a real deployment you can never check an
off-policy estimate, because the counterfactual never happened. Here it did -
the same population was run under both arms with common random numbers. So the
estimate can be compared against ground truth, which is the one thing a
simulation is genuinely good for. If DR disagrees with the measured value, the
estimator is not trustworthy on this data and saying so is the useful result.

**Simplification, stated up front.** This treats the first decision on each
case as a one-step contextual bandit: context is (recoverability class, amount
band), action is what the arm did first, reward is rupees recovered. A full
sequential treatment needs per-step importance weights whose product degenerates
over a ladder this long. The one-step view is a real restriction, not a
formality, and it is why the numbers below are a sanity check rather than a
production estimate.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

# Wang, Agarwal & Dudik (ICML 2017): clip importance weights so one tiny
# propensity cannot dominate the estimate. Trades a little bias for a lot of
# variance, which is the right trade at n=300.
DEFAULT_CLIP = 10.0


@dataclass
class DRResult:
    dr: float
    ips: float
    direct: float
    truth: float | None
    n: int
    clipped: int
    max_weight: float

    @property
    def dr_error(self) -> float | None:
        return None if self.truth is None else self.dr - self.truth

    @property
    def dr_error_pct(self) -> float | None:
        if self.truth in (None, 0):
            return None
        return (self.dr - self.truth) / abs(self.truth) * 100


def _amount_band(rupees: float) -> str:
    """Coarse context. Finer bands would mean fewer samples per cell, and the
    reward model is already thin at n=300."""
    if rupees < 500:
        return "small"
    if rupees < 2000:
        return "medium"
    return "large"


def _first_step(case_log: list[dict]) -> dict | None:
    return case_log[0] if case_log else None


def _contexts(run: dict, arm: str) -> dict[str, dict[str, Any]]:
    """One row per case: context, first action, propensity, reward."""
    outcomes = {c["event_id"]: c for c in run["arms"][arm]}
    out: dict[str, dict[str, Any]] = {}
    for entry in run["logs"][arm]:
        step = _first_step(entry["log"])
        if step is None:
            continue
        case = outcomes[entry["event_id"]]
        out[entry["event_id"]] = {
            "context": (case["klass"], _amount_band(case["amount_rupees"])),
            "action": step["action"],
            "p_action": step.get("p_action"),
            "reward": case["amount_rupees"] if case["recovered"] else 0.0,
        }
    return out


def fit_reward_model(rows: dict[str, dict]) -> dict[tuple, float]:
    """Q-hat: mean reward per (context, action), from the LOGGING policy only.

    Deliberately simple. A richer model would fit the simulator better and tell
    you less about whether DR works on data this thin.
    """
    buckets: dict[tuple, list[float]] = defaultdict(list)
    for row in rows.values():
        buckets[(row["context"], row["action"])].append(row["reward"])
    return {k: statistics.mean(v) for k, v in buckets.items()}


def _q(model: dict, fallback: float, context: tuple, action: str) -> float:
    return model.get((context, action), fallback)


def evaluate(run: dict, clip: float = DEFAULT_CLIP) -> DRResult:
    """Estimate the Chukta arm's value using only control-arm logs."""
    logging_rows = _contexts(run, "control")
    target_rows = _contexts(run, "chukta")

    model = fit_reward_model(logging_rows)
    global_mean = statistics.mean(r["reward"] for r in logging_rows.values())

    dr_terms, ips_terms, direct_terms = [], [], []
    clipped = 0
    max_weight = 0.0

    for event_id, row in logging_rows.items():
        target = target_rows.get(event_id)
        if target is None:
            continue

        context = row["context"]
        target_action = target["action"]

        # Direct: what the reward model says the target action is worth.
        q_target = _q(model, global_mean, context, target_action)
        direct_terms.append(q_target)

        p = row["p_action"]
        if not p or p <= 0:
            # No propensity means no correction is possible; fall back to the
            # direct term rather than silently dropping the case.
            dr_terms.append(q_target)
            ips_terms.append(0.0)
            continue

        # The logging policy is stochastic, the target is deterministic, so the
        # indicator is 1 exactly when the arms happened to choose the same
        # first action.
        match = 1.0 if row["action"] == target_action else 0.0
        weight = min(match / p, clip)
        if match / p > clip:
            clipped += 1
        max_weight = max(max_weight, weight)

        q_logged = _q(model, global_mean, context, row["action"])
        dr_terms.append(q_target + weight * (row["reward"] - q_logged))
        ips_terms.append(weight * row["reward"])

    truth = None
    if target_rows:
        truth = statistics.mean(r["reward"] for r in target_rows.values())

    return DRResult(
        dr=statistics.mean(dr_terms) if dr_terms else 0.0,
        ips=statistics.mean(ips_terms) if ips_terms else 0.0,
        direct=statistics.mean(direct_terms) if direct_terms else 0.0,
        truth=truth,
        n=len(dr_terms),
        clipped=clipped,
        max_weight=max_weight,
    )


def report(run: dict, clip: float = DEFAULT_CLIP) -> str:
    r = evaluate(run, clip=clip)
    # Scale by the number of cases the estimator actually saw, not the run
    # size. Cases that self-resolved before any decision have no logged action
    # and are excluded - multiplying a mean over 280 by 300 would overstate
    # every column by the same 7%.
    n_cases = r.n

    lines = [
        "DOUBLY ROBUST OFF-POLICY EVALUATION",
        "",
        "  Estimating the Chukta arm's per-case value from CONTROL logs only -",
        "  the counterfactual a merchant would need before switching.",
        "",
        f"{'estimator':<28} {'per case':>12} {'over ' + str(r.n) + ' cases':>15}",
        "-" * 58,
        f"{'direct method':<28} {r.direct:>12,.0f} {r.direct * n_cases:>15,.0f}",
        f"{'IPS (clipped)':<28} {r.ips:>12,.0f} {r.ips * n_cases:>15,.0f}",
        f"{'doubly robust':<28} {r.dr:>12,.0f} {r.dr * n_cases:>15,.0f}",
    ]

    if r.truth is not None:
        lines += [
            "-" * 58,
            f"{'ACTUAL (on-policy)':<28} {r.truth:>12,.0f} "
            f"{r.truth * n_cases:>15,.0f}",
            "",
            f"  DR error: {r.dr_error:+,.0f} per case "
            f"({r.dr_error_pct:+.1f}%)",
        ]

    lines += [
        "",
        f"  n={r.n}   weights clipped at {clip:g}: {r.clipped}   "
        f"max weight {r.max_weight:.1f}",
        "",
    ]

    # The estimator is only useful if it is honest about failing.
    if r.truth is not None and r.dr_error_pct is not None:
        err = abs(r.dr_error_pct)
        if err < 10:
            lines.append(
                "  DR lands within 10% of the measured value. On this data the\n"
                "  estimator is usable - which is worth knowing, because in a\n"
                "  real deployment this check is impossible."
            )
        elif err < 30:
            lines.append(
                "  DR is directionally right but loose. Treat it as a sanity\n"
                "  check, not a number to make a switching decision on."
            )
        else:
            lines.append(
                "  DR does NOT agree with the measured value. Do not trust it\n"
                "  on data of this shape. The likely cause is overlap: the\n"
                "  control policy rarely takes the action Chukta would take, so\n"
                "  there is little to reweight and the reward model carries the\n"
                "  whole estimate."
            )

    control_rows = _contexts(run, "control")
    target_rows = _contexts(run, "chukta")
    overlap = sum(
        1
        for eid, row in control_rows.items()
        if (t := target_rows.get(eid)) and t["action"] == row["action"]
    )
    pct = overlap / r.n * 100 if r.n else 0
    lines += [
        "",
        f"  Action overlap: the arms took the same first action on {overlap} of",
        f"  {r.n} cases ({pct:.0f}%). Off-policy evaluation is only as good as",
        "  this overlap - with none, no amount of reweighting recovers the",
        "  counterfactual, and DR collapses to the reward model.",
        "",
        f"  Excluded: {len(run['arms']['chukta']) - r.n} cases that resolved before",
        "  any decision was taken, so there is no logged action to reweight.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", type=float, default=DEFAULT_CLIP)
    args = ap.parse_args()

    from .metrics import load_run

    print()
    print(report(load_run(), clip=args.clip))
    print()


if __name__ == "__main__":
    main()
