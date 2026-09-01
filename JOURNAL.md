# Build journal

Obstacles logged as they happened. This file is the source for the submission's
"Build Challenges & Technical Obstacles" field, which is why it is written
during the build and not after it.

---

## 29 Aug 2026 — day 1

### Razorpay's `reason` slugs cannot be verified up front

**Problem.** The diagnosis layer maps Razorpay's `source × step × reason`
triplet to a recoverability class. The `source` and `step` axes are small,
documented and stable. The `reason` slug is neither fully enumerated in the
docs nor stable across gateways, and test mode will not emit most of them on
demand — so a lookup keyed on `reason` alone would be built on guesses and
would fail silently in exactly the cases that matter.

**Fix.** Two-tier classification in `chukta/taxonomy.py`:

1. `REASON_RULES` — precise, keyed on the slug, high confidence.
2. `SOURCE_STEP_DEFAULTS` — coarse, keyed on the two stable axes, medium
   confidence.

An unrecognised slug degrades to tier 2 instead of falling out of the taxonomy,
and the tier that fired is written into the audit row's evidence block, so a
reviewer can see which classifications rest on unverified strings. Turning the
uncertainty into a visible confidence level was better than pretending to
certainty I do not have.

**Still open.** Every slug in `REASON_RULES` needs confirming against a live
test-mode account. The note at the top of the module comes out when that is
done.

### Simulated time vs. the wall clock

**Problem.** Quiet hours are IST, events carry UTC, and the simulator has to
cross fourteen days in milliseconds. Any `datetime.now()` anywhere in the
engine would make runs unreproducible and would break the TRAI window tests,
which depend on the hour being exactly what the test says it is.

**Fix.** `chukta/clock.py` exposes an injectable `Clock`, and time is passed
explicitly into every gate and policy call. `datetime.now()` appears nowhere in
`chukta/`. Window arithmetic is done in IST via `zoneinfo` and supports windows
that wrap midnight, which the 21:00–10:00 promotional block does.

### A fair paired comparison needs common random numbers

**Problem.** First cut drew fresh randomness per arm. The measured difference
between arms was then part policy and part noise, and on 300 cases the noise
was large enough to swamp the effect.

**Fix.** Each simulated customer pre-draws its uniforms once
(`sim/population.py`) and both arms consume the same stream. A customer who
would self-recover does so in both arms at the same hour; a churn draw that
fires on the second contact fires on the second contact in whichever arm
reaches it. Differences are then attributable to policy. This is standard
paired-comparison variance reduction and it should have been the first design,
not the second.

### Propensity logging is a one-way door

**Problem.** The doubly robust estimator needs the logging policy's action
propensities. A deterministic control arm records none, and they cannot be
reconstructed after the fact — recovering them means re-running the entire
batch.

**Fix.** `sim/control_policy.py` samples from a fixed action distribution and
writes `p_action` on every decision, even though nothing reads it yet. The
control is not made smarter by this; it still ignores the failure reason
completely. It just does so with a known probability.

### First honest result: the agent over-contacts

**Problem.** First full run (n=300, seed 20260829) came back mixed, and the bad
half is the interesting half:

```
                          control    chukta
gross recovery rate         51.7%    61.0%
recovered                Rs 155,846  Rs 203,228
charge attempts               365      149
customer contacts              83      274
contacts per recovery        0.54     1.50
outreach-induced churn          4       10
```

Incremental revenue is **+Rs 47,382 (+9.3pp)** and charge attempts fall by more
than half — the agent stops hammering dead cards, exactly as designed. But it
spends **191 extra customer contacts** to do it and induces **6 more
cancellations**. Uplift by quadrant confirms the mechanism: persuadables carry
Rs 38,055 of the gain while sleeping dogs come in at **−Rs 1,077 with 5 extra
churns**. The negative-uplift segment is real and the policy is walking into it.

A second, subtler point: the control arm had **234 actions blocked by gates**
against the agent's 6 — because the control sends promotional-class SMS at all
hours and TRAI timing rules stop most of them. The blind baseline is partly
protected from itself by its own non-compliance being caught. That has to be
said out loud rather than quietly banked as a win.

