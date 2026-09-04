# Chukta

A bounded revenue-recovery agent for Razorpay merchants. It detects stalled
money, diagnoses *why* it stalled, picks an intervention matched to the cause,
executes it behind hard compliance gates — and knows when to stop.

Razorpay Hackathon, Track 03 — AI Revenue Recovery.

---

## The thesis

The industry default for a failed payment is a fixed retry ladder: charge again
at T+1h, T+24h, T+72h, same instrument, same rail, plus a generic SMS. That is
wrong twice over.

**It ignores the reason.** An expired card retried three times produces three
guaranteed declines, three pointless notifications, and a worse decline ratio
with the issuer. An insufficient-funds failure retried on the 28th fails; the
identical retry on the 1st succeeds.

**It ignores the cost of trying.** Every attempt spends fees, goodwill and
issuer auth-rate — and outreach to a lukewarm subscriber can cause the very
cancellation it was meant to prevent.

So the headline metric here is **incremental** recovery measured against a
control arm, not gross recovery rate. Gross rate counts the customers who would
have paid anyway, and on any realistic population that is most of them. It is
the standard way dunning numbers get inflated.

### What is already solved, and is not the contribution

A [prior-art review](docs/prior-art.md) done on day 2 — later than it should
have been — changed what this project claims.

**Reason-aware retry timing is commodity.** Stripe Smart Retries trains on 500+
attributes across billions of payments; Butter, Revaly, FlyCode, Slicker and
Razorpay Subscriptions itself all ship retry logic. A seven-day rules engine
does not beat that and should not pretend to.

**The incremental-measurement critique is already public.** Yuno: *"without a
proper control group, you are measuring gross recovery, not net value."* I
reached it independently, not first.

What is left, and what this actually argues, is narrower: vendors publish gross
rates anyway. The contribution is an agent that is **bounded, auditable and
honestly measured** — negative uplift modelled explicitly, compliance checked
per action before execution, one audit row per decision naming the rule that
fired, and the incremental number as the only headline. Those are the rows in
the comparison table where every commercial column still says no.

## Results

### First, the number this project refuses to lead with

```
python -m eval.report_variants
```

One run. One policy. One population. Only the framing changes:

| Framing | Number |
| --- | ---: |
| Recovery rate, pursued cases only | 60.6% |
| Gross recovery rate, all cases | 54.7% |
| Total rupees recovered | Rs 195,704 |
| Rupees attributable to an action | Rs 134,177 |
| **Incremental vs control arm** | **Rs 37,667** |

**68 of 164 "recoveries" — 42% — were customers who paid without the agent
doing anything.** Every framing except the last counts them as a win, and the
spread between the most flattering and the honest one is five-fold on rupees.

"60.6% recovery rate" is a headline anyone would publish. It is also true, from
this same run. That is the problem, and it is why everything below is
incremental.

### The honest headline

**+Rs 16,653 mean incremental revenue across 12 seeds** (n=300 each,
sd 13,041, approximate 95% CI on the mean [9,274, 24,032], positive in **11 of
12**), for **+55 extra contacts and +1.7 extra cancellations**. Reproduce with
`python -m eval.sweep --seeds 12`.

The per-seed spread is wide, and one seed is **negative** — [-5,569, 37,667] — so any single run is close to
meaningless on its own. An earlier draft of this README reported 47,382 as the
result; that was the *best* of the twelve, and quoting it was cherry-picking
even though the seed was chosen before the numbers were seen. The mean and the
interval are the honest summary.

### The result is not robust to one assumption

`python -m eval.sweep --seeds 12 --sensitivity` re-runs everything with one
belief in `sim/response_model.py` perturbed at a time:

| Scenario | Mean incremental | Seeds positive |
| --- | ---: | ---: |
| baseline | 16,653 | 11/12 |
| outreach works better | 22,138 | 12/12 |
| customers rarely churn | 17,519 | 10/12 |
| customers churn twice as fast | 17,614 | 11/12 |
| retry timing matters less | 15,406 | 11/12 |
| outreach barely works | 6,105 | 8/12 |
| **message frames do nothing** | **−8,129** | **3/12** |

**If the behavioural message frames carry no lift in a payments context, Chukta
is net negative.** That assumption is load-bearing, and it is the least
defensible thing in the project: the frames are transplanted from
tax-compliance RCTs, where the ask, the authority relationship and the
consequence of ignoring the message are all different. Nothing here tests
whether they transfer.

