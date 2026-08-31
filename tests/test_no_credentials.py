"""The measurement pipeline must run with no credentials at all.

This is the load-bearing claim of ADR 0009: adding a live demo path did not
make the numbers depend on a network. If any part of `eval/` or `sim/` grows a
credential dependency, the README stops being reproducible by a stranger with a
clone and no Razorpay account - which is the whole basis for asking anyone to
believe it.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def no_credentials(monkeypatch, tmp_path):
    """Strip every credential source: env vars and the .env file."""
    for k in list(os.environ):
        if k.startswith(("RAZORPAY_", "CHUKTA_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("chukta.execute.ENV_PATH", tmp_path / "absent.env")
    return tmp_path


def test_measurement_needs_no_credentials(no_credentials):
    """The full two-arm run, end to end, with nothing configured."""
    from eval.metrics import compare
    from sim.baselines import policy_for_arm
    from sim.control_policy import BlindRetryPolicy
    from sim.corpus import build_corpus
    from sim.population import build_population
    from sim.run import START, run_case
    from eval.sweep import NullAudit
    from chukta.policy import PolicyEngine, load_policy

    policy = load_policy()
    engine = PolicyEngine(policy)
    people = build_population(30, seed=7)
    events = build_corpus(people, START, seed=7)
    by_id = {p.customer_id: p for p in people}

    arms = {}
    for arm in ("control", "chukta"):
        blind = BlindRetryPolicy(seed=7)
        ap = policy_for_arm(policy, "chukta" if arm == "chukta" else "control")
        arms[arm] = [
            {k: v for k, v in vars(
                run_case(by_id[e.customer_id], e, arm, engine, blind, ap, NullAudit())
            ).items() if k != "log"}
            for e in events
        ]
    result = compare({"arms": arms})
    assert result["arms"]["chukta"]["cases"] == 30


def test_the_executor_still_works_in_dry_run_without_credentials(no_credentials):
    """Dry run must never need a key - otherwise the trace and the dashboard
    would both require an account."""
    from datetime import datetime, timezone

    from chukta.execute import Executor, IdempotencyLedger
    from chukta.types import (
        Action, ActionType, Channel, Customer, FailureEvent, MessageClass,
    )

    ex = Executor(dry_run=True, ledger=IdempotencyLedger(no_credentials / "i.jsonl"))
    now = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
    out = ex.execute(
        Action(type=ActionType.PAYMENT_LINK, channel=Channel.SMS,
               message_class=MessageClass.SERVICE),
        FailureEvent("e1", "c1", 89900, now, "customer",
                     "payment_authentication", "incorrect_otp"),
        Customer("c1"), now,
    )
    assert out["status"] == "dry_run"
    assert out["credential"] is None


def test_the_trace_runs_without_credentials(no_credentials, capsys):
    from chukta.trace import main

    assert main() == 0
    assert "CHUKTA decision trace" in capsys.readouterr().out


def test_composition_falls_back_to_templates_without_a_key(no_credentials):
    """No ANTHROPIC_API_KEY either. The model is off the metric path, so its
    absence must change nothing."""
    from chukta.compose import Composer
    from chukta.policy import load_policy

    msg = Composer(load_policy()).compose("simplification", {"amount": "Rs 499"})
    assert msg.source == "template"


def test_only_the_demo_touches_the_network():
    """One file may import the razorpay SDK path. If a second appears, the
    reproducibility claim needs re-checking rather than assuming."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in list(root.glob("sim/*.py")) + list(root.glob("eval/*.py")):
        if "import razorpay" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, f"measurement code imports the SDK: {offenders}"
