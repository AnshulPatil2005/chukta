"""Robustness sweep: does the result survive a different seed, or a different
belief about how customers behave?

A single run on a single seed with a single parameterisation is an anecdote.
Two questions have to be separated:

  SAMPLING NOISE - would a different draw of 300 customers reverse this?
      Answered by re-running across seeds. If the sign flips across seeds, the
      headline number is noise and should not be reported at all.

  MODEL SENSITIVITY - does the conclusion depend on the response-model
      constants I chose? Those constants are calibration anchors taken from
      vendor-published figures, not measurements. If the conclusion only holds
      at exactly those values, the conclusion is about sim/response_model.py
      and not about the policy.

The second is the one that matters. Anyone can tune a simulator until their
agent wins. The defensible claim is that the ordering holds across a range of
plausible beliefs - and if it does not, that has to be reported.

    python -m eval.sweep --seeds 12
    python -m eval.sweep --seeds 8 --sensitivity
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass

from sim import response_model as rm
from sim.baselines import policy_for_arm
from sim.control_policy import BlindRetryPolicy
from sim.corpus import build_corpus
from sim.population import build_population
from sim.run import START, run_case
from chukta.policy import PolicyEngine, load_policy


class NullAudit:
    """The sweep runs hundreds of cases and the audit trail is not the object
    of study here. Writing it would produce megabytes nobody reads."""

    def write(self, decision) -> None:
        pass

    def note(self, kind: str, **fields) -> None:
        pass

    def __len__(self) -> int:
        return 0


@dataclass
class SweepResult:
    seed: int
    incremental_rupees: float
    incremental_cases: int
    extra_contacts: int
    extra_churn: int
    control_rupees: float
    chukta_rupees: float

    @property
    def rupees_per_extra_contact(self) -> float:
        return (
            self.incremental_rupees / self.extra_contacts
            if self.extra_contacts
            else float("inf")
        )


def one_run(n: int, seed: int, policy: dict, engine: PolicyEngine) -> SweepResult:
    people = build_population(n, seed=seed)
    events = build_corpus(people, START, seed=seed)
    by_id = {p.customer_id: p for p in people}
    audit = NullAudit()

    totals = {}
    for arm in ("control", "chukta"):
        blind = BlindRetryPolicy(seed=seed)
        # G-OPS-08 is the thing under evaluation. Giving it to the control arm
        # would measure it against itself - see sim/run.py for the same guard.
        arm_policy = policy_for_arm(policy, "chukta" if arm == "chukta" else "control")
        outcomes = [
            run_case(by_id[e.customer_id], e, arm, engine, blind, arm_policy, audit)
            for e in events
        ]
        totals[arm] = {
            "rupees": sum(o.amount_rupees for o in outcomes if o.recovered),
            "cases": sum(1 for o in outcomes if o.recovered),
            "contacts": sum(o.contacts for o in outcomes),
            "churn": sum(1 for o in outcomes if o.churned),
        }

    c, w = totals["control"], totals["chukta"]
    return SweepResult(
        seed=seed,
        incremental_rupees=w["rupees"] - c["rupees"],
        incremental_cases=w["cases"] - c["cases"],
        extra_contacts=w["contacts"] - c["contacts"],
        extra_churn=w["churn"] - c["churn"],
        control_rupees=c["rupees"],
        chukta_rupees=w["rupees"],
    )


def seed_sweep(n: int, seeds: list[int]) -> list[SweepResult]:
    policy = load_policy()
    engine = PolicyEngine(policy)
    return [one_run(n, s, policy, engine) for s in seeds]


def _stats(values: list[float]) -> tuple[float, float, float, float]:
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, sd, min(values), max(values)


def report_seeds(results: list[SweepResult], n: int) -> str:
    inc = [r.incremental_rupees for r in results]
    contacts = [float(r.extra_contacts) for r in results]
    churn = [float(r.extra_churn) for r in results]

    mean, sd, lo, hi = _stats(inc)
    positive = sum(1 for v in inc if v > 0)
    # Standard error of the mean across independent seeds. This is a statement
    # about the simulator, not about Razorpay merchants.
    sem = sd / (len(inc) ** 0.5) if len(inc) > 1 else 0.0

    lines = [
        f"SEED SWEEP - {len(results)} seeds x n={n}",
        "",
        f"{'seed':>10} {'incremental':>14} {'contacts':>10} {'churn':>7} "
        f"{'Rs/contact':>12}",
        "-" * 58,
    ]
    for r in results:
        lines.append(
            f"{r.seed:>10} {r.incremental_rupees:>14,.0f} {r.extra_contacts:>10} "
            f"{r.extra_churn:>7} {r.rupees_per_extra_contact:>12,.0f}"
        )
    lines += [
        "-" * 58,
        f"  incremental revenue   mean {mean:>12,.0f}   sd {sd:>10,.0f}",
        f"                        range [{lo:,.0f}, {hi:,.0f}]",
        f"                        95% CI on the mean approx "
        f"[{mean - 1.96 * sem:,.0f}, {mean + 1.96 * sem:,.0f}]",
        f"  positive in {positive} of {len(inc)} seeds",
        f"  extra contacts        mean {_stats(contacts)[0]:>12,.0f}",
        f"  extra churn           mean {_stats(churn)[0]:>12,.1f}",
    ]
    if positive < len(inc):
        lines.append("")
        lines.append(
            "  WARNING: the sign is not stable across seeds. The headline "
            "number is noise."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# sensitivity
# --------------------------------------------------------------------------

# Each entry perturbs one belief in sim/response_model.py. The point is not
# that these values are right - it is to find out whether the conclusion
# depends on them.
SCENARIOS: dict[str, dict] = {
    "baseline": {},
    "outreach barely works": {"CONTACT_SCALE": 0.5},
    "outreach works better": {"CONTACT_SCALE": 1.5},
    "customers churn twice as fast": {"CHURN_SCALE": 2.0},
    "customers rarely churn": {"CHURN_SCALE": 0.25},
    "retry timing matters less": {"TIMING_SCALE": 0.5},
    "message frames do nothing": {"FRAME_SCALE": 0.0},
}


def _apply(scenario: dict) -> dict:
    """Scale the published anchors, returning the originals for restoration."""
    saved = {
        "CONTACT_RELEVANCE": dict(rm.CONTACT_RELEVANCE),
        "FRAME_LIFT": dict(rm.FRAME_LIFT),
        "RETRY_24H_OVER_2H_RELATIVE_LIFT": rm.RETRY_24H_OVER_2H_RELATIVE_LIFT,
        "EXTRA_RETRIES_RELATIVE_LIFT": rm.EXTRA_RETRIES_RELATIVE_LIFT,
    }
    if "CONTACT_SCALE" in scenario:
        k = scenario["CONTACT_SCALE"]
        rm.CONTACT_RELEVANCE = {
            key: min(1.0, v * k) for key, v in saved["CONTACT_RELEVANCE"].items()
        }
    if "FRAME_SCALE" in scenario:
        k = scenario["FRAME_SCALE"]
        rm.FRAME_LIFT = {key: v * k for key, v in saved["FRAME_LIFT"].items()}
    if "TIMING_SCALE" in scenario:
        k = scenario["TIMING_SCALE"]
        rm.RETRY_24H_OVER_2H_RELATIVE_LIFT = (
            saved["RETRY_24H_OVER_2H_RELATIVE_LIFT"] * k
        )
        rm.EXTRA_RETRIES_RELATIVE_LIFT = saved["EXTRA_RETRIES_RELATIVE_LIFT"] * k
    return saved


def _restore(saved: dict) -> None:
    rm.CONTACT_RELEVANCE = saved["CONTACT_RELEVANCE"]
    rm.FRAME_LIFT = saved["FRAME_LIFT"]
    rm.RETRY_24H_OVER_2H_RELATIVE_LIFT = saved["RETRY_24H_OVER_2H_RELATIVE_LIFT"]
    rm.EXTRA_RETRIES_RELATIVE_LIFT = saved["EXTRA_RETRIES_RELATIVE_LIFT"]


def sensitivity(n: int, seeds: list[int]) -> str:
    lines = [
        f"SENSITIVITY - each row re-runs all {len(seeds)} seeds with one belief "
        "in sim/response_model.py perturbed",
        "",
        f"{'scenario':<32} {'mean incremental':>18} {'seeds +ve':>11}",
        "-" * 63,
    ]
    fragile: list[tuple[str, float, int]] = []
    baseline_positive: int | None = None
    for name, scenario in SCENARIOS.items():
        saved = _apply(scenario)
        try:
            if "CHURN_SCALE" in scenario:
                # Churn lives on the population, not the response model, so it
                # is scaled at draw time instead.
                results = _churn_scaled_sweep(n, seeds, scenario["CHURN_SCALE"])
            else:
                results = seed_sweep(n, seeds)
        finally:
            _restore(saved)

        inc = [r.incremental_rupees for r in results]
        pos = sum(1 for v in inc if v > 0)
        mean = statistics.mean(inc)

        if name == "baseline":
            baseline_positive = pos

        # Fragility is relative to the baseline, not to a perfect 12/12. If the
        # baseline itself loses a seed to noise, a scenario that loses the same
        # seed has told us nothing about that scenario. What matters is a mean
        # that goes negative, or a materially worse positive rate.
        ref = baseline_positive if baseline_positive is not None else len(inc)
        worse = mean < 0 or pos <= ref - 3
        flag = "   <- breaks the result" if worse and name != "baseline" else ""
        lines.append(f"{name:<32} {mean:>18,.0f} {pos:>5} / {len(inc):<3}{flag}")
        if name != "baseline" and worse:
            fragile.append((name, mean, pos))

    lines.append("")
    if not fragile:
        lines.append(
            "  The sign survives every perturbation, so the conclusion is a "
            "property of\n  the policy rather than of the constants. Magnitude "
            "is not - it moves a lot."
        )
    else:
        lines.append("  The conclusion is NOT robust. It depends on:")
        for name, mean, pos in fragile:
            lines.append(f"    - {name}  (mean {mean:,.0f}, positive {pos}/{len(seeds)})")
        lines.append("")
        lines.append(
            "  These are load-bearing assumptions, not incidental parameters.\n"
            "  Reporting the baseline number without them would be misleading."
        )
    return "\n".join(lines)


def _churn_scaled_sweep(n: int, seeds: list[int], k: float) -> list[SweepResult]:
    from sim import population as pop

    saved = dict(pop.QUADRANT_PARAMS)
    scaled = {}
    for q, params in saved.items():
        p = dict(params)
        if "churn" in p:
            p["churn"] = min(1.0, p["churn"] * k)
        scaled[q] = p
    pop.QUADRANT_PARAMS = scaled
    try:
        return seed_sweep(n, seeds)
    finally:
        pop.QUADRANT_PARAMS = saved


def sweep_payload(n: int, seeds: list[int], with_sensitivity: bool) -> dict:
    """Machine-readable form of the same numbers the report prints.

    The dashboard reads this rather than re-running the sweep on request - a
    twelve-seed sensitivity pass takes the better part of a minute, which is
    not something to do inside an HTTP handler.
    """
    results = seed_sweep(n, seeds)
    inc = [r.incremental_rupees for r in results]
    mean, sd, lo, hi = _stats(inc)
    sem = sd / (len(inc) ** 0.5) if len(inc) > 1 else 0.0

    payload = {
        "n": n,
        "seeds": seeds,
        "per_seed": [vars(r) for r in results],
        "mean": mean,
        "sd": sd,
        "min": lo,
        "max": hi,
        "ci95": [mean - 1.96 * sem, mean + 1.96 * sem],
        "positive": sum(1 for v in inc if v > 0),
        "mean_extra_contacts": statistics.mean([r.extra_contacts for r in results]),
        "mean_extra_churn": statistics.mean([r.extra_churn for r in results]),
        "sensitivity": None,
    }

    if with_sensitivity:
        rows = []
        for name, scenario in SCENARIOS.items():
            saved = _apply(scenario)
            try:
                if "CHURN_SCALE" in scenario:
                    res = _churn_scaled_sweep(n, seeds, scenario["CHURN_SCALE"])
                else:
                    res = seed_sweep(n, seeds)
            finally:
                _restore(saved)
            vals = [r.incremental_rupees for r in res]
            rows.append(
                {
                    "scenario": name,
                    "mean": statistics.mean(vals),
                    "positive": sum(1 for v in vals if v > 0),
                    "of": len(vals),
                }
            )
        payload["sensitivity"] = rows
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--base-seed", type=int, default=20260829)
    ap.add_argument("--sensitivity", action="store_true")
    ap.add_argument(
        "--json",
        nargs="?",
        const="runs/sweep.json",
        default=None,
        help="also write machine-readable results here (for the dashboard)",
    )
    args = ap.parse_args()

    seeds = [args.base_seed + i for i in range(args.seeds)]

    print()
    print(report_seeds(seed_sweep(args.n, seeds), args.n))
    print()
    if args.sensitivity:
        print(sensitivity(args.n, seeds))
        print()

    if args.json:
        import json
        from pathlib import Path

        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sweep_payload(args.n, seeds, args.sensitivity), indent=2),
            encoding="utf-8",
        )
        print(f"  -> {path}")
        print()


if __name__ == "__main__":
    main()
