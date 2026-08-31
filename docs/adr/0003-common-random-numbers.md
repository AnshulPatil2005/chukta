# ADR 0003 — Common random numbers across arms

**Status:** accepted · 29 Aug 2026

## Context

The first version drew fresh randomness per arm. The measured difference between
control and treatment was then part policy and part noise, and at n=300 the
noise was large enough to swamp the effect being measured.

## Decision

Each simulated customer pre-draws its uniforms once (`sim/population.py`) and
both arms consume the same stream. A customer who would self-recover does so in
both arms at the same hour; a churn draw that fires on the second contact fires
on the second contact in whichever arm reaches it.

## Alternatives rejected

**Independent draws per arm, more replications.** Statistically valid, just
wasteful — it needs far more runs for the same precision.

**Paired bootstrap over independent draws.** Recovers some precision after the
fact but cannot recover what the design threw away.

**Larger n instead.** Also valid, and orthogonal: variance reduction is free, so
it should be taken regardless. n was later raised to 12 seeds anyway, which is
how the single-seed cherry-pick was caught.

## Consequences

- Differences between arms are attributable to policy.
- The paired structure lets `eval/uplift.py` compute per-case uplift directly,
  which is a **simulation luxury**: real logged data has each customer in one arm
  only. `qini_scaled` exists for that case and is validated against the Hillstrom
  experiment in `tests/test_qini_hillstrom.py`.
- The arms are no longer statistically independent, so any test assuming
  independence is wrong here. Paired comparison is required.

## References

Standard variance-reduction technique in discrete-event simulation. This should
have been the first design rather than the second.