Which is the actual finding. Not "the agent recovers more money" — that was
never in doubt for a policy allowed to contact people more. The finding is that
the effect is **entirely contingent on message quality**, and that a recovery
agent which improves targeting but sends indifferent copy is worse than doing
nothing. That is what would need an A/B test on live traffic to settle, and
it is the first thing to measure, not the last.

Robustness against churn is the reassuring half: doubling churn propensity
barely moves the result, so the policy is not quietly buying revenue with
cancellations.

### It is slower, and that needed a deadline term

|  | control | chukta (uncapped) | chukta (7-day cap) |
| --- | ---: | ---: | ---: |
| hours to recovery, p50 | 25h | 61h | **60h** |
| p95 | 138h | 302h | **168h** |
| p99 | 151h | 326h | **168h** |

Chukta waits for salary-adjacent mornings, which is where most of its timing
advantage comes from — and most of its slowness. Reporting means alone hid this
completely; only percentiles showed a tail running past 13 days.

`max_wait_hours: 168` in `policy.yaml` caps it. A merchant with a cash-flow
constraint would rather have the money later-but-bounded than
optimally-timed-but-open-ended, and hitting the cap means deliberately taking a
worse-timed action.

The surprise is that it costs nothing in aggregate: the 12-seed mean went **up**
(19,570 → 20,347) and the spread narrowed (sd 12,602 → 10,764). Bounding the
wait removed variance rather than revenue. It does cost 7% on seed 20260829,
which is a good reminder of why single-seed results were retired.

## Head to head against the competing strategies

The [prior-art review](docs/prior-art.md) established that reason-aware retrying
and smart timing are commodity. That raises the only question worth answering:
**how much of the gain is commodity, and how much is this project?**

A marketing-claims table cannot answer that. Running the strategies as arms can.
`python -m eval.compare_systems --seeds 12` — identical population, common
random numbers, identical compliance gates, only the strategy varies:

| arm | recovered | attempts | contacts | churn | p50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `blind` — fixed ladder | 188,253 | 394 | 86 | 2.6 | 28h |
| `smart_timing` — optimal timing, reason-blind | 181,756 | 243 | 152 | 3.8 | 116h |
| `reason_aware` — diagnosis, unbounded contact | 215,028 | 137 | 284 | 7.1 | 53h |
| `chukta` — diagnosis + `G-OPS-08` + deadline | 208,600 | 137 | 144 | 4.0 | 56h |

Decomposed by capability added:

```
 -> smart_timing     -6,497   contacts  +66    commodity
 -> reason_aware    +33,272   contacts +133    commodity
 -> chukta            -6,428   contacts -140    this project

 total over the blind ladder   +20,347
```

**Read that honestly. Nearly all the gain is commodity**, and it comes from
diagnosis, not timing — timing alone is *negative* on this population, because
waiting for a better moment costs more than it wins. Anyone claiming a large
recovery uplift from smart retry timing should be asked against what.

**And the contribution costs revenue.** `G-OPS-08` gives up Rs 6,428 to avoid
140 contacts (−49%) and 3.1 cancellations (−44%). On revenue alone, this project
is *worse* than an unbounded reason-aware baseline. That is the measurement, and
softening it would defeat the purpose of having built the harness.

The defence is that a fourteen-day horizon prices a cancellation at one missed
payment, when a cancelled subscriber stops paying forever. The break-even:

> Giving up Rs 6,428 to avoid 3.1 cancellations **pays off if a retained
> customer is worth more than Rs 2,085 in future billing** — roughly 1.5 billing
> cycles at the mean transaction size here.

That is a low bar, and most subscription businesses would clear it comfortably.
But it is not *claimed* to be cleared: measuring it needs retention data this
project does not have, and the honest position is to name the number and let a
merchant check it against their own.

### What these arms are not

`SmartTimingPolicy` is a hand-written approximation of the published *strategy*,
handed the salary-day effect for free — more than a real model gets. Stripe
trains on 500+ attributes across billions of payments. **Beating this arm is not
beating Stripe**, and nothing here should be read as claiming otherwise. The arm
exists to price what timing alone is worth on this population, not to stand in
for anyone's product.

### One run in detail

Illustrative only — seed 20260829, the most favourable of the twelve. Read the
shape, not the magnitude:

|                            | control     | chukta       |
| -------------------------- | ----------- | ----------- |
| gross recovery rate        | 51.7%       | 55.3%       |
| &nbsp;&nbsp;self-recovered | 65          | 68          |
| &nbsp;&nbsp;policy-driven  | 90          | 98          |
| recovered                  | Rs 155,846  | Rs 197,059  |
| charge attempts            | 365         | **149**     |
| customer contacts          | 83          | 139         |
| contacts per recovery      | 0.54        | 0.84        |
| outreach-induced churn     | 4           | 7           |

