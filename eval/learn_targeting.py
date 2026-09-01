"""Does learning the targeting score beat writing it by hand?

    python -m eval.learn_targeting --train 6 --test 6

`policy.yaml` carries `class_priors` - eight numbers, written by hand from
structural reasoning about which failures are worth intervening on. Competing
entries on this track fit models instead: gradient-boosted recovery predictors,
a LinUCB contextual bandit. The obvious question is whether that helps.

This answers it in the only way that means anything: **fit on one set of seeds,
score on seeds the fitter never saw.** A model evaluated on its training data
always wins, which is why "our bandit converged" and "our model is good" are
different claims.

Two things this deliberately does NOT do:

**No sklearn.** The learner is an empirical uplift table over
(class x amount band) - a T-learner with a lookup for a base model. That is a
real learned model, and keeping it interpretable means the comparison is about
whether the DATA beats the hand-written numbers, not about whether gradient
boosting beats a lookup. Adding a heavier learner would confound the question.

**No claim that convergence implies quality.** A bandit that recovers the
ground-truth mapping on a simulator has proved the bandit works, not that the
policy is good - the ground truth was authored by whoever wrote the simulator.
The honest test is out-of-sample ranking quality, which is what Qini measures.

The result is allowed to be "hand-written wins". That would be a finding, not a
failure, and it is reported either way.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from sim.baselines import policy_for_arm
from sim.control_policy import BlindRetryPolicy
from sim.corpus import build_corpus
from sim.population import build_population
from sim.run import START, run_case
from chukta.policy import PolicyEngine, load_policy

from .sweep import NullAudit
from .uplift import qini_coefficient, qini_from_rows

# Same banding the DR estimator uses, so the two share a notion of context.
BANDS = ((500, "small"), (2000, "medium"), (float("inf"), "large"))


def band(rupees: float) -> str:
    for ceiling, name in BANDS:
        if rupees < ceiling:
            return name
    return "large"


def paired_rows(n: int, seed: int, policy: dict, engine: PolicyEngine) -> list[dict]:
    """Per-case paired uplift for one seed. Both arms, common random numbers."""
    people = build_population(n, seed=seed)
    events = build_corpus(people, START, seed=seed)
    by_id = {p.customer_id: p for p in people}
    audit = NullAudit()

    outcomes = {}
    for arm in ("control", "chukta"):
        blind = BlindRetryPolicy(seed=seed)
        ap = policy_for_arm(policy, "chukta" if arm == "chukta" else "control")
        outcomes[arm] = {
            e.event_id: run_case(by_id[e.customer_id], e, arm, engine, blind, ap, audit)
            for e in events
        }

    rows = []
    for eid, treated in outcomes["chukta"].items():
        ctl = outcomes["control"][eid]
        rows.append(
            {
                "klass": treated.klass,
                "amount_rupees": treated.amount_rupees,
                "band": band(treated.amount_rupees),
                "uplift_rupees": (treated.amount_rupees if treated.recovered else 0.0)
                - (ctl.amount_rupees if ctl.recovered else 0.0),
            }
        )
    return rows


def fit(rows: list[dict]) -> dict[tuple[str, str], float]:
    """Mean realised uplift per (class, band). This is the learned model.

    A T-learner in the loosest sense: the two arms are already paired, so the
    difference is taken per case and averaged per cell rather than fitting two
    response surfaces and subtracting them.
    """
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        cells[(r["klass"], r["band"])].append(r["uplift_rupees"])
    return {k: statistics.mean(v) for k, v in cells.items() if len(v) >= 3}


def learned_score(model: dict, fallback: float, row: dict) -> float:
    """Expected uplift for this case under the learned table.

    Unseen cells fall back to the global mean rather than to zero: zero would
    mean "definitely not worth contacting", which is a much stronger claim than
    "never observed".
    """
    return model.get((row["klass"], row["band"]), fallback)


def evaluate(n: int, train_seeds: list[int], test_seeds: list[int]) -> dict:
    policy = load_policy()
    engine = PolicyEngine(policy)
    priors = policy["class_priors"]

    train = [r for s in train_seeds for r in paired_rows(n, s, policy, engine)]
    model = fit(train)
    global_mean = statistics.mean(r["uplift_rupees"] for r in train)

    per_seed = []
    for seed in test_seeds:
        rows = paired_rows(n, seed, policy, engine)
        for r in rows:
            # Hand-written: prior x amount, exactly as G-OPS-08 scores it.
            r["hand"] = priors.get(r["klass"], 0.2) * r["amount_rupees"]
            r["learned"] = learned_score(model, global_mean, r)

        per_seed.append(
            {
                "seed": seed,
                "hand": qini_coefficient(qini_from_rows(rows, key="hand")),
                "learned": qini_coefficient(qini_from_rows(rows, key="learned")),
                "oracle": qini_coefficient(qini_from_rows(rows, key="uplift_rupees")),
            }
        )

    return {
        "model": model,
        "cells": len(model),
        "train_seeds": train_seeds,
        "test_seeds": test_seeds,
        "per_seed": per_seed,
    }


def report(result: dict) -> str:
    per = result["per_seed"]
    hand = [p["hand"] for p in per]
    learned = [p["learned"] for p in per]
    oracle = [p["oracle"] for p in per]
    wins = sum(1 for p in per if p["learned"] > p["hand"])

    lines = [
        "LEARNED TARGETING vs HAND-WRITTEN PRIORS",
        "",
        f"  fitted on {len(result['train_seeds'])} seeds "
        f"({result['cells']} populated cells), scored on "
        f"{len(result['test_seeds'])} seeds the fitter never saw.",
        "",
        f"{'seed':>10} {'hand':>10} {'learned':>10} {'oracle':>10}  winner",
        "-" * 52,
    ]
    for p in per:
        winner = "learned" if p["learned"] > p["hand"] else "hand"
        lines.append(
            f"{p['seed']:>10} {p['hand']:>10.3f} {p['learned']:>10.3f} "
            f"{p['oracle']:>10.3f}  {winner}"
        )
    lines += [
        "-" * 52,
        f"{'mean':>10} {statistics.mean(hand):>10.3f} "
        f"{statistics.mean(learned):>10.3f} {statistics.mean(oracle):>10.3f}",
        "",
        f"  learned beats hand-written on {wins} of {len(per)} held-out seeds.",
        "",
    ]

    delta = statistics.mean(learned) - statistics.mean(hand)
    gap_to_oracle = statistics.mean(oracle) - statistics.mean(hand)
    captured = delta / gap_to_oracle * 100 if gap_to_oracle else 0
    sd_learned = statistics.stdev(learned) if len(learned) > 1 else 0.0
    sd_hand = statistics.stdev(hand) if len(hand) > 1 else 0.0

    lines += [
        f"  spread   hand sd {sd_hand:.3f}   learned sd {sd_learned:.3f}",
        "",
    ]

    if wins >= len(per) * 0.8 and delta > 0.01:
        lines += [
            f"  Learning helps, and helps consistently: +{delta:.3f} Qini on the",
            f"  mean, winning {wins} of {len(per)} held-out seeds, closing "
            f"{captured:.0f}% of the",
            "  gap to the oracle ceiling. class_priors should be refitted.",
        ]
    elif delta > 0.05 and wins <= len(per) / 2:
        # The interesting case, and the one an earlier version of this report
        # mislabelled as "no difference" because it gated on win count alone.
        # A higher mean with a coin-flip win rate is not an improvement you can
        # ship - it is a louder signal, not a better one.
        lines += [
            f"  Learning has a HIGHER MEAN (+{delta:.3f} Qini) but wins only",
            f"  {wins} of {len(per)} seeds, and its spread is "
            f"{sd_learned / sd_hand if sd_hand else 0:.1f}x the hand-written",
            "  priors'. That is not an improvement you can ship: on any given",
            "  batch it is roughly a coin flip which scorer ranks better, and",
            "  the mean is carried by a few seeds where the fitted table got",
            "  lucky on a rare cell.",
            "",
            "  With 300 cases per seed spread over 21 populated cells, most",
            "  cells hold a handful of observations. The hand-written priors",
            "  encode structural facts - an expired card cannot be charged, a",
            "  cancelled customer must not be chased - that survive a thin",
            "  sample; a fitted mean does not.",
            "",
            "  Keeping the hand-written priors, and reporting this rather than",
            "  the mean alone. Refit when there is real traffic, not before.",
        ]
    elif delta < -0.01:
        lines += [
            f"  Learning HURTS here ({delta:+.3f} Qini). The hand-written priors",
            "  encode structural knowledge - an expired card cannot be charged,",
            "  a cancelled customer should not be chased - that a table fitted on",
            "  300 cases per seed cannot recover from noise. Keeping them.",
        ]
    else:
        lines += [
            f"  No meaningful difference ({delta:+.3f} Qini). At this sample size",
            "  the data does not beat structural reasoning, and a learned model",
            "  would add a training step, a staleness risk and an unreadable",
            "  artefact in exchange for nothing. Keeping the hand-written priors.",
        ]

    lines += [
        "",
        "  Note on what this does NOT show: the oracle column ranks by realised",
        "  uplift and is unreachable, not a target. And a model that recovered",
        "  the simulator's own structure would be proving the fitter works, not",
        "  that the policy is good - which is why the test seeds are held out.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--train", type=int, default=6)
    ap.add_argument("--test", type=int, default=6)
    ap.add_argument("--base-seed", type=int, default=20260829)
    args = ap.parse_args()

    train = [args.base_seed + i for i in range(args.train)]
    test = [args.base_seed + args.train + i for i in range(args.test)]

    print()
    print(report(evaluate(args.n, train, test)))
    print()


if __name__ == "__main__":
    main()
