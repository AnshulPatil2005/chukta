"""Assert that the figures quoted in the README still hold.

    python -m eval.check_claims          # single-seed claims (fast)
    python -m eval.check_claims --full   # also the 12-seed sweep

A README is documentation that silently rots: someone tunes a constant, the
numbers move, and the quoted figures stay put looking authoritative. This turns
every quoted figure into a test, so the day a change moves a headline number CI
says so and names which claim broke.

Adding a claim here is the price of quoting a number in the README.
"""

from __future__ import annotations

import argparse
import sys

from sim.baselines import policy_for_arm
from sim.population import build_population
from sim.corpus import build_corpus
from sim.run import START, run_case
from chukta.policy import PolicyEngine, load_policy

from .metrics import compare
from .sweep import NullAudit, seed_sweep

# (label, expected, tolerance). Tolerances are tight: these are deterministic
# given the seed, so anything but rounding slack would hide a real change.
SINGLE_SEED_CLAIMS = [
    ("incremental revenue", 37667.0, 1.0),
    ("incremental recovery rate (pp)", 2.3, 0.05),
    ("control charge attempts", 365, 0),
    ("chukta charge attempts", 151, 0),
    ("extra contacts", 54, 0),
    ("extra churn", 3, 0),
]

# The five-headline table in the README and SUBMISSION.md. If the spread ever
# collapses, the central argument stops being supported by its own data.
VARIANT_CLAIMS = [
    ("gross rupees recovered", 195704.0, 1.0),
    ("attributable rupees", 134177.0, 1.0),
    ("self-recovered share of recoveries (%)", 41.5, 0.2),
]

SWEEP_CLAIMS = [
    ("12-seed mean incremental", 16653.0, 50.0),
    ("seeds with positive incremental", 11, 0),
]


def _single_seed(n: int = 300, seed: int = 20260829) -> dict:
    policy = load_policy()
    engine = PolicyEngine(policy)
    people = build_population(n, seed=seed)
    events = build_corpus(people, START, seed=seed)
    by_id = {p.customer_id: p for p in people}
    audit = NullAudit()

    from .sweep import BlindRetryPolicy

    arms = {}
    for arm in ("control", "chukta"):
        blind = BlindRetryPolicy(seed=seed)
        # The control arm does not get G-OPS-08; see sim/run.py.
        arm_policy = policy_for_arm(policy, "chukta" if arm == "chukta" else "control")
        arms[arm] = [
            {
                k: v
                for k, v in vars(
                    run_case(
                        by_id[e.customer_id], e, arm, engine, blind, arm_policy, audit
                    )
                ).items()
                if k != "log"
            }
            for e in events
        ]
    return compare({"arms": arms})


def check(label: str, actual: float, expected: float, tol: float) -> bool:
    ok = abs(actual - expected) <= tol
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {label:36} expected {expected:>12,.1f}  got {actual:>12,.1f}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also run the 12-seed sweep")
    args = ap.parse_args()

    print("\nREADME claims, seed 20260829, n=300")
    c = _single_seed()
    inc = c["incremental"]
    actual = {
        "incremental revenue": inc["rupees"],
        "incremental recovery rate (pp)": inc["recovery_rate_pp"],
        "control charge attempts": c["arms"]["control"]["attempts"],
        "chukta charge attempts": c["arms"]["chukta"]["attempts"],
        "extra contacts": inc["contacts_delta"],
        "extra churn": inc["churn_delta"],
    }
    passed = [check(k, actual[k], v, t) for k, v, t in SINGLE_SEED_CLAIMS]

    print("\nREADME five-headline table")
    from .metrics import load_run
    from pathlib import Path

    if Path("runs/latest.json").exists():
        run = load_run()
        treated = run["arms"]["chukta"]
        rec = [x for x in treated if x["recovered"]]
        by_self = [x for x in rec if x["recovered_by"] == "self"]
        by_action = [x for x in rec if x["recovered_by"] != "self"]
        va = {
            "gross rupees recovered": sum(x["amount_rupees"] for x in rec),
            "attributable rupees": sum(x["amount_rupees"] for x in by_action),
            "self-recovered share of recoveries (%)": len(by_self) / len(rec) * 100,
        }
        passed += [check(k, va[k], v, t) for k, v, t in VARIANT_CLAIMS]
    else:
        print("  (skipped - no runs/latest.json)")

    if args.full:
        print("\nREADME claims, 12-seed sweep")
        seeds = [20260829 + i for i in range(12)]
        results = seed_sweep(300, seeds)
        vals = [r.incremental_rupees for r in results]
        sweep_actual = {
            "12-seed mean incremental": sum(vals) / len(vals),
            "seeds with positive incremental": sum(1 for v in vals if v > 0),
        }
        passed += [check(k, sweep_actual[k], v, t) for k, v, t in SWEEP_CLAIMS]

    n_bad = passed.count(False)
    print()
    if n_bad:
        print(f"{n_bad} claim(s) in the README no longer hold. Fix the code or")
        print("update the README - but do not leave it saying something untrue.")
        return 1
    print(f"all {len(passed)} claims hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
