# ADR 0008 — The model writes copy and nothing else

**Status:** accepted · 30 Aug 2026

## Context

The README claimed a model was used in three places: parsing unstructured
signals, writing copy inside a behavioural frame, and arbitrating rule
conflicts. **None of it existed.** There was not a single LLM call in the
repository, and the claim sat there for two days looking authoritative — the
same failure as the earlier "execution is real against test mode" claim.

Meanwhile the sensitivity sweep had identified message quality as *the*
load-bearing assumption in the project: if the behavioural frames carry no lift,
Chukta is net negative (−₹7,691, positive in 4 of 12 seeds). The component that
would carry that lift was the one that had not been built.

## Decision

Build exactly one model-backed component: `chukta/compose.py`, which writes the
message. Correct the README to say one place, and say which two were dropped and
why.

Three constraints make it safe:

1. **It cannot invent an action.** By the time it runs, class, intervention,
   channel and schedule are decided and already through the gates. It receives a
   frame and facts, returns prose. No tools, no action vocabulary.
2. **A deterministic guard checks its output.** `CoercionGuard` is regex and
   rules, not a second model — a model grading a model shares the failure mode
   you are trying to catch. Blocked copy is replaced with a template and the
   substitution is written to the audit row.
3. **It is off the critical metric path.** With no credential the template
   composer runs and every number is unchanged.

## Alternatives rejected

**Also use a model for reply parsing.** It would read "will pay after Diwali"
better than regex does. Rejected: reply parsing decides whether to keep
messaging someone, and opt-out is a legal obligation. That belongs in code a
reviewer can read line by line, not in a component that is right 97% of the
time. `chukta/replies.py` is deterministic.

**Also use a model to arbitrate conflicting rules.** Rejected: no case in the
current policy has two rules in conflict. It was a solution looking for a
problem, which is why it was described and never built.

**Use a model as the coercion classifier.** Better recall on paraphrased
threats. Rejected: the guard has to hold when the generator is having a bad day,
and correlated failure is exactly what you cannot afford in a component whose
job is catching the generator.

**Ship no model at all** and argue rules are the right answer. Defensible, and
close to what the project already was. Rejected because copy is genuinely
open-ended — a template that says the same sentence to a hundred thousand people
reads like a template — and because the analysis had already identified this as
the highest-leverage place to improve.

## Consequences

- The submission's answer to "where is the AI?" is one specific, defensible
  place rather than a list.
- No credential is required for anything measured. The composer degrades to
  templates, and the pipeline is unaffected.
- **A model outage cannot stop recovery** — it degrades to templates rather than
  failing.
- The templates are guard-checked too. If a template is ever edited into
  something unsendable there is no safer fallback left, so `Composer` raises
  rather than sending.
- **The honest limitation:** `sim/response_model.py` scores the *frame*, not the
  words. Nothing here tests whether better copy converts better, so building
  this does not resolve the load-bearing assumption — it only makes the
  assumption testable on live traffic.