**Fix (in progress).** The Qini curve over the ranked file says where to stop:

```
decile   cum. uplift   extra contacts
 10%        27,765            28
 30%        40,210            82
100%        47,382           191
```

**30% of the file yields 85% of the incremental revenue for 43% of the
contacts.** So `contact_budget` in `policy.yaml` — currently 2, chosen by hand —
should be derived from this curve, and the policy should refuse outreach to
low-score cases entirely rather than working every case to exhaustion. The
oracle ceiling is Rs 67,655 at 15% of the file (Qini coefficient +0.872 against
the model ranking's +0.350), so there is real headroom in the ranking too.

---

## 30 Aug 2026 — day 2

### A live key reached the working session

**Problem.** Setting up the enforcement layer, I asked for Razorpay test-mode
credentials. What arrived was a **live** key pair — `rzp_live_` prefix, and
pasted into a chat transcript rather than typed into `.env`. Two separate
failures in one step: wrong mode, and a secret written somewhere it cannot be
unwritten. Had the executor existed and been pointed at it, a recovery agent
whose contact policy is still visibly over-contacting (see day 1) would have
been re-presenting charges against real cards.

The credential was rotated and never written to disk. But "be careful next
time" is not a control, and the interesting question is what in the *system*
allowed a live key to be one config line away from executing.

**Fix.** `chukta/execute.py` refuses live credentials structurally rather than
by convention:

- `load_credentials()` raises `LiveCredentialRefused` on any `rzp_live_` id,
  before a client is constructed. Also on any id that is not `rzp_test_` —
  allow-list, not deny-list, so a future prefix fails closed.
- There is deliberately **no override**. No env var, no argument, no flag.
  `test_live_refusal_cannot_be_configured_away` asserts this by trying three
  plausible escape hatches and requiring all three to fail. A guardrail with a
  documented bypass is a speed bump.
- `CHUKTA_DRY_RUN` defaults to **on** when unset, so a missing variable is not
  read as permission to spend money.
- Dry run needs no credentials at all, so the whole pipeline stays runnable and
  testable on a machine that has no keys.
- The audit log records a **fingerprint** — a truncated salted digest — so a
  reviewer can tell which credential acted without the log becoming a place
  secrets accumulate.

This is the same principle as the gate layer, applied one level down: check
before the side effect, never after. The gates stop a *non-compliant* action;
this stops a *correctly reasoned* action pointed at the wrong universe.

## 31 Aug 2026 — day 3

## 1 Sept 2026 — day 4

### Went through the competitors' strengths one by one

Read their source rather than their READMEs and built a capability map. Four
things they have that this project did not.

**Learned targeting.** Three entries fit models; one has a LinUCB bandit that
recovers the ground-truth action on 12/12 segments over 30,000 rounds. The
temptation was to build a bandit too. Instead `eval/learn_targeting.py` asks
the question that actually matters: does learning beat the hand-written priors
**out of sample**? Fit on 6 seeds, score on 6 held out.

The answer is no, and the shape of the no is the interesting part. Learned has
a HIGHER MEAN (+0.167 Qini) but **5.1x the variance**, winning only 3 of 6
seeds. On any given batch it is a coin flip which scorer ranks better, and the
mean is carried by a few seeds where a thin cell got lucky. With 300 cases
spread over 21 cells, most cells hold a handful of observations; the
hand-written priors encode structural facts that survive a thin sample.

My own report initially called that "no meaningful difference", gating the
verdict on win count alone while a +0.167 mean sat in the table above it. Fixed
to distinguish three cases: helps consistently, higher-mean-but-unreliable, and
no difference. A report that mislabels its own data is worse than no report.

Worth saying plainly: a bandit converging on its own simulator proves the
BANDIT works, not that the policy is good — the ground truth was authored by
whoever wrote the simulator. That is a real result about the learner and it is
not a result about recovery.

**Webhooks.** Four entries ingest Razorpay deliveries; this project had none,
replaying everything from a corpus. Built `chukta/webhook.py` and aimed at the
things webhook handlers get wrong rather than at having one:

- Verify BEFORE parsing. `receive()` takes bytes, so a parser never runs over
  attacker-controlled input first. The ordering is enforced by the signature of
  the function, not by remembering.
- `hmac.compare_digest`, never `==`, so a forged signature does not leak how
  much of it was correct through timing.
- Verify the bytes received, not re-serialised JSON — two byte-strings that
  parse identically must not share a signature, and there is a test for it.
- Replay: dedupe on event id AND a freshness window, because a captured
  delivery stays validly signed forever.
- Every rejection returns the same shape. "Bad signature" and "too old" are
  useful things for an attacker to tell apart.

29 tests, most about what must not happen.

**Live deployed demo and Postgres persistence.** Declined, with reasons in
`docs/prior-art.md`. A URL that is down during judging is worse than no URL,
and JSONL with a hash chain detects edits, deletions and reordering — which a
database table does not do by default.

### Scanned the field, then aimed at the gap rather than the features

Re-scanned Track 03: **60 public repos**, 23 touched in the last 48 hours. The
strongest is `agastyasharma20/revenue-recovery-agent` — a LinUCB contextual
bandit with a 30,000-round convergence demo, 0/1 knapsack portfolio
optimisation under a human-review budget, a deployed live demo, and a README
that is genuinely careful: "not cherry-picked", "what's simulated vs real",
"real bugs caught during development, left in this README on purpose".

That is close to this project's own positioning, and better resourced on
features. Competing on feature count against a bandit and a knapsack in four
days would be losing slowly.

But their headline is **"recovery rate of pursued cases: 11.0% → 36.9%"**. No
self-recovery baseline, and "pursued cases" as the denominator. That is exactly
the framing this project exists to argue against, and they are not unusual — it
is the category norm.

So rather than name anyone, `eval/report_variants.py` makes the point with this
project's own data. One run, one policy, one population, five framings:

```
Recovery rate, pursued cases only      60.4%
Gross recovery rate, all cases         54.3%
Total rupees recovered            Rs 194,119
Attributable to an action         Rs 132,593
INCREMENTAL vs control arm         Rs 38,274   <- honest
```

**68 of 163 "recoveries" — 42% — were customers who paid without the agent
doing anything.** Every framing except the last counts them as a win. The
rupee spread is five-fold, and "60.4% recovery rate" is a headline anyone would
publish.

Six tests pin it, including that the incremental figure must remain the
*smallest* rupee number and that the spread must stay above 3x. If either
breaks, the argument stops being supported by its own data and the README needs
rewriting rather than the test relaxing.

This is the strongest thing in the project. Not because it flatters the work —
it does the opposite, it takes the headline from Rs 194,119 to Rs 38,274 — but
because it is checkable, it is about method rather than features, and no amount
of engineering elsewhere answers it.

### Renamed: Wapsi -> Chukta

Searched GitHub for the rest of the Track 03 field and found 40 public repos.
One of them is `PythonScript32/wapsi` — same name, same track, same one-line
pitch, created **five days before this project started**, and carrying
`CLAUDE.md`, so built with the same tooling.

The architecture overlap is close enough to be uncomfortable: fail-closed
governance gates, append-only audit, promise-to-pay pausing outreach, salary-day
timing, revoked mandates never charged, RBI pre-debit notice, idempotent
executor, holdout batch.

The benign explanation is almost certainly the right one. *Wapsi* is the obvious
Hindi word for a recovery product; two Claude-built projects reading the same
track brief in the same regulatory context converged. But that reasoning is
useless to a judge looking at two identically-named entries, one of which is
timestamped earlier.

**Renamed to Chukta** (चुकता — *settled, paid up*). It names the outcome rather
than the act of collecting, which suits a project whose thesis is restraint.
*Vasooli* was the obvious alternative and was rejected on exactly that ground —
it connotes strong-arm collection, which is backwards here.

221 lowercase, 44 title-case and 12 uppercase occurrences across 44 files, plus
the package directory and the Razorpay `notes` keys (`wapsi_event`,
`wapsi_idem`) that would otherwise have leaked the old name into live API
payloads. One thing deliberately NOT renamed: references to the competitor's
repo, which still says `wapsi` because that is its name.

The competitive analysis is now written down in `docs/prior-art.md` rather than
living only in a chat log — including the finding that their naive baseline
carries a hand-tuned constant whose comment admits it exists to keep the
reported lift looking believable. That is the same failure this project's
sensitivity sweep exists to catch, and did catch.

### Built, tested, documented — and imported by nothing

`chukta/compose.py` and `chukta/replies.py` each had a full test suite and an ADR.
Neither was imported by a single line of production code. 186 tests passed.

This is the same failure as `contact_budget: 2`, which was designed, documented
and inert. That one was **analysed, never built**. This one was **built, never
wired**. Both are invisible to a test suite that only asks whether modules work,
because a module can work perfectly while nothing calls it.

**Fix.** Composition now runs in `chukta/trace.py`, `demo_live.py` and the
dashboard, so every contact step shows the copy a customer would actually read
rather than a frame label. Reply handling runs in the demo, which finally makes
`G-OPS-07` and `TerminalState.PROMISE_TO_PAY` demonstrable:

```
  INBOUND REPLY
    "will pay on the 5th"  -> promise_to_pay  (high)
      customer promised to pay by 05 Sep; case paused by G-OPS-07
      next action would be blocked by: ['G-OPS-07']
    "STOP"                 -> opt_out         (high)
      next action would be blocked by: ['G-TRAI-03']
```

### The test that was supposed to catch it did not

Wrote `tests/test_wiring.py` to make orphaning impossible. Then checked it by
deleting the import again — and **all 13 tests still passed**.

They grepped the source for `"Composer"`. Removing the import left the name in
the function signature, so the string was still there and the assertion held.
A test a stale string can satisfy is worse than no test: it converts an
unnoticed gap into a confidently-wrong green tick.

Rewrote them to run the thing and inspect the output — the trace must *print* a
composed message, the demo must *show* a promise blocking on G-OPS-07, the API
must *return* message text over twenty characters. Re-ran the deletion: one test
failed, correctly.

The lesson is narrow and worth keeping: a structural test asserting that code
is *called* has to execute the call. Grepping for a symbol tests the spelling.

### Smaller things

- `serve.py --help` started the server instead of printing help. It had no
  argparse at all, so it accepted no arguments — no port, no host. Fixed.
- `pytest.ini` added. Every run printed a pytest-asyncio deprecation warning
  that had nothing to do with this project, which is exactly how people learn
  to ignore warnings.

199 tests.

---

### Auditing my own claims found three that were false

Went looking for what was left undone and found, instead, three things the
README asserted that the code did not do.

**"The model is used in exactly three places."** There was not one LLM call in
the repository. This is the second overclaim of the same shape — the first was
"execution is real against test mode" — and both survived days of work because
nothing tests prose. Fixed by building the one use that earns its place
(`chukta/compose.py`, message copy inside a fixed frame, behind a deterministic
coercion guard) and rewriting the section to say one place, naming the two that
were dropped and why. See [ADR 0008](docs/adr/0008-model-writes-only.md).

Worth noting the track is *AI* Revenue Recovery and the honest answer was "there
is no AI in it". Building the component was the right response; claiming it
already existed was not.

**"Five stopping rules, and nothing else ends a case."** Four could fire.
`G-OPS-07` reads `promise_to_pay_until` and **nothing anywhere set it** —
`TerminalState.PROMISE_TO_PAY` was unreachable. `chukta/replies.py` now parses
inbound replies into opt-out, promise-to-pay with a date, dispute, or unknown,
deterministically. Opt-out beats every other intent, because "stop, I already
paid" is still an opt-out and getting that precedence wrong is a regulatory
problem rather than a UX one.

**The TRAI message classification was wrong.** Every step was labelled
`transactional`. TCCCPR defines a transactional message as one sent *"within
thirty minutes"* of a customer-initiated transaction — an OTP, a confirmation.
A recovery notice sent 24 hours to 8 days after a *failed* payment is a
**service** message: same exemptions, different category.

The behaviour was already right, which is exactly why no test caught it — every
test asserted the outcome, and the outcome was correct. The reasoning underneath
it was not. That is worth fixing in a project whose pitch is that its compliance
reasoning is inspectable. [ADR 0007](docs/adr/0007-service-not-transactional.md).

The cause was reading law-firm summaries rather than the regulation. Every
secondary source says "transactional and service messages are exempt from the
promotional window" — true, and it obscures that they are two categories.

**What the same pass confirmed as correct:** the ₹15,000 AFA-free limit (RBI,
16 Jun 2022), the 24-hour pre-debit notification (verbatim from the circular:
*"at least 24 hours prior to the actual charge / debit to the card"*), the
₹1 lakh ceiling and its exact three categories, and the 10:00–21:00 promotional
window. Four out of five right is a better hit rate than I expected from
secondary sources, and the one miss was the one that mattered.

### Percentiles bought a deadline term

Capping the wait at 168 hours (`max_wait_hours`) collapses p95 from 302h to 168h
and p99 from 326h to 168h. The expectation was that this would cost revenue -
the whole timing advantage comes from waiting for salary dates.

It does not. The 12-seed mean went **up**, 19,570 to 20,347, and the standard
deviation fell from 12,602 to 10,764. Bounding the wait removed variance rather
than revenue: the long waits were not paying for themselves, they were adding
noise. It does cost 7% on seed 20260829 specifically, which is a useful reminder
of why single-seed results were retired.

### The differentiator was analysed and never built

**Problem.** Built `eval/compare_systems.py` to answer a question the prior-art
review forced: how much of the measured gain is commodity, and how much is
this project? Four arms — blind ladder, smart timing, reason-aware, Chukta —
through the identical harness.

The Chukta arm came back **byte-identical** to the unbounded reason-aware
baseline. Same revenue, same contacts, same churn, to the rupee.

`contact_budget: 2` never bound. The ladders rarely propose two contacts on one
case, so a cap of two constrained nothing. The whole "bounded contact"
differentiator was inert, and had been for the entire build. Worse, the Qini
analysis had already said what to do — *30% of the ranked file carries 85% of
the incremental revenue* — and the policy simply did not act on it. I had
written the analysis, quoted it in the README, and never implemented it.

Nothing in the test suite could have caught this. 81 tests passed. Every one of
them tested a rule in isolation; none asked whether the rules *taken together*
did anything a simpler system would not.

**Fix.** `G-OPS-08`: score each case as `class_prior x amount`, and refuse
outreach below a threshold. `eval/tune_threshold.py` sweeps the threshold and
prints the frontier, so the shipped value of 511 is the highest one retaining
>=97% of revenue — derived rather than chosen, which is exactly what the old
`contact_budget: 2` was not.

Charge retries are deliberately exempt: a retry costs fees, not goodwill, and
cannot cause a cancellation.

**Result, stated the way it came out.** Against no threshold, over 12 seeds:
extra contacts 200 -> 59 (-70%), extra churn 4.6 -> 1.3 (-72%), incremental
revenue 26,197 -> 19,570 (-25%).

**On revenue, this project is worse than a reason-aware baseline.** The
head-to-head decomposition is not flattering:

```
 -> smart_timing     -6,497    commodity
 -> reason_aware    +32,694    commodity
 -> chukta            -6,627    this project
```

Almost all the gain is commodity, and it comes from diagnosis — timing alone is
*negative* here. The contribution gives revenue up to buy restraint.

The defence is real but conditional: a 14-day horizon prices a cancellation at
one missed payment, when a cancelled subscriber stops paying forever. Break-even
works out at **Rs 2,039 per avoided cancellation**, roughly 1.5 billing cycles.
Most subscription businesses clear that easily. But the report says the number
rather than assuming it, because measuring it needs retention data this project
does not have.

**A bug the fix introduced.** The first version applied `G-OPS-08` to the
control arm too, because `sim/run.py` passed one policy dict to both arms. That
measured the gate against itself and inflated the reported delta by about 24%
(24,212 vs the true 19,570). `eval/check_claims.py` caught it within seconds by
flagging that *control* charge attempts had moved 365 -> 366 — a number that
should have been impossible to change. The same contamination was in
`eval/sweep.py` and in `check_claims` itself. All three now route through
`sim/baselines.policy_for_arm`.

That is the second time the claim-checker has paid for itself, and the first
time a *baseline* number moving was the thing that exposed a bug.

### Running an engineering checklist against the project, late

Applied a prior-art / benchmark / decision-record checklist on day 2. Two items
came back as outright failures, and both were the ones that determine whether
you are rebuilding something that already exists.

**Failure 1 — no prior-art review.** Done now, in `docs/prior-art.md`, and it
cost the project two of its three claimed differentiators.

Reason-aware retry timing is commodity: Stripe Smart Retries trains on 500+
attributes across billions of payments, and Butter, Revaly, FlyCode, Slicker
and **Razorpay Subscriptions itself** all ship retry logic. Worse, the
incremental-measurement critique I had been presenting as this project's
insight is the industry's own published self-criticism — Yuno and Slicker both
write it plainly. I reached it independently, not first.

What survives is narrower and I have rewritten the README to say only that:
vendors publish gross rates anyway, so the contribution is shipping the honest
metric as the *only* headline, plus negative-uplift modelling, pre-execution
gating, and a per-decision audit row. Those are the columns where the
comparison table still says no across the board.

Doing this before the build would have pointed the same seven days at a
narrower and better target.

**Failure 2 — I invented my own benchmark.** Every number came from a simulator
I wrote, scored by a metric I wrote. The checklist warns that a benchmark you
invent flatters your design; the sensitivity sweep had already demonstrated it
empirically, and I found that by luck rather than by method.

Fixed in two layers. `tests/test_qini.py` checks the curve against cases
computable on paper — including a worked example whose coefficient is
5.5/12 — plus the structural properties Radcliffe & Surry define: a good
ranking scores positive, an inverted one negative, an uninformative one exactly
zero. All twelve passed first run, which is reassuring rather than impressive.

The stronger fix is external: `tests/test_qini_hillstrom.py` runs against
Hillstrom's 2008 randomised e-mail experiment, 64,000 customers that nobody
here generated. That data is **unpaired**, which exposed a real gap — the paired
form works only because the simulator runs both arms over identical customers
under common random numbers. Live traffic never looks like that. So
`qini_scaled` now implements the standard treated/control ratio correction, and
the test pins its endpoint to the published +7.66pp lift (1631.89 incremental
responders) exactly. The test I care about most is that a *random* ranking
scores near zero — that is the one that would catch a curve which flatters
every ordering.

**Tails, which the means were hiding.** Reporting p50/p95/p99 rather than
averages surfaced something no other metric had: Chukta is **2.4× slower**.
Median time to recovery 25h → 61h, p99 151h → 355h. It waits for salary days
and honours cooldowns, and the cost is customers waiting a fortnight. For a
merchant with cash-flow constraints that trade may not be worth Rs 26,000. The
policy has no deadline term and probably needs one.

**A README that cannot go stale.** `eval/check_claims.py` turns every figure
quoted in the README into an assertion and runs in CI. Adding a claim to the
README is now the price of quoting a number. Six single-seed claims and two
sweep claims currently hold.

**Also done:** CI on three Python versions with a separate reproducibility job
that fails if the headline numbers move; environment capture (Python, numpy,
platform, policy hash) written into every run manifest, because a seed alone is
not reproducibility; and five ADRs recording what was rejected and why — the
half that stops the same argument being relitigated.

### Building the dashboard found two things the CLI had hidden

**Problem 1 — the TRAI gates can never fire.** Wiring the inspector's clock
slider, I expected `G-TRAI-01` to flip when the hour passed 21:00. It did not,
at any hour. Two independent reasons, both invisible from the CLI:

1. Every step in `policy.yaml` is `transactional`. A bare recovery notice is
   service traffic, so the promotional window never applies. Correct — and it
   means `G-TRAI-01` and `G-TRAI-02` are dead code in the default policy.
2. `PolicyEngine.decide` already calls `resolve_when` with the contact window,
   so contacts are *scheduled into* 10:00–21:00 before the gate ever sees
   them. A correctly scheduled action structurally cannot trip the timing gate.

Neither is a bug. Together they mean the guardrail a reviewer most wants to see
is the one that never fires. The unit tests pass because they construct actions
directly and bypass the scheduler — so the suite was green on a code path
production never takes.

**Fix.** Two what-if controls that change the *input*, never the rule:
`attach_offer` reclassifies the message (which is what TCCCPR says an offer
does), and `send_now` bypasses the scheduler (which is what a naive integration
does, and precisely what the control arm does on every contact). With both on,
the gate flips at exactly 21:00. `tests/test_web.py` now pins the boundary at
20:00 pass / 21:00 block, and pins that the scheduler alone keeps the gate
quiet — so the two layers are asserted to be independent rather than assumed.

**A bug in my own fix.** The first `send_now` did nothing. I overrode the `now`
passed to `evaluate()`, but `G-TRAI-01` reads `action.scheduled_for` — because
what matters is when the message *lands*, not when the decision was taken. The
gate was right and my override was incomplete. Worth recording because the
symptom looked exactly like a broken gate.

**Problem 2 — a stale server lies convincingly.** Two rounds of debugging went
into a feature that was already working: `pkill` does not exist on Windows, so
the old uvicorn process kept serving old code while I edited files and re-ran
curl. The fix is environmental, not architectural, but the lesson is that a
dev server which silently survives its own restart command is a debugging trap.

**The architectural risk a dashboard introduces.** A UI that computes anything
itself becomes a second source of truth, and the two drift the first time one
side is edited — a stale verdict rendered authoritatively is worse than no UI.
So `web/app.py` holds no rules, and `tests/test_web.py` enforces it: the API
and the engine are called with identical input and must return identical
answers. If someone later inlines `if amount > 15000` into the endpoint to save
a call, the build breaks.

### The headline number was the best of twelve seeds

**Problem.** Everything so far ran on seed 20260829 at n=300, and the README
reported its +Rs 47,382 as *the* result. Asking "how are we actually testing
this?" produced `eval/sweep.py`, and the answer was uncomfortable:

```
mean 26,197   sd 12,200   range [9,557, 47,382]   positive 12/12
```

47,382 is the **maximum** of the twelve. The seed was picked before any numbers
were seen, so this was not deliberate cherry-picking — which is exactly why it
is worth recording. A single-seed result on n=300 with this much variance is an
anecdote, and it read as a measurement.

**Fix.** The headline is now the 12-seed mean with an interval, and the
single-seed table is explicitly labelled illustrative. The stable claim turns
out to be the attempt reduction, not the revenue.

### The result depends on an assumption I cannot defend

**Problem.** The sensitivity pass perturbs one belief in
`sim/response_model.py` at a time and re-runs all twelve seeds. Six of seven
scenarios keep the sign. One does not:

```
message frames do nothing     mean -7,778     positive 4/12
outreach barely works         mean  9,991     positive 9/12
```

**If the behavioural message frames carry no lift, Chukta is net negative.**
The frames come from tax-compliance RCTs — Guatemala, n=43,387 — where the ask,
the authority relationship and the consequence of ignoring the letter are all
different from a failed subscription renewal. The README always said that
transfer was an assumption. It did not say the entire result rested on it.

**What this changes.** The claim is no longer "the agent recovers more money."
For a policy permitted to contact people more often, that was never in doubt.
The claim is that the effect is **contingent on message quality**, and that
better targeting with indifferent copy is worse than doing nothing at all. That
reframes what to measure first on live traffic: not the routing, the copy.

Worth noting what did *not* break. Doubling churn propensity moved the mean by
about 1%, so the policy is not quietly buying revenue with cancellations —
which was the thing I most expected to find.

**A bug found while writing this.** The first version of `sensitivity()` ended
with "the sign survives every perturbation" while printing two rows where it
plainly did not — the conclusion was hardcoded to check one scenario instead of
being derived from the results. A summary line that cannot contradict its own
table is worse than no summary line.

### Cutting the live API integration

**Problem.** After the credential incident, the question was whether to keep
chasing a working test-mode integration at all. Honest accounting of what it
would have bought: a handful of `payment_link.create` calls against an account
with no real customers, on a corpus that is replayed anyway.

**Decision — cut it.** The claim this project makes is about *measurement*:
that recovery agents get credited with revenue that would have arrived without
them, and that the honest number is the incremental one. Defending that needs a
control arm, common random numbers and a Qini curve. It does not need a live
API. A few real calls would not have strengthened the causal argument by a
single basis point, and they would have added a network dependency to a demo
whose entire value is that it reproduces exactly.

The temptation was to keep it because "real API integration" reads well on a
submission. That is a reason to demo something, not a reason to build it.

**What this cost, stated plainly.** The README previously claimed payment
execution was *real* against test mode. That claim is now false and has been
removed rather than softened. Chukta does not call Razorpay. `chukta/execute.py`
renders the exact request — endpoint, payload, idempotency key — and stops.

**What survived, and why it was worth keeping.** Everything that happens
*around* the call is where the failure modes actually live, and none of it
needs a network to test: the credential guard, deterministic idempotency, the
`in_flight` escalation, the circuit breaker, fingerprinted audit rows. Twenty
offline tests cover them. The guard is retained even though nothing loads a key,
because the first thing anyone extending this will do is add one.

`python -m chukta.trace` replaced the live demo, and is better on camera:
event → diagnosis with the tier that fired → action → every gate with its
verdict → the rendered request. The mandate case blocks on three gates at once
(G-RBI-01, G-RBI-02, G-OPS-05), which is the clearest possible demonstration
that gates do not short-circuit. A live call could not have shown that.

### Idempotency is about the unknown outcome, not the duplicate

**Problem.** The obvious idempotency bug is sending the same charge twice. The
harder one is a request that goes out and whose response never comes back —
timeout, crash, dropped connection. The call may or may not have charged. Both
naive answers are wrong: re-issue and you may double-charge; assume success and
you drop a real recovery.

**Fix.** The ledger row is written **before** the request, so a process that
dies mid-flight leaves an `in_flight` row rather than a silent gap. On seeing
one, `execute()` returns `needs_reconciliation` and touches nothing — the case
goes to a human. Knowing that you do not know is a state worth representing.

The key itself excludes wall-clock time, keyed only on
`(event_id, action, channel, attempt_no)`, precisely so a retry issued minutes
later collides with the call that timed out. A timestamp in the material would
have made every re-issue look novel and defeated the whole mechanism.

**Also worth noting.** Razorpay has no universal idempotency header on the
Payments API, so `reference_id` on the payment link is derived from the same
key. The gateway rejects the duplicate as a second line of defence; the local
ledger catches it first.

### The retry action degrades honestly

A silent re-presentment needs a saved token. The replayed corpus has none, and
test mode will not conjure one. Rather than mock a charge that always succeeds,
`_retry_charge` falls back to a payment link and marks the response
`degraded_from: retry_charge`. The demo therefore shows what actually happens
rather than what the architecture diagram promises.

---

## Templates for what is still coming

- [x] Idempotency on live retries — done, day 2
- [x] Circuit breaker behaviour on a gateway 5xx — done, day 2
- [ ] Model non-determinism vs. reproducible metrics (keep the model off the
      critical metric path)
- [ ] Live-vs-replayed event disclosure in the run manifest
- [ ] Deriving `contact_budget` from the Qini peak rather than by hand
- [ ] Doubly robust estimate vs. the naive difference — do they agree?
