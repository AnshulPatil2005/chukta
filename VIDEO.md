# 5-minute pitch — shooting script

Read this off the screen. Every command is copy-pasteable and every number it
prints is asserted in CI, so nothing here can go stale without the build going
red.

**Before recording:** run `python -m sim.run --n 300 --seed 20260829` once so
the eval commands have data, and `rm -f runs/demo_live*.jsonl` so the live demo
creates a fresh link rather than reporting a duplicate.

---

## 0:00–0:45 — The problem

> "A payment fails. In subscriptions that is usually a cancelled customer, not
> one missed charge.
>
> The industry default is a fixed retry ladder — T+1h, T+24h, T+72h, same card,
> plus a generic SMS. It's wrong twice. It ignores *why* the payment failed: an
> expired card retried three times gives you three guaranteed declines and a
> worse decline ratio with the issuer. And it ignores the *cost* of trying —
> messaging a lukewarm subscriber can cause the cancellation you were trying to
> prevent."

*No terminal yet. Just say it.*

---

## 0:45–1:45 — The measurement trap (this is the pitch)

```bash
python -m eval.report_variants
```

> "Before showing you what I built, here's the number I refuse to lead with.
>
> This is one run. One policy. One population. The only thing that changes is
> the framing."

*Point at the table as it prints.*

> "Sixty-point-four percent recovery rate. Fifty-four percent. One hundred and
> ninety-four thousand rupees. All true, all from this same run.
>
> The honest number is thirty-eight thousand — five times smaller — because
> **sixty-eight of a hundred and sixty-three 'recoveries' were customers who
> paid without the agent doing anything.** Every framing except the last counts
> them as a win.
>
> That's not a hypothetical about someone else's marketing. It's my own data,
> and it's why everything after this is incremental."

---

## 1:45–3:00 — The system, live

```bash
python demo_live.py --live --fresh
```

> "One real failed payment, end to end, against the Razorpay test API."

Walk the output as it appears:

- **Diagnosis** — "`source × step × reason` from Razorpay becomes a
  recoverability class. Tier one matched the slug directly, high confidence."
- **Decision** — "Auth dropout, so a payment link, not another retry on the
  same rail."
- **Gates** — "Seven rules, all evaluated. Nothing short-circuits, because the
  audit row should show the complete verdict, not the first objection."
- **Message** — "The copy a customer actually reads, written inside a fixed
  behavioural frame, checked by a deterministic coercion guard."
- **The link** — "That's a real payment link, created after the gates passed."
- **Inbound reply** — "And the other half: a customer says 'will pay on the
  5th', and the case pauses. G-OPS-07."

Then the one to linger on:

```bash
python -m chukta.trace
```

*Scroll to `evt_mandate`.*

> "₹45,000 recurring debit, no AFA on file. Three gates block it at once —
> two RBI, one operational. A live API call can't show you that, because the
> call never happens."

---

## 3:00–4:30 — Results, including the half that hurts

```bash
python -m eval.sweep --seeds 12 --sensitivity
```

*(Pre-run this — it takes about a minute. Show the output.)*

> "Twelve seeds. Plus twenty thousand rupees mean, ninety-five percent interval
> fourteen to twenty-six thousand, positive in twelve of twelve. Cost: fifty-nine
> extra contacts, one-point-four extra cancellations.
>
> Now the part most demos skip."

*Point at the sensitivity table.*

> "Each row perturbs one belief in the simulator. Six survive. One doesn't:
> **if the behavioural message frames carry no lift in a payments context, this
> agent is net negative** — minus seven thousand, positive in only four of
> twelve seeds. Those frames come from tax-compliance trials in Guatemala.
> Nothing here tests whether they transfer to Indian subscriptions."

```bash
python -m eval.compare_systems --seeds 12
```

*(Also pre-run.)*

> "And running the competing strategies through the same harness: nearly all the
> gain is commodity. Diagnosis contributes thirty-three thousand. My own
> contribution is *minus* six thousand — it gives up revenue to buy forty-nine
> percent fewer contacts and forty-four percent less churn. That trade only pays
> off if a retained customer is worth more than two thousand rupees in future
> billing."

---

## 4:30–5:00 — Close

> "So what would I measure first on real traffic? Not the routing — the copy.
> The sensitivity analysis says that's the load-bearing assumption, and it's the
> one thing a simulator cannot settle.
>
> Two hundred and sixty-three tests. The Qini implementation is validated
> against a published 1998 randomised experiment, not just against my own
> simulator. The off-policy estimator lands within five percent of ground truth.
> And every number in the README is a CI assertion — if the code stops matching
> the claims, the build goes red.
>
> That's Chukta."

---

## Notes

- **Don't apologise for the negative findings.** They are the strongest thing
  here. Most submissions will claim a big number; "here is the assumption that
  makes my contribution net negative" is much harder to fake.
- **Pre-run the two slow commands** (`sweep`, `compare_systems`) and show the
  output, or the video is 40% waiting.
- If short on time, cut the `chukta.trace` segment — the mandate case also
  appears in the dashboard, and section 2 is the one that must survive.
- Screen-record at 1080p minimum; the terminal output is the visual.
