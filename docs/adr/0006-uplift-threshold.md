# ADR 0006 — Suppress outreach below a measured uplift threshold

**Status:** accepted · 30 Aug 2026

## Context

The project claimed "bounded contact" as its differentiator, implemented as
`contact_budget: 2` in `policy.yaml`.

Building `eval/compare_systems.py` — which runs the competing strategies as arms
through the identical harness — showed the Chukta arm producing results
**byte-identical** to an unbounded reason-aware baseline. Same revenue, same
contacts, same churn, to the rupee.

The budget never bound. The intervention ladders rarely propose two contacts on
a single case, so a cap of two constrained nothing. The differentiator had been
analysed on the Qini curve, written up, and never actually built.

Worse, the Qini analysis had already said what to do — *30% of the ranked file
carries 85% of the incremental revenue* — and the policy did not act on it.

## Decision

A new gate, `G-OPS-08`. Before any **outreach** action, score the case as
`class_prior × amount_at_risk` and block the contact if it falls below
`defaults.min_uplift_score`.

The threshold is **derived, not chosen**. `python -m eval.tune_threshold` sweeps
it across seeds and prints the frontier; the shipped value of 511 is the highest
threshold retaining ≥97% of revenue.

Class priors moved into `policy.yaml` and `eval/uplift.py` now reads them from
there. The scoring that ranks cases for the Qini curve must be the scoring the
gate applies, or the curve measures a policy that is not the one running.

## Alternatives rejected

**Keep tuning `contact_budget`.** Rejected: the budget is per-case, and the
problem is *which cases* get worked at all. No value of a per-case cap
expresses "do not start on this one."

**Suppress the whole case, retries included.** Rejected: a charge retry costs
fees, not goodwill, and cannot cause a cancellation. Suppressing retries on
low-uplift cases forfeits revenue to prevent a harm that is not there. The gate
governs outreach only.

**Put the threshold in the decision layer rather than a gate.** Arguably purer —
this is optimisation, not compliance. Rejected because a gate lands in the audit
row with a rule ID and a reason, and shows up in the decision inspector. A
suppression nobody can see is indistinguishable from a bug.

**Learn the threshold per merchant.** The right answer with traffic. Rejected:
no data, and it would put a model on the critical metric path.

## Consequences

- **Revenue falls and the system gets better on its own stated axes.** Against
  no threshold, at the 12-seed level: extra contacts 200 → 59 (−70%), extra
  churn 4.6 → 1.3 (−72%), incremental revenue 26,197 → 19,570 (−25%).
- **Break-even is Rs 1,655 per avoided cancellation.** On a mean transaction
  near Rs 1,100 that is roughly 1.5 billing cycles. Whether a merchant clears it
  is a business question this project cannot answer — it has no retention data —
  but the number is small enough to be worth stating.
- **The comparison arms had to be separated.** Giving `G-OPS-08` to the control
  arm would measure it against itself. `sim/baselines.policy_for_arm` strips it
  from every arm but Chukta, and `sim/run.py`, `eval/sweep.py` and
  `eval/check_claims.py` all route through it. The first version of this change
  did not, and inflated the reported delta by about 24%.
- The threshold is a business decision expressed as a number in a readable file,
  which is the property the whole policy file exists to have.