**This seed: +Rs 41,213 (+3.7pp), on 59% fewer charge attempts** — against a
12-seed mean of +Rs 19,570. The attempt reduction is the stable part; the
revenue figure is not.

The agent stops hammering instruments that cannot succeed, which is where the
attempt reduction comes from, and `G-OPS-08` stops it spending outreach on
cases that will not repay it. An earlier build without that gate spent **191**
extra contacts and **6** extra cancellations for a bigger revenue number; the
gate trades about a quarter of the revenue for **70% fewer extra contacts and
72% fewer extra cancellations**. See [ADR 0006](docs/adr/0006-uplift-threshold.md).

One caveat that cuts against us: the control arm had **234 actions blocked by
gates** against the agent's 152, because it sends promotional-class SMS at all
hours and TRAI timing rules stop most of them. The blind baseline is partly
protected from itself by its own non-compliance being caught — some of what
looks like Chukta doing better is the baseline being saved from its own worst
impulses. (Chukta's own 152 are mostly `G-OPS-08` declining to contact
low-uplift cases, which is the gate working, not the policy misfiring.)

## Run it

```bash
pip install -r requirements.txt
python -m sim.run --n 300 --seed 20260829
python -m eval.metrics                              # incremental + tails
python -m eval.uplift                               # Qini
python -m eval.sweep --seeds 12 --sensitivity       # robustness
python -m eval.compare_systems --seeds 12           # vs competing strategies
python -m eval.dr                                   # off-policy estimate
python -m eval.learn_targeting --train 6 --test 6   # does learning beat the priors?
python -m eval.report_variants                     # one run, five headlines
python -m eval.check_claims --full                  # every README figure
pytest                                              # 199 tests
```

`eval.check_claims` turns every number quoted in this README into an assertion,
so the day a change moves one, CI names which claim broke. A README that
silently goes stale is worse than one that admits it does not know.

The external uplift benchmark is optional and needs a 4 MB download:

```bash
python -m eval.fetch_hillstrom && pytest -q tests/test_qini_hillstrom.py
```

**No credentials are required, for any of it.** There is no key to obtain, no
account to register, no network call. A reviewer clones this and reproduces
every number in the results table.

```bash
python -m chukta.trace          # full decision trace, one case per class
python serve.py                # decision inspector at http://127.0.0.1:8000
```

## The decision inspector

`python serve.py` opens a browser panel where you build a failure event —
`source × step × reason`, amount, rail, mandate state, customer state, hour of
day — and watch the engine diagnose it, walk the policy ladder, and evaluate
every gate. It renders the outgoing API request and stops; nothing executes.

The dashboard **owns no policy**. Every verdict on screen comes from
`classify`, `PolicyEngine.decide`, `gates.evaluate` and `Executor` over
`/api/diagnose` — the same call path as `python -m chukta.trace`. That is not a
convention: `tests/test_web.py` calls the API and the engine with identical
input and requires identical answers, so inlining a rule into the endpoint
breaks the build. A UI that renders a stale verdict is worse than no UI,
because it looks authoritative.

Two controls are worth knowing about, because they exist to expose something
the default policy hides:

- **Attach an offer.** Every step in `policy.yaml` is transactional — a bare
  recovery notice is service traffic — so the promotional window never binds
  and `G-TRAI-01`/`G-TRAI-02` never fire. Attaching an offer reclassifies the
  *message*, not the rule, which is exactly what TCCCPR says happens.
- **Send now.** The policy already schedules contacts inside the permitted
  window, so a correctly scheduled action cannot trip the timing gate. That is
  the design working, and it makes the backstop invisible. Bypassing the
  scheduler is what a naive integration does — and what the blind control arm
  does on every contact, which is why it accumulates 234 gate blocks against
  the agent's 6.

With both ticked, dragging the clock past 21:00 IST flips `G-TRAI-01` to
BLOCK. The boundary is pinned in tests at 20:00 pass / 21:00 block.

## The live path, and what it is walled off from

One file touches the network: `demo_live.py`. It runs a single case end to end
against Razorpay test mode — diagnosis, the chosen intervention, every gate, and
a **real payment link** — then verifies the audit row it wrote.

```bash
python demo_live.py           # dry run, prints the request it would send
python demo_live.py --live    # real call against test mode
```

**Everything that produces a number stays credential-free.** `eval/` and `sim/`
never import the SDK and never load a key; `tests/test_no_credentials.py` strips
every credential source and asserts the full two-arm pipeline still runs. A
stranger with a clone and no Razorpay account reproduces every figure in this
README. That separation is the point of [ADR 0009](docs/adr/0009-live-demo-path.md),
which amends the earlier decision to have no integration at all.

