# ADR 0001 — Report incremental recovery, not gross recovery rate

**Status:** accepted · 29 Aug 2026

## Context

The obvious headline for a recovery agent is *"we recovered 61% of failed
payments."* On any realistic population most of those customers would have paid
anyway — the card cleared on a later attempt, or they noticed and paid without
being asked. Counting them credits the agent with revenue it did not cause.

Uplift modelling splits the population by what the intervention *causes*:
persuadables (pay only if contacted), sure things (pay regardless), lost causes
(never pay), and sleeping dogs (contacting them causes them to leave). Gross
recovery rate counts every sure thing as a win and is blind to sleeping dogs
entirely.

## Decision

The headline metric is **incremental** recovery, measured against a control arm
running the blind ladder over the identical seeded population under common
random numbers. Gross rate is still printed, with the self-recovered share
broken out immediately beside it so the gap is visible rather than implied.

## Alternatives rejected

**Gross recovery rate alone.** Rejected: it is the metric the whole project
exists to argue against. Reporting it as the headline would make the tool an
example of the problem.

**Attribution windows (credit any recovery within N hours of an action).** The
standard marketing approach and much cheaper — no control arm needed. Rejected:
it cannot distinguish a persuadable from a sure thing who happened to pay
inside the window, which is the exact confusion at issue.

**Pre/post comparison against historical recovery rates.** Rejected: confounded
by everything else that changes between periods — traffic mix, seasonality,
issuer behaviour.

**Propensity-score matching on observational data.** Defensible when a control
arm is impossible. Rejected here because a control arm *is* possible, and
matching buys assumptions in exchange for statistical power we do not need.

## Consequences

- A permanent control arm costs real revenue — some customers are deliberately
  worked with the inferior policy. That is the price of knowing.
- The headline number is smaller and less impressive than competitors quote.
- The comparison needs common random numbers to be readable at n=300
  (see [ADR 0003](0003-common-random-numbers.md)).
- Measurement became the load-bearing part of the project, which is why the
  robustness sweep exists at all — and it is what caught the fact that the
  first reported figure was the best of twelve seeds.

## References

- Radcliffe & Surry, *Real-World Uplift Modelling with Significance-Based
  Uplift Trees*, TR-2011-1.
- Gutierrez & Gérardy, *Causal Inference and Uplift Modelling: A Review of the
  Literature*, PMLR 67:1–13, 2017.
- The prior-art review ([../prior-art.md](../prior-art.md)) found this critique
  is already public — Yuno and Slicker both publish it. The contribution is
  shipping it as the default output, not noticing it.
