# ADR 0004 — No live Razorpay integration

**Status:** accepted · 30 Aug 2026 (supersedes the original plan)

## Context

The build began intending real test-mode API calls. Two things changed that.

A **live** key pair reached the working session — wrong dashboard mode, and
pasted into a transcript. The credential was rotated, but it made concrete how
close a recovery agent whose contact policy was still visibly over-contacting
came to re-presenting charges against real cards.

Separately, honest accounting of what a test-mode integration would buy: a
handful of `payment_link.create` calls against an account with no real
customers, on a corpus that is replayed anyway.

## Decision

Chukta does not call the Razorpay API. `chukta/execute.py` renders the exact
request — endpoint, payload, idempotency key — and stops. Credentials that do
not begin `rzp_test_` are refused before a client is constructed, with no
override of any kind.

## Alternatives rejected

**Test-mode integration as planned.** Rejected: it adds a network dependency to
a demo whose entire value is that it reproduces exactly, and strengthens the
causal argument by nothing. "Real API integration" reads well on a submission,
which is a reason to demo something, not a reason to build it.

**Live mode with small amounts.** Rejected outright. The policy was still being
tuned, and the sensitivity sweep later showed it can be net negative under a
plausible assumption about message quality.

**Keep the SDK and mock the transport in tests.** Effectively what happens now,
minus the pretence that the mocked path had ever been exercised against a real
gateway.

**Delete the executor entirely.** Rejected: the failure modes live *around* the
call — idempotency, unknown outcomes, breaker state, credential safety — and all
of them are testable offline. Twenty tests cover them.

## Consequences

- The README claim that execution was "real against test mode" was **false** and
  has been removed rather than softened.
- No credential is required to reproduce any number in the repository.
- `python -m chukta.trace` and the decision inspector replaced the live demo, and
  show more: a live call cannot display three gates blocking at once.
- The credential guard is retained though nothing loads a key, because the first
  thing anyone extending this will do is add one.
