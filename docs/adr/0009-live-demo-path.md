# ADR 0009 — A live demo path, with measurement still credential-free

**Status:** accepted · 31 Aug 2026 · amends [ADR 0004](0004-no-live-api.md)

## Context

ADR 0004 cut the Razorpay integration entirely. Two reasons: a live key had
reached the working session, and a handful of test-mode calls would not have
strengthened the causal argument by a basis point.

Working test-mode credentials now exist. That changes one of those reasons and
not the other.

Two things also changed the calculus:

- The [prior-art review](../prior-art.md) found the strongest competitor on this
  track ships a working test-mode integration. On a track named *AI Revenue
  Recovery*, "we do not call the API" is defensible but has to be defended out
  loud, every time.
- The executor already existed, fully guarded and tested offline. The cost of
  turning it on was a script, not an architecture.

## Decision

Add exactly one thing that touches the network: `demo_live.py`. Everything else
is unchanged.

    python demo_live.py           # dry run, prints the request
    python demo_live.py --live    # real call against Razorpay test mode

**ADR 0004's core reasoning is retained, not reversed.** It said a network
dependency must never touch reproducible numbers. That still holds:

- Every command in `eval/` runs with no credential and produces identical
  output. `test_measurement_needs_no_credentials` asserts it.
- `CHUKTA_DRY_RUN` still defaults to **on**. The demo opts in explicitly.
- The live-key guard is untouched: `rzp_live_` is refused before a client is
  constructed, with no override.

## Alternatives rejected

**Leave ADR 0004 as it stood.** Intellectually clean and the numbers would be
identical. Rejected: it costs a real demo moment for no measurement benefit, and
the reason it was written — a live key in the session — no longer applies.

**Full reversal: run the whole batch live.** Rejected outright. It would make
the headline numbers depend on network conditions and a rate limiter, which is
the one property this project cannot trade away. It would also mean 300 real
payment links per run.

**Point the simulator at test mode behind a flag.** A softer version of the
same mistake. Two code paths through the measurement pipeline is two things to
keep honest, and the one that runs in CI would be the one nobody checks.

## Consequences

- The video can show a **real payment link created by the agent** after passing
  seven gates, then the audit row that records it.
- Running it created real objects in the test account — `plink_TWG9GkKdwXwv7g`,
  `https://rzp.io/rzp/PM9RUgUq`. Test mode, no money, but they exist.
- **Two bugs surfaced within minutes of the first real call**, neither of which
  any test had caught:

  1. `AuditLog` reset its chain head to `GENESIS` on construction, so appending
     to an existing log wrote `prev=GENESIS` mid-file and `verify` correctly
     reported the chain as broken. An append-only log that cannot survive a
     process restart is broken for the one job it exists to do. Now resumes;
     four tests pin it.
  2. A gateway duplicate rejection was reported as a generic error **and
     counted toward the circuit breaker**. It is neither. `reference_id` is
     derived from the idempotency key, so when the local ledger was deleted the
     gateway still refused to create the link twice — the second line of defence
     working exactly as documented. It now returns `duplicate_prevented` and
     leaves the breaker closed, because a gateway that correctly rejects a
     duplicate is the opposite of degraded.

  That is the argument for the live path in one paragraph. Offline tests are
  necessary and they do not find everything; a real gateway found two design
  errors in five minutes.
- The duplicate carve-out is narrow on purpose: Razorpay returns a plain 400 for
  both a duplicate and a malformed request, so the match requires
  `reference_id` in the message. `test_a_validation_error_is_not_mistaken_for_a_duplicate`
  pins that a real validation bug cannot be silently read as deduplication.