**Turning it on found two bugs in five minutes**, neither caught by 172 offline
tests:

- `AuditLog` reset its hash chain to genesis on construction, so appending to
  an existing log broke its own chain. An append-only log that cannot survive a
  process restart is broken for the one job it has.
- A gateway duplicate rejection was reported as a generic error *and counted
  toward the circuit breaker*. It is neither. `reference_id` is derived from the
  idempotency key, so with the local ledger deleted the gateway still refused to
  create the link twice — the documented second line of defence, working. It now
  returns `duplicate_prevented` and leaves the breaker closed.

That is the argument for a live path in two bullets. Offline tests are necessary
and they do not find everything.

What the executor still has to get right is everything that happens *around*
the call, and that is testable without one:

| Property | Where |
| --- | --- |
| Live credentials refused before a client is built | `load_credentials`, no override |
| Same logical action never sent twice | `idempotency_key`, time excluded on purpose |
| Unknown outcome escalated, never guessed | `in_flight` → `needs_reconciliation` |
| Degraded gateway stops the agent | `CircuitBreaker`, trips audited |
| Secrets never reach the audit log | truncated salted fingerprint |

Twenty tests cover those, all of them offline. The credential guard is the
gate-layer principle one level down: the gates stop a *non-compliant* action;
the credential check stops a *correctly reasoned* action pointed at the wrong
universe. It is retained even though nothing currently loads a key, because the
first thing anyone extending this will do is add one.

## Architecture

`specify → verify → enforce → trace`. The gate layer sits *between* decide and
execute, never after — checking a proposed action is cheaper and safer than
inspecting side effects once money has moved.

| Layer     | Module                     | Does                                                       |
| --------- | -------------------------- | ---------------------------------------------------------- |
| Specify   | `policy.yaml`              | The entire policy, in one readable file                     |
| Diagnose  | `chukta/taxonomy.py`        | `source × step × reason` → recoverability class             |
| Decide    | `chukta/policy.py`          | Class + context → intervention, rail, schedule              |
| Verify    | `chukta/gates.py`           | RBI, TRAI and operational rules, pre-execution              |
| Enforce   | `chukta/execute.py`         | Renders the request; idempotency, breaker, credential guard |
| Ingest    | `chukta/webhook.py`        | Signed Razorpay deliveries; verify-before-parse, replay-proof |
| Compose   | `chukta/compose.py`         | Model writes copy in a frame; deterministic coercion guard   |
| Wiring    | `tests/test_wiring.py`     | Runs each entry point and asserts modules are actually called |
| Listen    | `chukta/replies.py`         | Opt-out, promise-to-pay, dispute - deterministic parsing      |
| Trace     | `chukta/audit.py`           | Append-only JSONL, hash-chained, one row per decision        |
| Measure   | `eval/`                    | Two arms, incremental revenue, Qini                         |

Audit row shape:
`trigger → evidence → rule fired → gate results → action → API response → outcome`.

### The diagnosis layer is two-tier on purpose

Razorpay's `source` and `step` axes are small and stable; the `reason` slug is
neither fully documented nor stable. So an unrecognised slug degrades to a
coarser class keyed on the stable axes rather than falling out of the taxonomy,
and the tier that fired is written into the audit row as a confidence level.

### Where the model earns its place

Most of this is rules, and that is a feature. **The model is used in exactly one
place: writing the message.**

An earlier version of this section claimed three uses. Two of them did not
exist, and the claim sat in the README for two days looking authoritative. It
is corrected here rather than quietly deleted, because the same overclaim was
made about live API execution and the pattern is worth naming.

**Where it is used** — `chukta/compose.py`. By the time it runs, the class, the
intervention, the channel and the schedule are decided and already through the
gates. The model gets a behavioural frame and a set of facts, and returns prose.
It has no tools, no action vocabulary, and nothing it writes can change what
happens. Its output passes a **deterministic** coercion guard before sending; a
model grading a model shares the failure mode you are trying to catch. Copy that
fails the guard is replaced with a template and the substitution is written to
the audit row.

**Where it is deliberately not used:**

- *Reply parsing* (`chukta/replies.py`) decides whether to keep messaging
  someone, so it is regex a reviewer can read line by line. Opt-out is a legal
  obligation, not a judgement call.
- *Rule arbitration* was described and never built. There is no case in the
  current policy where two rules conflict, so it would have been a solution
  looking for a problem.

