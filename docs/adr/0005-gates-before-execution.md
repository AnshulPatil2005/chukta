# ADR 0005 — Compliance checked before execution, per action

**Status:** accepted · 29 Aug 2026

## Context

An agent that spends money and customer goodwill needs regulatory constraints
enforced somewhere. India adds two hard ones: the RBI E-mandate Framework (AFA
limits, 24-hour pre-debit notice, revoked mandates) and TRAI TCCCPR (messaging
windows, DND, service vs promotional traffic classification).

## Decision

`chukta/gates.py` evaluates every proposed action **before** anything executes.
Rule IDs are stable and quotable (`G-RBI-*`, `G-TRAI-*`, `G-OPS-*`). Every gate
runs — no short-circuit on first failure — and blocked actions are logged with
their blocking rule.

## Alternatives rejected

**Encode constraints in the policy ladder itself.** Fewer moving parts, and the
scheduler already does some of this. Rejected: it conflates "what we want to do"
with "what we are permitted to do", and a reviewer cannot then see that the
constraint was checked, only that nothing bad happened to be attempted.

**Post-execution monitoring and alerting.** The common production pattern.
Rejected: by the time the alert fires, the debit has happened. Checking a
proposed action is cheaper and safer than inspecting side effects once money has
moved.

**Short-circuit on the first failing gate.** Faster. Rejected: the audit row
should show the complete verdict, not the first objection. The ₹45,000 mandate
case blocks on three independent rules, and knowing all three is the point.

**A general policy engine (OPA / Rego).** Rejected: another language and runtime
for fourteen rules, and it would move the rules out of the one file a compliance
reviewer can read end to end.

## Consequences

- The gate layer deliberately duplicates some scheduling logic — the policy
  schedules contacts inside the permitted window, *and* the gate checks the
  window. That redundancy means the gate almost never fires in normal operation,
  which made it look like dead code until the dashboard exposed it (`JOURNAL.md`,
  30 Aug). The redundancy is the point: the scheduler is an optimisation, the
  gate is the guarantee.
- Gate results are part of the audit row, so "we were compliant" is evidenced
  per decision rather than asserted per system.
- The thresholds are transcribed from secondary sources and still need
  verification against the RBI circular and the TRAI amendment themselves. This
  is the largest outstanding correctness risk in the project.
