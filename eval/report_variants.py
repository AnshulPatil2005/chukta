"""The same run, reported five ways. The spread is the point.

    python -m eval.report_variants

Every number below comes from ONE run of ONE policy over ONE population. No
scenario changes, no tuning, no cherry-picked seed. The only thing that varies
is which denominator you choose and whether you subtract a baseline.

The spread between the most flattering framing and the honest one is roughly
twenty-fold. That is not a hypothetical about other people's marketing - it is
this project's own data, and it is why `eval/metrics.py` reports incremental
recovery and refuses to lead with anything else.

Read this before believing any recovery number, including the ones in this
repository's README.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .metrics import load_run


@dataclass
class Variant:
    label: str
    value: str
    honest: bool
    why: str


def _rupees(x: float) -> str:
    return f"Rs {x:,.0f}"


def variants(run: dict) -> list[Variant]:
    treated = run["arms"]["chukta"]
    control = {c["event_id"]: c for c in run["arms"]["control"]}

    n = len(treated)
    recovered = [c for c in treated if c["recovered"]]
    by_self = [c for c in recovered if c["recovered_by"] == "self"]
    by_action = [c for c in recovered if c["recovered_by"] != "self"]

    # "Pursued" = the agent actually did something. Cases it correctly declined
    # to work - a cancelled customer, a merchant-config bug - vanish from the
    # denominator, which rewards the policy for its own restraint.
    pursued = [c for c in treated if c["attempts"] > 0 or c["contacts"] > 0]
    pursued_recovered = [c for c in pursued if c["recovered"]]

    gross_rupees = sum(c["amount_rupees"] for c in recovered)
    control_rupees = sum(
        c["amount_rupees"] for c in run["arms"]["control"] if c["recovered"]
    )
    incremental = gross_rupees - control_rupees
    attributable = sum(c["amount_rupees"] for c in by_action)

    return [
        Variant(
            "Recovery rate, pursued cases only",
            f"{len(pursued_recovered) / len(pursued) * 100:.1f}%",
            False,
            "Drops every case the agent declined to work from the denominator, "
            "so refusing hard cases raises the score. The most flattering "
            "framing available, and a common one.",
        ),
        Variant(
            "Gross recovery rate, all cases",
            f"{len(recovered) / n * 100:.1f}%",
            False,
            f"Counts the {len(by_self)} customers who paid unaided as wins. "
            "This is the number most dunning tools publish.",
        ),
        Variant(
            "Total rupees recovered",
            _rupees(gross_rupees),
            False,
            "A big, real, meaningless number. No baseline, so it answers "
            "'how much money arrived', not 'how much did we cause'.",
        ),
        Variant(
            "Rupees attributable to an action",
            _rupees(attributable),
            False,
            "Better - excludes self-recovery. Still wrong: some of those "
            "customers would have paid anyway, just later.",
        ),
        Variant(
            "INCREMENTAL vs control arm",
            _rupees(incremental),
            True,
            "The honest one. Same population, same random draws, different "
            "policy. It is the only framing that answers what the agent CAUSED.",
        ),
    ]


def report(run: dict) -> str:
    v = variants(run)
    treated = run["arms"]["chukta"]
    recovered = [c for c in treated if c["recovered"]]
    by_self = [c for c in recovered if c["recovered_by"] == "self"]

    lines = [
        "ONE RUN, FIVE HEADLINES",
        "",
        f"  n={len(treated)}  seed={run['seed']}  one policy, one population.",
        "  Nothing below is a different experiment. Only the framing changes.",
        "",
        f"{'framing':<38} {'number':>14}",
        "-" * 56,
    ]
    for item in v:
        mark = "  <- honest" if item.honest else ""
        lines.append(f"{item.label:<38} {item.value:>14}{mark}")

    lines += ["", "WHY EACH ONE IS WHAT IT IS", ""]
    for item in v:
        lines.append(f"  {item.label}")
        for chunk in _wrap(item.why, 66):
            lines.append(f"    {chunk}")
        lines.append("")

    self_share = len(by_self) / len(recovered) * 100 if recovered else 0
    lines += [
        "-" * 56,
        f"  {len(by_self)} of {len(recovered)} 'recoveries' ({self_share:.0f}%) were customers who",
        "  paid without the agent doing anything. Every framing except the last",
        "  counts them as a win.",
        "",
        "  The spread between the most flattering framing and the honest one is",
        "  the reason this project reports incremental recovery and refuses to",
        "  lead with anything else - including in its own README.",
    ]
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="runs/latest.json")
    args = ap.parse_args()
    print()
    print(report(load_run(args.run)))
    print()


if __name__ == "__main__":
    main()
