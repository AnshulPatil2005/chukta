# ADR 0002 — Two-tier failure classification

**Status:** accepted · 29 Aug 2026

## Context

Razorpay ships a `source × step × reason` triplet on every failed payment. The
`source` and `step` axes are small, documented and stable. The `reason` slug is
neither fully enumerated in the docs nor stable across gateways, and test mode
will not emit most of them on demand.

A lookup keyed on `reason` alone would therefore be built partly on guesses, and
would fail silently in exactly the cases that matter — an unrecognised slug
would fall through to a default that looks like a decision but is not one.

## Decision

Classify in two tiers. Tier 1 matches the reason slug (precise, high
confidence). Tier 2 matches `(source, step)` alone (coarse, medium confidence).
Unmatched input lands in `UNKNOWN` with low confidence.

**The tier that fired is written into the audit row as a confidence level.**
Uncertainty is surfaced, not hidden.

## Alternatives rejected

**Single lookup on the reason slug.** Simplest. Rejected: an unknown slug has no
sensible default, and the failure is invisible.

**Regex or substring matching on the slug.** Tempting, since `card_expired` and
`expired_card` clearly mean the same thing. Rejected: it would silently match
things it should not, and a wrong high-confidence classification is worse than a
right low-confidence one.

**A learned classifier over historical failures.** The eventual right answer at
scale. Rejected: no labelled history, no traffic, and it would put a model on
the critical metric path where reproducibility matters.

**Ask the model at decision time.** Rejected for the same reason — and because
diagnosis has to be deterministic for the audit trail to mean anything.

## Consequences

- Adding a slug is a one-line change with no retraining.
- Coverage is honestly reported rather than assumed: grep the audit log for
  `tier: source_step` to see exactly which decisions rest on the coarse path.
- The slugs in `REASON_RULES` are transcribed from public documentation and have
  **not** been confirmed against a live account. The note at the top of
  `chukta/taxonomy.py` stays until they are. Tier 2 is what makes that acceptable
  rather than reckless.
