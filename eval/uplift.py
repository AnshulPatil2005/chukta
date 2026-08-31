"""Qini curve and the targeting threshold derived from it.

Qini (Radcliffe & Surry 2011) generalises the Gini coefficient to incremental
response: order the population by a targeting score, then plot cumulative
incremental gain against the fraction targeted. The curve rises while you are
still reaching persuadables, flattens over sure things and lost causes, and
turns DOWN once you are into sleeping dogs. Its peak is the fraction worth
targeting - which is where the contact budget in policy.yaml should come from
instead of being picked by hand.

Both arms here contain the identical case set under common random numbers, so
the usual treatment/control size correction reduces to a plain difference.
That is a simulation luxury and is stated as such - on real logged data the
scaled form is required.

Three curves are produced:
  model   ranked by a score the agent could compute at decision time
  oracle  ranked by realised uplift; the achievable ceiling, not a result
  random  the diagonal
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

def _load_class_priors() -> dict[str, float]:
    """Read the priors from policy.yaml rather than restating them.

    These used to be duplicated here. That is the same second-source-of-truth
    hazard the dashboard has to avoid: the scoring used to rank cases for the
    Qini curve MUST be the scoring the G-OPS-08 gate applies, or the curve is
    measuring a policy that is not the one running.
    """
    from chukta.policy import load_policy

    return load_policy()["class_priors"]


CLASS_PRIOR = _load_class_priors()


@dataclass
class QiniCurve:
    fractions: list[float]
    cumulative: list[float]
    peak_fraction: float
    peak_value: float
    final_value: float

    @property
    def value_left_on_table(self) -> float:
        """What targeting the whole file costs versus stopping at the peak."""
        return self.peak_value - self.final_value

    @property
    def knee(self) -> tuple[float, float]:
        """The efficiency elbow: (fraction, cumulative value).

        `peak_fraction` answers "where does the curve turn DOWN" - the point
        past which you are actively destroying value on sleeping dogs. On a
        curve that never turns down it lands at 100% and reports a cost of
        stopping late of zero, which is arithmetically correct and
        operationally useless.

        The knee answers the question actually being asked: where do the
        remaining cases stop being worth their contacts? It is the point of
        greatest vertical distance above the straight line from (0, 0) to
        (1, final) - the standard elbow construction. Past it, additional
        targeting buys revenue at worse than the average rate.
        """
        best_i, best_gap = 0, 0.0
        final = self.final_value
        for i, (f, c) in enumerate(zip(self.fractions, self.cumulative)):
            gap = c - f * final  # height above the chord
            if gap > best_gap:
                best_i, best_gap = i, gap
        return self.fractions[best_i], self.cumulative[best_i]

    def fraction_for(self, share: float) -> float:
        """Smallest fraction of the file capturing `share` of the final value.

        The plain-language version of the same idea: "85% of the money is in
        the first 30% of the list."
        """
        if self.final_value <= 0:
            return 1.0
        target = share * self.final_value
        for f, c in zip(self.fractions, self.cumulative):
            if c >= target:
                return f
        return 1.0


def targeting_score(case: dict) -> float:
    """Score the agent could compute before acting. No outcome data."""
    return CLASS_PRIOR.get(case["klass"], 0.2) * case["amount_rupees"]


def _paired(run: dict) -> list[dict[str, Any]]:
    control = {c["event_id"]: c for c in run["arms"]["control"]}
    rows = []
    for case in run["arms"]["chukta"]:
        ctl = control[case["event_id"]]
        rows.append(
            {
                "event_id": case["event_id"],
                "klass": case["klass"],
                "quadrant": case["quadrant"],
                "amount_rupees": case["amount_rupees"],
                "score": targeting_score(case),
                "uplift_rupees": (case["amount_rupees"] if case["recovered"] else 0.0)
                - (ctl["amount_rupees"] if ctl["recovered"] else 0.0),
                "extra_contacts": case["contacts"] - ctl["contacts"],
            }
        )
    return rows


def qini_from_rows(rows: list[dict[str, Any]], key: str = "score") -> QiniCurve:
    """The curve itself, over rows carrying `key` and `uplift_rupees`.

    Kept separate from the run-file parsing so the maths can be tested against
    hand-computed cases instead of only against this project's own simulator
    output - a metric validated only on the data it was written for is not
    validated.
    """
    rows = sorted(rows, key=lambda r: r[key], reverse=True)

    n = len(rows)
    if n == 0:
        return QiniCurve([0.0], [0.0], 0.0, 0.0, 0.0)
    fractions, cumulative = [0.0], [0.0]
    running = 0.0
    for i, row in enumerate(rows, start=1):
        running += row["uplift_rupees"]
        fractions.append(i / n)
        cumulative.append(running)

    peak_idx = max(range(len(cumulative)), key=lambda i: cumulative[i])
    return QiniCurve(
        fractions=fractions,
        cumulative=cumulative,
        peak_fraction=fractions[peak_idx],
        peak_value=cumulative[peak_idx],
        final_value=cumulative[-1],
    )


def qini(run: dict, key: str = "score") -> QiniCurve:
    return qini_from_rows(_paired(run), key)


def qini_scaled(rows: list[dict[str, Any]], key: str = "score") -> QiniCurve:
    """Qini for UNPAIRED data - the form real logged traffic requires.

    `qini_from_rows` assumes every unit appears in both arms, which is true
    here only because the simulator runs both arms over the identical
    population under common random numbers. Live data never looks like that:
    a customer is either treated or held out, never both.

    The standard correction (Radcliffe & Surry, TR-2011-1) rescales the control
    responders by the treated/control ratio within the first k units:

        Q(k) = R_t(k) - R_c(k) * N_t(k) / N_c(k)

    At k = N this reduces to the usual incremental-responders estimate, which
    is what `test_qini_hillstrom.py` pins against a published experiment.

    Each row needs `key`, `treated` (bool) and `outcome` (float).
    """
    rows = sorted(rows, key=lambda r: r[key], reverse=True)
    n = len(rows)
    if n == 0:
        return QiniCurve([0.0], [0.0], 0.0, 0.0, 0.0)

    fractions, cumulative = [0.0], [0.0]
    r_t = r_c = 0.0
    n_t = n_c = 0

    for i, row in enumerate(rows, start=1):
        if row["treated"]:
            n_t += 1
            r_t += row["outcome"]
        else:
            n_c += 1
            r_c += row["outcome"]
        # Before any control unit has been seen there is nothing to subtract.
        # Reporting the raw treated count there overstates the gain, so the
        # curve is only meaningful once both arms are represented - which is
        # why the head of a steeply-ranked curve should be read with care.
        q = r_t - r_c * (n_t / n_c) if n_c else r_t
        fractions.append(i / n)
        cumulative.append(q)

    peak_idx = max(range(len(cumulative)), key=lambda i: cumulative[i])
    return QiniCurve(
        fractions=fractions,
        cumulative=cumulative,
        peak_fraction=fractions[peak_idx],
        peak_value=cumulative[peak_idx],
        final_value=cumulative[-1],
    )


def oracle(run: dict) -> QiniCurve:
    return qini(run, key="uplift_rupees")


def qini_coefficient(curve: QiniCurve) -> float:
    """Area between the model curve and the random diagonal, normalised.

    Positive means the ranking beats targeting at random; zero means it adds
    nothing.
    """
    final = curve.final_value
    n = len(curve.fractions)
    area_model = sum(
        (curve.cumulative[i] + curve.cumulative[i - 1]) / 2
        * (curve.fractions[i] - curve.fractions[i - 1])
        for i in range(1, n)
    )
    area_random = final / 2.0
    return (area_model - area_random) / abs(final) if final else 0.0


def report(run: dict) -> str:
    model = qini(run)
    orc = oracle(run)
    rows = _paired(run)
    n = len(rows)

    # What the peak implies for the contact budget in policy.yaml.
    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    cutoff = max(1, int(round(model.peak_fraction * n)))
    contacts_if_capped = sum(r["extra_contacts"] for r in ranked[:cutoff])
    contacts_full = sum(r["extra_contacts"] for r in rows)

    lines = [
        "QINI - incremental revenue vs fraction of the file targeted",
        "",
        f"  target all      end  {model.final_value:>12,.0f} at  100%",
        f"  turn-down point      {model.peak_value:>12,.0f} at "
        f"{model.peak_fraction:>5.0%}  (past here the curve falls)",
        f"  cost of not stopping        {model.value_left_on_table:>12,.0f}",
        f"  efficiency knee      {model.knee[1]:>12,.0f} at "
        f"{model.knee[0]:>5.0%}  (past here, worse than average)",
        f"  80% of the money is in the first "
        f"{model.fraction_for(0.8):.0%} of the ranked file",
        f"  oracle ceiling  peak {orc.peak_value:>12,.0f} at "
        f"{orc.peak_fraction:>5.0%}",
        f"  qini coefficient  {qini_coefficient(model):+.3f}  "
        f"(oracle {qini_coefficient(orc):+.3f})",
        "",
        f"  stopping at the peak targets {cutoff} of {n} cases and spends",
        f"  {contacts_if_capped} extra contacts instead of {contacts_full}.",
        "",
        "  decile           cum. uplift      contacts",
    ]

    for d in range(1, 11):
        idx = int(round(d / 10 * n))
        cum = model.cumulative[idx]
        contacts = sum(r["extra_contacts"] for r in ranked[:idx])
        marker = "  <- peak" if abs(d / 10 - model.peak_fraction) < 0.05 else ""
        lines.append(f"  {d*10:>3}%        {cum:>14,.0f} {contacts:>13}{marker}")

    return "\n".join(lines)


if __name__ == "__main__":
    from .metrics import load_run

    print(report(load_run()))
