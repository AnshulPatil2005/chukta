"""Derive the outreach threshold instead of choosing it.

    python -m eval.tune_threshold --seeds 6

`policy.yaml` carries `min_uplift_score`, the expected-uplift level below which
Chukta will not contact a customer. Picking that by hand is how the previous
`contact_budget: 2` ended up inert - it looked principled and constrained
nothing.

This sweeps the threshold across seeds and prints the frontier, so the value in
the policy file can be traced to a measurement. There is no single right
answer: the threshold trades revenue against contacts and cancellations, and
which trade a merchant wants is a business decision, not a technical one. What
this can do is show the shape and rule out the settings that are strictly
worse.

The selected value should then be pinned in `eval/check_claims.py` so a change
that silently moves it fails CI.
"""

from __future__ import annotations

import argparse
import copy
import statistics
from dataclasses import dataclass

from sim.corpus import build_corpus
from sim.population import build_population
from sim.run import START, run_case
from chukta.policy import PolicyEngine, load_policy

from .sweep import NullAudit

CANDIDATES = [0, 200, 374, 511, 599, 696, 814, 987, 1585, 3000]


@dataclass
class Point:
    threshold: float
    revenue: float
    contacts: float
    churn: float
    blocked: float

    @property
    def revenue_per_contact(self) -> float:
        return self.revenue / self.contacts if self.contacts else float("inf")


def run_at(threshold: float, n: int, seeds: list[int]) -> Point:
    base = load_policy()
    policy = copy.deepcopy(base)
    policy["defaults"]["min_uplift_score"] = threshold
    engine = PolicyEngine(policy)
    audit = NullAudit()

    rev, con, chu, blk = [], [], [], []
    for seed in seeds:
        people = build_population(n, seed=seed)
        events = build_corpus(people, START, seed=seed)
        by_id = {p.customer_id: p for p in people}
        outcomes = [
            run_case(by_id[e.customer_id], e, "chukta", engine, None, policy, audit)
            for e in events
        ]
        rev.append(sum(o.amount_rupees for o in outcomes if o.recovered))
        con.append(sum(o.contacts for o in outcomes))
        chu.append(sum(1 for o in outcomes if o.churned))
        blk.append(sum(o.blocked_actions for o in outcomes))

    return Point(
        threshold=threshold,
        revenue=statistics.mean(rev),
        contacts=statistics.mean(con),
        churn=statistics.mean(chu),
        blocked=statistics.mean(blk),
    )


def report(points: list[Point], baseline: Point) -> str:
    lines = [
        "OUTREACH THRESHOLD FRONTIER",
        "",
        f"{'threshold':>10} {'revenue':>12} {'contacts':>9} {'churn':>7} "
        f"{'Rs/contact':>11} {'vs no threshold':>16}",
        "-" * 70,
    ]
    for p in points:
        delta = p.revenue - baseline.revenue
        lines.append(
            f"{p.threshold:>10,.0f} {p.revenue:>12,.0f} {p.contacts:>9,.0f} "
            f"{p.churn:>7,.1f} {p.revenue_per_contact:>11,.0f} {delta:>+16,.0f}"
        )

    # The defensible pick: the highest threshold that still keeps most of the
    # revenue, since every contact avoided is a cancellation risk avoided.
    keepers = [p for p in points if p.revenue >= 0.97 * baseline.revenue]
    best = max(keepers, key=lambda p: p.threshold) if keepers else baseline
    lines += [
        "",
        f"  Highest threshold retaining >=97% of revenue: {best.threshold:,.0f}",
        f"    revenue {best.revenue:,.0f} ({best.revenue - baseline.revenue:+,.0f})",
        f"    contacts {best.contacts:,.0f} ({best.contacts - baseline.contacts:+,.0f})",
        f"    churn {best.churn:.1f} ({best.churn - baseline.churn:+.1f})",
        "",
        "  Put this in policy.yaml and pin it in eval/check_claims.py.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--base-seed", type=int, default=20260829)
    args = ap.parse_args()

    seeds = [args.base_seed + i for i in range(args.seeds)]
    points = [run_at(t, args.n, seeds) for t in CANDIDATES]
    print()
    print(report(points, points[0]))
    print()


if __name__ == "__main__":
    main()
