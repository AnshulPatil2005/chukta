# Prior art

Done on 30 Aug 2026, day 2 — later than it should have been. This should have
come before the build, not after the first working slice. Recording it late is
better than not recording it, and it changed what this project claims.

## What already exists

Failed-payment recovery is a **crowded, well-funded category**, not an opening.

| Product | What it does | Public claim |
| --- | --- | --- |
| Stripe Smart Retries | ML picks the retry moment; 500+ attributes, trained across the Stripe network | Deliveroo recovered >£100m in a year, with Card Account Updater and Adaptive Acceptance |
| Stripe Adaptive Acceptance | Real-time retry / routing / auth at authorisation to reverse false declines | — |
| Butter Payments | Retry optimisation, plus "Payments Score" and "Outreach" (Jan 2026) | 8–10% uplift on the newer products; 56% more subscription revenue YoY at platform level |
| Revaly (formerly FlexPay) | AI retry logic and "decline intelligence" | — |
| Gravy, Churn Buster, Churnkey | Outreach-led dunning | — |
| FlyCode | Retries + alternate card-on-file + timezone-aware predictive dunning | — |
| Slicker | Smart retries, incl. **country-specific rules for India RBI** | — |
| **Razorpay Subscriptions itself** | Fallback retry logic, up to 8 attempts | 20–30% of lost revenue recoverable |

## What this does to the pitch

**Three things I believed were differentiators are not.**

**1. "Retry timing should depend on the failure reason" is commodity.** Stripe
trains on 500+ attributes across billions of payments. Anything a seven-day
rules engine does here is strictly worse. This is not the contribution.

**2. The incremental-measurement critique is already public.** Yuno: *"without
a proper control group, you are measuring gross recovery, not net value."*
Slicker: *"without holdout groups, any recovery rate improvement number is an
argument, not a measurement."* I framed this as the project's insight. It is
the industry's own stated critique of itself, and I arrived at it independently
rather than first.

What is still true, and is a narrower claim: vendors **publish gross rates
anyway**. The gap is not knowing the right metric, it is shipping a system
whose default and only headline output is the incremental one, computed against
a live control arm, with the self-recovered share broken out beside it.

**3. India-specific compliance is partially covered.** Slicker publishes on RBI
retry rules. Razorpay's own docs cover the AFA limits and the 24-hour pre-debit
notification. This is less unique than assumed.

## Where every column still says "no"

| Axis | Stripe | Butter | Revaly | FlyCode | Slicker | Razorpay | Chukta |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Reason-aware retry timing | yes | yes | yes | yes | yes | partial | yes |
| Incremental measurement as the default headline | no | no | no | no | no | no | **yes** |
| Models **negative** uplift (contact causes churn) | no | no | no | no | no | no | **yes** |
| Compliance checked **before** execution, per action | no | no | no | no | no | no | **yes** |
| TRAI messaging windows / DND / traffic classification | no | no | no | no | no | no | **yes** |
| Per-decision audit row with the rule that fired | no | no | no | no | no | no | **yes** |
| Policy legible as one file a compliance officer can read | no | no | no | no | no | no | **yes** |

Entries for closed commercial products are inferred from public marketing and
documentation. None of them publish a decision-level audit format or a
pre-execution gate model, but absence of evidence is not evidence of absence —
this table would need a trial account to verify properly, and it has not had
one.

**The honest opening is the last four rows, and they are one idea:** an agent
that spends money and customer goodwill should be *auditable and stoppable*,
and should be measured on what it caused rather than what happened around it.
The retry optimisation is table stakes and I should stop presenting it as the
point.

## What this changes in the repo

- The README should not imply reason-aware retrying is novel. It is the
  baseline every commercial tool already clears.
- The claim is: **bounded, auditable, honestly-measured** — not "smarter."
- The strongest genuine contribution is negative-uplift avoidance plus the
  pre-execution gate layer with a per-decision audit trail. That is what the
  demo should lead with.

## Sources

