# ADR 0007 — Recovery notices are Service messages, not Transactional

**Status:** accepted · 30 Aug 2026 (corrects an error shipped since day 1)

## Context

Every outbound message in `policy.yaml` was labelled `transactional`, and the
README justified it: *"a bare recovery notice is service/transactional
traffic."* That label drove which TRAI delivery window `G-TRAI-01` applied and
whether `G-TRAI-02` honoured DND.

It was wrong. TCCCPR defines a Transactional Message as one sent

> "in response to a customer initiated transaction **within thirty minutes** of
> the transaction"

— OTPs, payment confirmations, balance alerts. A recovery notice goes out 24
hours to 8 days after a *failed* payment, and the customer did not initiate
anything. It is a **Service Message**: still no explicit consent required, still
not blockable by DND, but a different category with a different justification.

The error came from reading law-firm summaries and vendor compliance blogs
rather than the regulation. Every secondary source says "transactional and
service messages are exempt from the promotional window", which is true and
obscures that they are two categories with different definitions.

## Decision

Add `MessageClass.SERVICE` and reclassify all eight message-sending steps.
`MessageClass.TRANSACTIONAL` is retained for genuinely within-30-minutes traffic
— Chukta emits none today, but collapsing the distinction is what caused this.

`policy.yaml` gets a separate `service_window_ist`, and `chukta/gates.py` selects
the window by class through a lookup that defaults unknown classes to the
*promotional* window — the safe direction to be wrong in.

## Alternatives rejected

**Leave it.** The delivery behaviour was already correct: service and
transactional traffic get the same treatment under the rules that matter here.
Rejected because a compliance claim resting on a misread definition is not one
you want to defend in front of someone who has read it, and this project's whole
pitch is that its compliance reasoning is inspectable.

**Collapse both into one `SERVICE` value.** Simpler. Rejected: it makes the
30-minute distinction unrepresentable, which is precisely the mistake being
corrected.

**Treat recovery notices as promotional to be safe.** Over-compliance has a
cost — it would confine service messages to 10:00–21:00 and make them DND-
blockable, both of which the regulation does not require. Being wrong in the
cautious direction is still being wrong.

## Consequences

- **No behavioural change.** The windows and DND rules produce identical
  outcomes. This is a correctness fix to the reasoning, not the conduct.
- **No test caught it**, and none could have: every test asserted the outcome,
  and the outcome was right. `test_a_recovery_notice_is_service_not_transactional`
  now asserts the *classification* directly, scanning `policy.yaml` for any step
  still labelled transactional.
- Verified in the same pass, and correct as shipped: the **₹15,000** AFA-free
  limit (RBI, 16 Jun 2022), the **24-hour** pre-debit notification (quoted
  verbatim: *"the issuer shall send a pre-transaction notification to the
  cardholder, at least 24 hours prior to the actual charge / debit to the
  card"*), the **₹1 lakh** category ceiling and its three categories — mutual
  fund subscriptions, insurance premiums, credit-card bills (RBI, Dec 2023) —
  and the **10:00–21:00** promotional window.
- The remaining unverified surface is the Razorpay `reason` slug set, which
  needs a live account. The note at the top of `chukta/taxonomy.py` stays until
  it is checked; tier 2 is what makes that acceptable rather than reckless.
