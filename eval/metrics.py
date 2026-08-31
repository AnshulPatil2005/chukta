"""Aggregate metrics for a run.

The headline is incremental recovery, not gross recovery rate. Gross rate
counts the customers who would have paid anyway - which, on this population,
is most of them. Reporting it alone is the standard way dunning numbers get
inflated, so the breakdown by `recovered_by` is printed right next to it.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

ARMS = ("control", "chukta")


def _pct(values: list[float], p: int) -> float | None:
    """Nearest-rank percentile. Small n here, so no interpolation games."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100 * len(ordered) + 0.5)) - 1))
    return ordered[k]


def load_run(path: str | Path = "runs/latest.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def arm_summary(cases: list[dict]) -> dict[str, Any]:
    n = len(cases)
    recovered = [c for c in cases if c["recovered"]]
    by_agent = [c for c in recovered if c["recovered_by"] != "self"]
    contacts = sum(c["contacts"] for c in cases)
    attempts = sum(c["attempts"] for c in cases)
    churned = [c for c in cases if c["churned"]]
    times = [c["hours_to_recovery"] for c in recovered if c["hours_to_recovery"]]

    return {
        "cases": n,
        "recovered": len(recovered),
        "recovery_rate": len(recovered) / n if n else 0.0,
        # Tails, not just the median. A policy that recovers most cases in a
        # day and strands the rest for a fortnight has a very different felt
        # experience from one that averages the same - and the mean hides it.
        "hours_p50": _pct(times, 50),
        "hours_p95": _pct(times, 95),
        "hours_p99": _pct(times, 99),
        "recovered_rupees": sum(c["amount_rupees"] for c in recovered),
        # The share of "recoveries" the policy can actually claim credit for.
        "recovered_by_action": len(by_agent),
        "recovered_by_self": len(recovered) - len(by_agent),
        "attributable_rupees": sum(c["amount_rupees"] for c in by_agent),
        "contacts": contacts,
        "attempts": attempts,
        "churned": len(churned),
        "churn_rate": len(churned) / n if n else 0.0,
        "blocked_actions": sum(c["blocked_actions"] for c in cases),
        "contacts_per_recovery": contacts / len(recovered) if recovered else float("inf"),
        "attempts_per_recovery": attempts / len(recovered) if recovered else float("inf"),
        "median_hours_to_recovery": statistics.median(times) if times else None,
    }


def compare(run: dict) -> dict[str, Any]:
    arms = {arm: arm_summary(run["arms"][arm]) for arm in ARMS}
    control, chukta = arms["control"], arms["chukta"]

    incremental_rupees = chukta["recovered_rupees"] - control["recovered_rupees"]
    incremental_cases = chukta["recovered"] - control["recovered"]

    return {
        "arms": arms,
        "incremental": {
            "rupees": incremental_rupees,
            "cases": incremental_cases,
            "recovery_rate_pp": (chukta["recovery_rate"] - control["recovery_rate"]) * 100,
            "contacts_delta": chukta["contacts"] - control["contacts"],
            "churn_delta": chukta["churned"] - control["churned"],
            "rupees_per_extra_contact": (
                incremental_rupees / (chukta["contacts"] - control["contacts"])
                if chukta["contacts"] != control["contacts"]
                else None
            ),
        },
        "by_quadrant": _by_quadrant(run),
    }


def _by_quadrant(run: dict) -> dict[str, dict[str, Any]]:
    """Ground-truth diagnostic. Never available in production - this is the
    simulator marking its own homework, and is labelled as such in the report."""
    out: dict[str, dict[str, Any]] = {}
    control = {c["event_id"]: c for c in run["arms"]["control"]}
    for case in run["arms"]["chukta"]:
        q = case["quadrant"]
        row = out.setdefault(
            q, {"n": 0, "uplift_rupees": 0.0, "extra_contacts": 0, "extra_churn": 0}
        )
        ctl = control[case["event_id"]]
        row["n"] += 1
        row["uplift_rupees"] += (
            case["amount_rupees"] if case["recovered"] else 0.0
        ) - (ctl["amount_rupees"] if ctl["recovered"] else 0.0)
        row["extra_contacts"] += case["contacts"] - ctl["contacts"]
        row["extra_churn"] += int(case["churned"]) - int(ctl["churned"])
    return out


def _h(x: float | None) -> str:
    return f"{x:,.0f}h" if x is not None else "-"


def _money(x: float) -> str:
    return f"Rs {x:,.0f}"


def report(run: dict) -> str:
    c = compare(run)
    control, chukta = c["arms"]["control"], c["arms"]["chukta"]
    inc = c["incremental"]

    lines = [
        f"run {run['run_id']}  |  n={run['n']}  seed={run['seed']}  "
        f"horizon={run['horizon_days']}d",
        "",
        f"{'':28} {'control':>14} {'chukta':>14}",
        "-" * 58,
        f"{'gross recovery rate':28} {control['recovery_rate']:>13.1%} "
        f"{chukta['recovery_rate']:>13.1%}",
        f"{'  of which self-recovered':28} {control['recovered_by_self']:>13} "
        f"{chukta['recovered_by_self']:>13}",
        f"{'  attributable to policy':28} {control['recovered_by_action']:>13} "
        f"{chukta['recovered_by_action']:>13}",
        f"{'recovered':28} {_money(control['recovered_rupees']):>13} "
        f"{_money(chukta['recovered_rupees']):>13}",
        "-" * 58,
        f"{'customer contacts':28} {control['contacts']:>13} {chukta['contacts']:>13}",
        f"{'charge attempts':28} {control['attempts']:>13} {chukta['attempts']:>13}",
        f"{'contacts per recovery':28} {control['contacts_per_recovery']:>13.2f} "
        f"{chukta['contacts_per_recovery']:>13.2f}",
        f"{'churned (outreach-induced)':28} {control['churned']:>13} "
        f"{chukta['churned']:>13}",
        f"{'hours to recovery  p50':28} {_h(control['hours_p50']):>13} "
        f"{_h(chukta['hours_p50']):>13}",
        f"{'                   p95':28} {_h(control['hours_p95']):>13} "
        f"{_h(chukta['hours_p95']):>13}",
        f"{'                   p99':28} {_h(control['hours_p99']):>13} "
        f"{_h(chukta['hours_p99']):>13}",
        f"{'actions blocked by gates':28} {control['blocked_actions']:>13} "
        f"{chukta['blocked_actions']:>13}",
        "",
        "INCREMENTAL (chukta - control, same seeded population, common random numbers)",
        f"  revenue          {_money(inc['rupees'])}",
        f"  cases            {inc['cases']:+d}",
        f"  recovery rate    {inc['recovery_rate_pp']:+.1f} pp",
        f"  contacts         {inc['contacts_delta']:+d}",
        f"  churn            {inc['churn_delta']:+d}",
        "",
        "uplift by quadrant (simulator ground truth - diagnostic only)",
    ]
    for q, row in sorted(c["by_quadrant"].items()):
        lines.append(
            f"  {q:14} n={row['n']:>4}  uplift={_money(row['uplift_rupees']):>14}  "
            f"contacts={row['extra_contacts']:+4d}  churn={row['extra_churn']:+3d}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(load_run()))