- <https://stripe.com/blog/how-we-built-it-smart-retries>
- <https://www.y.uno/en/blog/how-to-actually-measure-failed-payment-recovery>
- <https://www.slickerhq.com/resources/blog/how-to-measure-roi-failed-payment-recovery-strategy>
- <https://www.slickerhq.com/resources/blog/country-specific-retry-rules-rbi-direct-debit-paypal>
- <https://www.butterpayments.com/>
- <https://www.flycode.com/blog/top-payment-recovery-platforms-2026-comparison-chart-success-rate-stats>
- <https://razorpay.com/blog/rbi-e-mandate-regulations/>
- <https://razorpay.com/blog/payment-gateway-support-for-subscription-businesses-key-considerations-in-2026/>

---

# Part 2 — the hackathon field

Done 31 Aug 2026, by searching GitHub directly. **40 public repositories** carry
a Track 03 name. This is the competitive set that actually matters for the
submission, as distinct from the commercial landscape above.

## Shape of the field

Most are thin: nine are empty, and many are under 150 KB. Two are
self-inflicted — one committed its entire `venv/`, another is a single 9.9 MB
blob. Four are substantial:

| Repo | Files | Code | Test files |
| --- | ---: | ---: | ---: |
| `PythonScript32/wapsi` | 69 | 45 | 10 |
| `Devviratt/recoverai` | 82 | 57 | 8 |
| `modiviveks/razorpay-ai-revenue-recovery-agent` | 60 | 47 | 19 |
| `Pen-ball/recover-ai` | 80 | 38 | 9 |

## The name collision, and why this project is now called Chukta

`PythonScript32/wapsi` — "वापसी · Wapsi", *AI revenue recovery agent for
Razorpay*, same track — was created **24 Aug**, five days before this project
started. It also carries `CLAUDE.md` and `AGENTS.md`, so it was built with the
same tooling.

The design convergence is close: governance gates that fail closed, an
append-only audit log, promise-to-pay pausing outreach, insufficient funds
waiting for salary day, revoked mandates never silently charged, an RBI-style
pre-debit notice, a 24-hour contact gap, an idempotent executor, a holdout
batch.

The likely explanation is benign — *wapsi* is the obvious Hindi word for a
recovery product, and two Claude-built projects working from the same track
brief and the same Indian regulatory context landed in the same place. But
theirs is timestamped earlier, and no explanation survives contact with a judge
comparing two identically-named entries. **Renamed to Chukta** (चुकता, *settled
/ paid up*) on 31 Aug. *Vasooli* was the obvious alternative and was rejected:
it connotes strong-arm collection, which is thematically backwards for a
project whose entire thesis is knowing when to stop.

## What they do better

- **Broader product surface.** Checkout-abandonment recovery, a Hinglish voice
  concierge, WhatsApp. Chukta does failed-payment recovery only.
- **Real persistence.** Supabase, with append-only enforced at the database
  level rather than by a hash chain over JSONL.
- **They shipped the live integration first**, while this project had cut it
  (ADR 0004) and only restored it as a demo path (ADR 0009).

## Where every column still says no

Read from their source, not their README:

| | Field | Chukta |
| --- | :-: | :-: |
| Control baseline | some | yes |
| **Multi-seed, variance reported** | no | **12 seeds, sd, 95% CI** |
| **Sensitivity analysis** | no | **7 scenarios, reports where it breaks** |
| **External benchmark validation** | no | **Qini vs Hillstrom's +7.66pp** |
| **Off-policy evaluation** | no | **DR within 5.3% of truth** |
| **Prior-art review** | no | this document |
| **ADRs with rejected alternatives** | no | 9 |
| **CI that fails when README figures move** | no | `eval/check_claims.py` |

One specific and instructive difference. `PythonScript32/wapsi` contains:

```python
_NAIVE_TOPUP_CHANCE = 0.35
# ...keeps the naive baseline in a believable ~12-18% recovery range instead
# of collapsing to ~5% (making our lift look implausibly large)...
```

Their baseline is tuned so the reported lift looks credible. They are
transparent about it in the comment, which is to their credit. But it means the
headline rests on a hand-chosen constant that nothing stress-tests — which is
precisely the failure `eval/sweep.py` exists to catch, and precisely the failure
it *did* catch in this project when the first reported figure turned out to be
the best of twelve seeds.

## A note on method

`gh search code` returned zero hits for every term, **including one confirmed to
exist** in a repo being searched. The code index does not cover repositories
this new. Every "they do not have X" claim above comes from fetching and reading
their source files directly, not from that search.
