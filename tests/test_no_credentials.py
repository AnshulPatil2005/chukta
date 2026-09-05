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


# -- .env.example must never hold a real credential --------------------------
#
# It did. A live key id AND its secret were committed there on 29 Aug and sat
# in the repository until 5 Sept, going public on the 4th. I had verified that
# .env was gitignored and never looked at the file next to it, which IS
# tracked. The key is rotated; this test is so the same mistake cannot be made
# quietly a second time.

import re

REAL_KEY = re.compile(r"rzp_(test|live)_[A-Za-z0-9]{10,}")


def looks_real(token: str) -> bool:
    """A real Razorpay key id has digits and mixed case. Placeholders
    (rzp_test_xxxxxxxxxxxx) and test fixtures (rzp_live_ABCDEFGHIJKL) have
    neither, and flagging those would make the guard noise people switch off."""
    body = token.split("_", 2)[2]
    return any(c.isdigit() for c in body) and any(c.islower() for c in body)


def _example_path():
    import pathlib

    return pathlib.Path(__file__).resolve().parent.parent / ".env.example"


def test_the_example_file_holds_no_real_key_id():
    text = _example_path().read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("RAZORPAY_KEY_ID="):
            value = line.split("=", 1)[1].strip()
            assert not looks_real(value), (
                f"a real-looking key id is committed in .env.example: {value[:14]}..."
            )
            assert "xxxx" in value.lower(), "key id must be an obvious placeholder"


def test_the_example_file_holds_no_real_secret():
    """The secret is the half that actually authenticates."""
    for line in _example_path().read_text(encoding="utf-8").splitlines():
        if line.startswith(("RAZORPAY_KEY_SECRET=", "RAZORPAY_WEBHOOK_SECRET=")):
            value = line.split("=", 1)[1].strip()
            assert "xxxx" in value.lower(), (
                f"{line.split('=')[0]} in .env.example is not a placeholder"
            )


def test_no_live_key_prefix_appears_anywhere_in_tracked_files():
    """Broader sweep. Test fixtures use rzp_live_ABCDEFGHIJKL deliberately to
    exercise the refusal path, so the check is for a plausible REAL key -
    mixed case and digits - rather than the prefix alone."""
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True
    ).stdout.split()

    offenders = []
    for rel in tracked:
        path = root / rel
        if not path.is_file() or path.suffix in {".png", ".jpg", ".ico"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in REAL_KEY.finditer(text):
            if looks_real(m.group(0)):
                offenders.append(f"{rel}: {m.group(0)[:14]}...")
    assert not offenders, f"real-looking keys in tracked files: {offenders}"