**It is off the critical metric path entirely.** Every number in `eval/` comes
from runs that never call it — when no credential is present the deterministic
template composer runs and results are unchanged. That is not a limitation to
apologise for: a model in the measurement loop would make the numbers
irreproducible, which is the one property this project cannot trade away.

The honest limitation is the flip side: `sim/response_model.py` scores the
*frame*, not the words, so nothing here tests whether better copy converts
better. The sensitivity sweep says frames are the load-bearing assumption in the
whole project, and settling that needs live traffic.

## Compliance gates

Rule IDs are stable and quotable.

| Rule      | Source                                | Blocks                                                        |
| --------- | ------------------------------------- | ------------------------------------------------------------- |
| G-RBI-01  | RBI E-mandate Framework 2026          | Recurring debit above the AFA-free limit with no AFA on file   |
| G-RBI-02  | RBI E-mandate Framework 2026          | Debit without a pre-debit notice served ≥24h ahead             |
| G-RBI-03  | RBI E-mandate Framework 2026          | Any charge against a revoked mandate                           |
| G-TRAI-01 | TCCCPR 2018, amended Feb 2025         | Promotional traffic outside 10:00–21:00 IST                    |
| G-TRAI-02 | TCCCPR 2018, amended Feb 2025         | Promotional traffic to a DND-registered subscriber             |
| G-TRAI-03 | TCCCPR 2018, amended Feb 2025         | Anything to an opted-out customer                              |
| G-OPS-06  | —                                     | Contacting a customer about *our* malformed request            |
| G-OPS-08  | Qini curve, `eval/tune_threshold.py`  | Outreach to a case whose expected uplift does not repay it     |
| G-OPS-07  | —                                     | Any action while a customer-stated promise-to-pay is live      |

The classification is the sharp part. A bare recovery notice is service
traffic. Attach an offer and it becomes promotional, and a narrower window plus
opt-out obligations apply. `test_gates.py` pins both directions.

**Five stopping rules, and nothing else ends a case:** recovered, hard-decline
class, attempt cap, opted out, promise-to-pay.

## What is real and what is simulated

- Customer response propensity is **simulated**, calibrated to published
  retry-timing effects (~20% relative lift from three extra retries in the
  dunning window; ~6.5% from a 24h rather than 2h first retry). Those are
  vendor-published, so they are calibration anchors, not evidence. They live in
  one place, `sim/response_model.py`, so a reviewer can change them and re-run.
- Payment execution is **not live**. Chukta never calls the Razorpay API. The
  executor renders the exact request it would send — endpoint, payload,
  idempotency key — and stops there. Nothing in this repository has moved
  money, and no credential is required to reproduce any number in it.
- The uplift split is generated by the simulator's own structure, not learned
  from observed data. Ranking quality is a property of the policy, not evidence
  about real customers.
- The **Qini implementation** is validated independently of all that: against
  hand-computed curves (`tests/test_qini.py`) and against Hillstrom's 2008
  randomised e-mail experiment, where the curve endpoint must reproduce the
  published +7.66pp lift exactly (`tests/test_qini_hillstrom.py`). A metric
  checked only on the data it was written for is not checked.
- Behavioural message frames are transplanted from tax-compliance RCTs.
  Untested in this setting — an assumption, not a result.
- Quadrant labels are simulator ground truth used for scoring only. Nothing in
  `chukta/` reads them.

## Built on

- Radcliffe & Surry, *Real-World Uplift Modelling with Significance-Based
  Uplift Trees*, TR-2011-1 — the Qini coefficient.
- Gutierrez & Gérardy, *Causal Inference and Uplift Modelling: A Review of the
  Literature*, PMLR 67:1–13, 2017.
- Dudík, Langford & Li, *Doubly Robust Policy Evaluation and Learning*, ICML
  2011 — why the control arm is stochastic and logs propensities.
- Wang, Agarwal & Dudík, *Optimal and Adaptive Off-policy Evaluation in
  Contextual Bandits*, ICML 2017 — propensity clipping.
- Behavioural Insights Team, *Behavioral Interventions in Tax Compliance:
  Evidence from Guatemala* (n=43,387) — the message frame library.
- RBI, *Digital Payments — E-mandate Framework, 2026*; TRAI, *TCCCPR 2018* as
  amended 12 Feb 2025.

## Where this goes next

Budget-constrained causal bandits — heterogeneous treatment effect estimation
with Thompson sampling and adaptive budget pacing, where the contact budget is
literally a knapsack constraint. Framing the sequential contact decision as a
constrained MDP is the other branch. Both need live traffic to explore, which a
seven-day build does not have, so neither is implemented here.
