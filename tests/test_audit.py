"""Audit log tests.

The pitch is that every decision is traceable. That claim is worth exactly as
much as the log's resistance to being edited afterwards, which is why the chain
exists and why it is tested against actual tampering rather than just
round-tripped.

What is deliberately NOT claimed: this is a hash chain, not a signature. It
detects piecewise edits; it does not stop someone who can rewrite the whole
file from recomputing it. `test_a_full_rewrite_is_not_detectable` pins that
limitation so nobody reads more into the feature than it delivers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from chukta.audit import GENESIS, AuditLog, read, verify
from chukta.types import (
    Action,
    ActionType,
    Channel,
    Decision,
    GateResult,
    RecoverabilityClass as RC,
)

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl", "run_test")


def a_decision(event_id: str = "e1", passed: bool = True) -> Decision:
    return Decision(
        event_id=event_id,
        customer_id="c1",
        arm="chukta",
        decided_at=NOW,
        klass=RC.AUTH_DROPOFF,
        evidence={"tier": "reason", "confidence": "high", "matched": "incorrect_otp"},
        action=Action(type=ActionType.PAYMENT_LINK, channel=Channel.SMS),
        gates=[GateResult("G-OPS-00", True), GateResult("G-TRAI-01", passed, "" if passed else "quiet hours")],
    )


# -- the chain ---------------------------------------------------------------


def test_a_clean_log_verifies(log):
    log.note("run_start", n=3)
    log.write(a_decision("e1"))
    log.write(a_decision("e2"))
    log.note("run_end", decisions=2)
    ok, problem = verify(log.path)
    assert ok and problem is None


def test_first_row_links_to_genesis(log):
    log.note("run_start")
    assert next(read(log.path))["prev"] == GENESIS


def test_each_row_links_to_the_one_before(log):
    log.note("a")
    log.write(a_decision())
    log.note("b")
    rows = list(read(log.path))
    for prev_row, row in zip(rows, rows[1:]):
        assert row["prev"] == prev_row["digest"]


def test_editing_a_row_is_detected(log):
    """The whole point. Someone quietly changes a decision after the fact."""
    log.note("run_start")
    log.write(a_decision("e1", passed=False))
    log.note("run_end")

    lines = log.path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["gates"][1]["passed"] = True          # rewrite a blocked gate to passed
    lines[1] = json.dumps(row)
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, problem = verify(log.path)
    assert not ok
    assert "does not match its digest" in problem


def test_deleting_a_row_is_detected(log):
    log.note("a")
    log.write(a_decision())
    log.note("c")
    lines = log.path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, problem = verify(log.path)
    assert not ok
    assert "breaks the chain" in problem


def test_reordering_rows_is_detected(log):
    log.note("a")
    log.note("b")
    log.note("c")
    lines = log.path.read_text(encoding="utf-8").splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert verify(log.path)[0] is False


def test_appending_a_forged_row_is_detected(log):
    """An attacker who can write to the file but cannot recompute the chain."""
    log.note("a")
    forged = {"run_id": "run_test", "seq": 1, "kind": "note",
              "prev": "0" * 64, "digest": "f" * 64}
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(forged) + "\n")
    assert verify(log.path)[0] is False


def test_a_full_rewrite_is_not_detectable(log):
    """Stating the limitation rather than overselling the feature.

    Anyone who can rewrite the entire file can recompute a valid chain. Making
    that detectable needs an external anchor - a signature, or publishing the
    head digest somewhere the writer does not control.
    """
    log.note("original")
    rewritten = AuditLog(log.path.with_name("rewritten.jsonl"), "run_test")
    rewritten.note("fabricated")
    assert verify(rewritten.path)[0] is True


def test_head_advances_and_is_the_last_digest(log):
    assert log.head == GENESIS
    log.note("a")
    first = log.head
    log.note("b")
    assert log.head != first
    assert log.head == list(read(log.path))[-1]["digest"]


def test_digest_does_not_depend_on_key_order(tmp_path):
    """Sorted keys, so two logically identical rows hash the same regardless of
    how the dict happened to be built."""
    a = AuditLog(tmp_path / "a.jsonl", "r")
    b = AuditLog(tmp_path / "b.jsonl", "r")
    a.note("x", alpha=1, beta=2)
    b.note("x", beta=2, alpha=1)
    assert list(read(a.path))[0]["digest"] == list(read(b.path))[0]["digest"]


# -- content -----------------------------------------------------------------


def test_a_decision_row_carries_its_evidence_and_gates(log):
    log.write(a_decision("e1", passed=False))
    row = next(read(log.path))
    assert row["event_id"] == "e1"
    assert row["evidence"]["tier"] == "reason"
    assert row["blocking_rules"] == ["G-TRAI-01"]
    assert row["allowed"] is False


def test_nothing_updates_or_deletes_a_row(log):
    """A correction is a new row. If this module ever grows an update method,
    the provenance claim stops being true."""
    assert not any(
        hasattr(AuditLog, name) for name in ("update", "delete", "amend", "edit")
    )


# -- resuming across processes ----------------------------------------------
#
# Found by running the live demo twice, not by a test. The first version reset
# the chain head to GENESIS on construction, so re-opening an existing log
# produced a row claiming prev=GENESIS in the middle of the file and `verify`
# correctly called it broken. An append-only log that cannot survive a process
# restart is broken for the one job it exists to do.

def test_reopening_a_log_continues_its_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path, "run_a")
    first.note("a")
    first.note("b")
    head_after_first = first.head

    second = AuditLog(path, "run_b")
    assert second.head == head_after_first, "reopened log restarted the chain"
    second.note("c")

    ok, problem = verify(path)
    assert ok, problem


def test_reopening_continues_the_sequence_numbers(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = AuditLog(path, "r")
    a.note("one")
    a.note("two")
    b = AuditLog(path, "r")
    b.note("three")
    seqs = [row["seq"] for row in read(path)]
    assert seqs == [0, 1, 2], f"sequence restarted: {seqs}"


def test_three_separate_processes_produce_one_valid_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    for i in range(3):
        log = AuditLog(path, f"run_{i}")
        log.write(a_decision(f"e{i}"))
    ok, problem = verify(path)
    assert ok, problem
    assert len(list(read(path))) == 3


def test_a_fresh_log_still_starts_at_genesis(tmp_path):
    assert AuditLog(tmp_path / "new.jsonl", "r").head == GENESIS
