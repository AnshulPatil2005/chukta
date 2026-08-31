"""Decision inspector: a browser front end for the same engine the CLI runs.

    uvicorn web.app:app --reload
    open http://127.0.0.1:8000

The one rule this module follows is that it owns **no policy**. Every endpoint
delegates to `chukta.taxonomy.classify`, `chukta.policy.PolicyEngine.decide`,
`chukta.gates.evaluate` and `chukta.execute.Executor` - the same call path as
`python -m chukta.trace`. A dashboard that reimplements a rule to render it
becomes a second source of truth, and the two drift the first time one side is
edited. If this file ever contains an `if amount > 15000` it is a bug.

It executes nothing. The executor stays in dry-run, so the panel showing the
outgoing request is showing a rendered payload, not a call that was made.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chukta.audit import read as read_audit
from chukta.clock import to_ist
from chukta.compose import Composer
from chukta.execute import Executor, IdempotencyLedger
from chukta.gates import CaseState, blocking, evaluate
from chukta.policy import PolicyEngine, load_policy
from chukta.taxonomy import REASON_RULES, SOURCE_STEP_DEFAULTS, classify
from chukta.types import (
    ActionType,
    Channel,
    Customer,
    FailureEvent,
    MandateContext,
    MessageClass,
    PaymentType,
    RecoverabilityClass,
)

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
RUNS = ROOT / "runs"

app = FastAPI(title="Chukta decision inspector", docs_url="/api/docs")

POLICY = load_policy()
ENGINE = PolicyEngine(POLICY)
EXECUTOR = Executor(dry_run=True, ledger=IdempotencyLedger(RUNS / "_web.jsonl"))
COMPOSER = Composer(POLICY)

SOURCES = ["issuer", "customer", "business", "bank", "gateway", "network", "NA"]
STEPS = [
    "payment_initiation",
    "payment_authentication",
    "payment_authorization",
    "payment_response",
]


class DiagnoseRequest(BaseModel):
    source: str = "issuer"
    step: str = "payment_authorization"
    reason: str = "card_expired"
    amount_rupees: float = Field(default=1299.0, ge=1, le=1_000_000)
    payment_type: str = "one_time"
    # Mandate context, only read when payment_type is "mandate".
    afa_completed: bool = False
    mandate_category: str = "general"
    mandate_revoked: bool = False
    pre_debit_notified_hours_ago: float | None = None
    # Customer context the TRAI gates read.
    dnd_registered: bool = False
    opted_out: bool = False
    # What-if: the merchant attaches an offer or a discount to the copy.
    # Under TCCCPR that reclassifies the traffic from service to promotional,
    # which narrows the delivery window and makes DND binding. This changes the
    # message, not the rule - see the comment at the call site.
    attach_offer: bool = False
    # What-if: fire the action at the current hour instead of the slot the
    # policy scheduled it into. This is what a naive integration does, and what
    # the blind control arm in sim/ does - which is why the control arm racks
    # up 234 gate blocks against the agent's 6.
    send_now: bool = False
    # Hour of day in IST, so a reviewer can watch the quiet-hours gate flip.
    ist_hour: int = Field(default=11, ge=0, le=23)


def _event(req: DiagnoseRequest, now: datetime) -> FailureEvent:
    mandate = None
    if req.payment_type == "mandate":
        notified = None
        if req.pre_debit_notified_hours_ago is not None:
            notified = now - _hours(req.pre_debit_notified_hours_ago)
        mandate = MandateContext(
            afa_completed=req.afa_completed,
            pre_debit_notified_at=notified,
            category=req.mandate_category,
            revoked=req.mandate_revoked,
        )
    return FailureEvent(
        event_id="web_probe",
        customer_id="web_customer",
        amount_paise=int(round(req.amount_rupees * 100)),
        occurred_at=now,
        source=req.source,
        step=req.step,
        reason=req.reason,
        payment_type=PaymentType(req.payment_type),
        mandate=mandate,
    )


def _hours(h: float):
    from datetime import timedelta

    return timedelta(hours=h)


def _now_at_ist_hour(hour: int) -> datetime:
    """A UTC instant that lands on `hour` IST today.

    The gates work in IST via zoneinfo; the slider in the UI is the honest way
    to let someone watch G-TRAI-01 flip at 21:00 without waiting for evening.
    """
    from datetime import timedelta

    today = datetime.now(timezone.utc).date()
    # IST is UTC+05:30.
    return datetime(
        today.year, today.month, today.day, 0, 0, tzinfo=timezone.utc
    ) + timedelta(hours=hour - 5, minutes=-30)


@app.get("/api/vocab")
def vocab() -> dict[str, Any]:
    """Everything the form needs, derived from the engine rather than hardcoded
    in JavaScript - so adding a reason slug to taxonomy.py updates the UI."""
    return {
        "sources": SOURCES,
        "steps": STEPS,
        "reasons": sorted(REASON_RULES),
        "reason_classes": {k: v.value for k, v in REASON_RULES.items()},
        "source_step": {f"{s}/{st}": c.value for (s, st), c in SOURCE_STEP_DEFAULTS.items()},
        "classes": [c.value for c in RecoverabilityClass],
        "categories": ["general", "insurance", "sip", "credit_card_bill"],
        "defaults": POLICY["defaults"],
        "rbi": POLICY["rbi"],
        "trai": POLICY["trai"],
    }


@app.post("/api/diagnose")
def diagnose(req: DiagnoseRequest) -> dict[str, Any]:
    """Classify, then walk the whole policy ladder, gate by gate.

    State advances between steps exactly as it does in the simulator, so the
    contact cooldown and budget gates fire on the later steps rather than every
    step being evaluated against a fresh slate.
    """
    now = _now_at_ist_hour(req.ist_hour)
    event = _event(req, now)
    klass, evidence = classify(event)

    customer = Customer(
        customer_id=event.customer_id,
        dnd_registered=req.dnd_registered,
        opted_out=req.opted_out,
    )
    state = CaseState()
    steps_out: list[dict[str, Any]] = []

    for i in range(len(ENGINE.steps_for(klass))):
        action = ENGINE.decide(event, klass, i, now)
        if action is None:
            break

        # Every step in policy.yaml is SERVICE class - a bare recovery notice
        # is service traffic, so the promotional window never binds. That makes
        # G-TRAI-01 and G-TRAI-02 invisible in the default policy, which is the
        # right outcome operationally and a useless one for a reviewer trying
        # to see whether the guardrails work.
        #
        # This flag reclassifies the MESSAGE, not the rule: attaching an offer
        # is exactly what turns service traffic into promotional traffic under
        # TCCCPR. The gate logic is untouched and still lives in chukta/gates.py.
        if req.attach_offer and action.channel in (
            Channel.SMS, Channel.WHATSAPP, Channel.EMAIL
        ):
            action.message_class = MessageClass.PROMOTIONAL

        # The policy already schedules contacts into the permitted window, so
        # a correctly scheduled action can never trip G-TRAI-01. That is the
        # design working - and it makes the gate invisible. Bypassing the
        # scheduler is how a reviewer sees the backstop actually catch
        # something, and it is not a hypothetical: it is what the blind
        # control arm does on every single contact.
        #
        # G-TRAI-01 reads action.scheduled_for, not the `now` argument, because
        # what matters is when the message actually lands. So the override has
        # to move the action itself, not just the clock passed to evaluate().
        if req.send_now:
            action.scheduled_for = now
        when = action.scheduled_for or now
        gates = evaluate(action, event, customer, klass, state, POLICY, when)
        blocked = blocking(gates)

        # What the customer actually reads. A frame label tells a reviewer
        # nothing; the rendered copy is the thing they can judge.
        message = None
        if action.channel in (Channel.SMS, Channel.WHATSAPP, Channel.EMAIL):
            m = COMPOSER.compose(action.message_frame, {
                "name": "Priya",
                "amount": f"Rs {event.amount_rupees:,.0f}",
                # Templates already supply the article ("your {merchant}"), so a
                # value carrying its own produces "your your subscription".
                "merchant": "Kirana Box",
                "link": "https://rzp.io/x/...",
                "deadline": to_ist(when).strftime("%d %b"),
                "service": "subscription",
            })
            message = {
                "text": m.text,
                "source": m.source,
                "blocked": m.was_blocked,
                "guard_findings": m.guard_findings,
            }

        rendered = None
        if not blocked and action.type is not ActionType.NO_ACTION:
            result = EXECUTOR.execute(action, event, customer, when, attempt_no=i)
            rendered = {
                "idempotency_key": result["idempotency_key"],
                "call": result.get("would_call"),
                "status": result["status"],
            }

        steps_out.append(
            {
                "index": i,
                "action": action.type.value,
                "channel": action.channel.value,
                "message_class": action.message_class.value,
                "frame": action.message_frame,
                "rule_id": action.rule_id,
                "scheduled_for": when.isoformat(),
                "scheduled_ist": to_ist(when).strftime("%a %d %b %H:%M"),
                "gates": [
                    {"rule_id": g.rule_id, "passed": g.passed, "detail": g.detail}
                    for g in gates
                ],
                "blocked_by": blocked,
                "message": message,
                "executed": rendered is not None,
                "rendered": rendered,
            }
        )

        if blocked:
            break
        if action.channel in (Channel.SMS, Channel.WHATSAPP, Channel.EMAIL):
            state.contacts += 1
            state.last_contact_at = when
        if action.type is ActionType.RETRY_CHARGE:
            state.attempts += 1
            state.exposure_rupees += event.amount_rupees

    return {
        "diagnosis": {
            "klass": klass.value,
            "tier": evidence["tier"],
            "confidence": evidence["confidence"],
            "matched": evidence["matched"],
            "note": evidence.get("note"),
            "hard_decline": ENGINE.is_hard_decline(klass),
            "rationale": POLICY["classes"][klass.value].get("note", "").strip(),
        },
        "context": {
            "ist": to_ist(now).strftime("%a %d %b %H:%M"),
            "amount_rupees": event.amount_rupees,
            "rail": event.payment_type.value,
        },
        "steps": steps_out,
        "terminal": (
            "hard_decline"
            if ENGINE.is_hard_decline(klass) and not steps_out
            else ("blocked" if steps_out and steps_out[-1]["blocked_by"] else "ladder_complete")
        ),
    }


@app.get("/api/policy", response_class=PlainTextResponse)
def policy_source() -> str:
    """The whole policy, verbatim. It is one readable file on purpose."""
    return (ROOT / "policy.yaml").read_text(encoding="utf-8")


@app.get("/api/results")
def results() -> dict[str, Any]:
    """Run metrics, Qini and the robustness sweep, if they have been generated.

    Nothing is computed on request. A twelve-seed sensitivity pass takes most
    of a minute and does not belong inside an HTTP handler.
    """
    from eval.metrics import compare, load_run
    from eval.uplift import oracle, qini, qini_coefficient

    out: dict[str, Any] = {"run": None, "qini": None, "sweep": None}

    latest = RUNS / "latest.json"
    if latest.exists():
        run = load_run(latest)
        out["run"] = {"run_id": run["run_id"], "n": run["n"], "seed": run["seed"], **compare(run)}
        model, orc = qini(run), oracle(run)
        n = len(run["arms"]["chukta"])
        out["qini"] = {
            "fractions": model.fractions,
            "cumulative": model.cumulative,
            "oracle_cumulative": orc.cumulative,
            "final": model.final_value,
            "coefficient": qini_coefficient(model),
            "oracle_coefficient": qini_coefficient(orc),
            "deciles": [
                {
                    "decile": d * 10,
                    "cumulative": model.cumulative[int(round(d / 10 * n))],
                }
                for d in range(1, 11)
            ],
        }

    sweep = RUNS / "sweep.json"
    if sweep.exists():
        out["sweep"] = json.loads(sweep.read_text(encoding="utf-8"))
    return out


@app.get("/api/audit")
def audit(limit: int = 60) -> dict[str, Any]:
    """Tail of the most recent audit file. Append-only, so tailing is honest."""
    files = sorted(RUNS.glob("audit_*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return {"path": None, "rows": []}
    rows = list(read_audit(files[-1]))
    return {"path": files[-1].name, "total": len(rows), "rows": rows[-limit:]}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
