"""Head-to-head against the competing strategies, on equal footing.

    python -m eval.compare_systems --seeds 12

The prior-art review found that reason-aware retrying and smart timing are
commodity. That raises the only question that matters: **how much of Chukta's
measured gain is commodity, and how much is the part that is actually new?**

A marketing-claims table cannot answer that. Running the strategies as arms
can. Four arms, each adding one capability:

    blind          fixed ladder, reason-blind
    smart_timing   optimal timing, reason-blind        (the Stripe-style claim)
    reason_aware   + reason-aware ladder, unbounded    (modern dunning tools)
    chukta          + bounded contact and stopping      (the contribution)

Every arm runs through the identical harness, over the identical seeded
population, under common random numbers, behind the identical compliance
gates. The only thing that varies is the strategy.

**What this is not.** These are approximations of published *strategies*, not
reproductions of anyone's product. Stripe trains on 500+ attributes across
billions of payments; `SmartTimingPolicy` is handed the salary-day effect for
free, which is more than a real model gets. Beating it here is not beating
Stripe - it is measuring what timing alone is worth on this population.

Read the deltas between adjacent arms, never the totals.
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass

from sim.baselines import ARM_LABELS, ARM_ORDER, build_arms, policy_for_arm
from sim.corpus import build_corpus
from sim.population import build_population
from sim.run import START, run_case
from chukta.policy import PolicyEngine, load_policy

from .sweep import NullAudit


@dataclass
class ArmResult:
    arm: str
    recovered_rupees: float
    recovered: int
    attempts: int
    contacts: int
    churned: int
    blocked: int
    hours_p50: float | None
    hours_p95: float | None


def run_arm(arm: str, n: int, seed: int, base_policy: dict, engine: PolicyEngine) -> ArmResult:
    people = build_population(n, seed=seed)
    events = build_corpus(people, START, seed=seed)
    by_id = {p.customer_id: p for p in people}
    decider = build_arms(engine, seed)[arm]
    policy = policy_for_arm(base_policy, arm)
    audit = NullAudit()

    outcomes = [
        run_case(
            by_id[e.customer_id], e, arm, engine, None, policy, audit, decider=decider
        )
        for e in events
    ]

    times = sorted(o.hours_to_recovery for o in outcomes if o.hours_to_recovery)

    def pct(p: int) -> float | None:
        if not times:
            return None
        k = max(0, min(len(times) - 1, int(round(p / 100 * len(times) + 0.5)) - 1))
        return times[k]

    return ArmResult(
        arm=arm,
        recovered_rupees=sum(o.amount_rupees for o in outcomes if o.recovered),
        recovered=sum(1 for o in outcomes if o.recovered),
        attempts=sum(o.attempts for o in outcomes),
        contacts=sum(o.contacts for o in outcomes),
        churned=sum(1 for o in outcomes if o.churned),
        blocked=sum(o.blocked_actions for o in outcomes),
        hours_p50=pct(50),
        hours_p95=pct(95),
    )


def compare(n: int, seeds: list[int]) -> dict[str, list[ArmResult]]:
    base_policy = load_policy()
    engine = PolicyEngine(base_policy)
    out: dict[str, list[ArmResult]] = {a: [] for a in ARM_ORDER}
    for seed in seeds:
        for arm in ARM_ORDER:
            out[arm].append(run_arm(arm, n, seed, base_policy, engine))
    return out


def _mean(rows: list[ArmResult], field: str) -> float:
    vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
    return statistics.mean(vals) if vals else 0.0


def report(results: dict[str, list[ArmResult]], n: int, seeds: list[int]) -> str:
    lines = [
        f"HEAD TO HEAD - {len(seeds)} seeds x n={n}, common random numbers, "
        "identical gates",
        "",
        f"{'arm':<15} {'recovered':>12} {'attempts':>9} {'contacts':>9} "
        f"{'churn':>6} {'p50':>7} {'p95':>7}",
        "-" * 72,
    ]
    for arm in ARM_ORDER:
        rows = results[arm]
        lines.append(
            f"{arm:<15} {_mean(rows,'recovered_rupees'):>12,.0f} "
            f"{_mean(rows,'attempts'):>9,.0f} {_mean(rows,'contacts'):>9,.0f} "
            f"{_mean(rows,'churned'):>6,.1f} "
            f"{_mean(rows,'hours_p50'):>6,.0f}h {_mean(rows,'hours_p95'):>6,.0f}h"
        )

    lines += ["", "WHERE THE GAIN COMES FROM - each row is one added capability", ""]
    prev = None
    for arm in ARM_ORDER:
        cur = _mean(results[arm], "recovered_rupees")
        if prev is not None:
            delta = cur - prev
            contacts = _mean(results[arm], "contacts") - prev_contacts
            attribution = (
                "commodity - every serious vendor ships this"
                if arm in ("smart_timing", "reason_aware")
                else "this project"
            )
            lines.append(
                f"  -> {arm:<14} {delta:>+11,.0f}   contacts {contacts:>+7,.0f}   "
                f"{attribution}"
            )
        prev, prev_contacts = cur, _mean(results[arm], "contacts")

    blind = _mean(results["blind"], "recovered_rupees")
    chukta = _mean(results["chukta"], "recovered_rupees")
    reason = _mean(results["reason_aware"], "recovered_rupees")
    total = chukta - blind
    ours = chukta - reason

    lines += [
        "",
        f"  total gain over the blind ladder      {total:>+11,.0f}",
        f"  attributable to this project          {ours:>+11,.0f}"
        f"  ({ours / total * 100 if total else 0:.0f}% of it)",
        "",
    ]

    c_reason = _mean(results["reason_aware"], "contacts")
    c_chukta = _mean(results["chukta"], "contacts")
    ch_reason = _mean(results["reason_aware"], "churned")
    ch_chukta = _mean(results["chukta"], "churned")
    churn_avoided = ch_reason - ch_chukta

    # The comparison is only honest if it can return a verdict against us.
    if ours > 0:
        lines.append("  Bounded contact adds revenue over a reason-aware baseline.")
    else:
        lines += [
            "  Bounded contact does NOT add revenue over the reason-aware",
            "  baseline. It gives revenue up. What it buys is restraint:",
            f"    contacts {c_chukta - c_reason:+,.0f}   "
            f"({(c_chukta / c_reason - 1) * 100 if c_reason else 0:+.0f}%)",
            f"    churn    {ch_chukta - ch_reason:+.1f}   "
            f"({(ch_chukta / ch_reason - 1) * 100 if ch_reason else 0:+.0f}%)",
        ]

    # A 14-day horizon prices a cancellation at one missed cycle. That is
    # wrong: a cancelled subscriber stops paying forever. Rather than pick a
    # lifetime value and let it do the arguing, report the break-even and let
    # the reader decide whether their customers clear it.
    if churn_avoided > 0 and ours < 0:
        breakeven = -ours / churn_avoided
        lines += [
            "",
            "  THE HORIZON UNDERCOUNTS THIS. Fourteen days prices a",
            "  cancellation at one missed payment; a cancelled subscriber",
            "  stops paying forever. Break-even on the trade:",
            "",
            f"    give up   {-ours:>10,.0f}  revenue in the window",
            f"    to avoid  {churn_avoided:>10.1f}  cancellations",
            f"    ------------------------------------------------",
            f"    pays off if a retained customer is worth more than",
            f"    Rs {breakeven:,.0f} in future billing.",
            "",
            "  That is the number to argue about. It is not claimed here that",
            "  it is cleared - measuring it needs retention data this project",
            "  does not have.",
        ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--base-seed", type=int, default=20260829)
    args = ap.parse_args()

    seeds = [args.base_seed + i for i in range(args.seeds)]
    print()
    print(report(compare(args.n, seeds), args.n, seeds))
    print()


if __name__ == "__main__":
    main()
