"""Every module must be reachable from something a user runs.

This file exists because of a failure that happened twice in this project, in
two different shapes:

  * `contact_budget: 2` was designed, documented and inert - it never bound, so
    the Chukta arm produced results byte-identical to an unbounded baseline.
    Analysed, never built.
  * `chukta/compose.py` and `chukta/replies.py` were built, tested and given
    ADRs, and **nothing imported them**. Built, never wired.

Both passed a full green test suite. Unit tests prove a module works; nothing
proves it is used. These tests close that gap.

They are deliberately structural rather than behavioural - they assert that the
wiring exists, not what it produces. A behavioural test would duplicate the
module's own suite; this one catches the module being quietly orphaned.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that must be reachable from production code, not just from tests.
# The value is what would be lost if it were orphaned.
MUST_BE_WIRED = {
    "compose": "message copy would silently fall back to nothing",
    "replies": "promise-to-pay and opt-out would be unreachable",
    "taxonomy": "nothing would classify failures",
    "gates": "no compliance checking",
    "audit": "no provenance",
    "execute": "no execution path",
    "clock": "IST windows would not be evaluated",
}

ENTRY_POINTS = ("demo_live.py", "serve.py")


def production_sources() -> list[pathlib.Path]:
    """Everything a user can run, excluding tests."""
    files = []
    for pattern in ("chukta/*.py", "sim/*.py", "eval/*.py", "web/*.py", "*.py"):
        files.extend(p for p in ROOT.glob(pattern) if "test" not in p.name)
    return files


@pytest.mark.parametrize("module,why", sorted(MUST_BE_WIRED.items()))
def test_module_is_imported_by_production_code(module, why):
    importers = [
        p.name
        for p in production_sources()
        if p.stem != module
        and (f"from .{module} import" in (t := p.read_text(encoding="utf-8"))
             or f"from chukta.{module} import" in t
             or f"import chukta.{module}" in t)
    ]
    assert importers, (
        f"chukta/{module}.py is orphaned - no production module imports it. "
        f"If it stays that way, {why}."
    )


# The three below RUN the thing and inspect the output. An earlier version
# grepped the source for "Composer" and passed happily with the import deleted,
# because the name still appeared elsewhere in the file. A test that a stale
# string can satisfy is worse than no test: it converts an unnoticed gap into a
# confidently-wrong green tick.


def test_the_trace_actually_prints_a_composed_message(capsys):
    """Run it. If composition is orphaned, no message line appears."""
    from chukta.trace import main

    main()
    out = capsys.readouterr().out
    assert "message (" in out, "the trace printed no composed message"
    # Real copy, not a frame label.
    assert "Hi Priya" in out


def test_the_demo_actually_exercises_reply_handling(capsys, monkeypatch):
    """G-OPS-07 is reachable only through a parsed reply. Run the demo dry and
    require it to show a promise pausing the case."""
    import demo_live

    monkeypatch.setattr("sys.argv", ["demo_live.py"])
    demo_live.main()
    out = capsys.readouterr().out
    assert "INBOUND REPLY" in out
    assert "promise_to_pay" in out
    assert "G-OPS-07" in out, "the promise did not actually block anything"
    assert "G-TRAI-03" in out, "the opt-out did not actually block anything"


def test_the_dashboard_returns_a_composed_message():
    """Call the API and require real copy in the response, not a frame name."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from web.app import app

    res = TestClient(app).post("/api/diagnose", json={
        "reason": "incorrect_otp", "source": "customer",
        "step": "payment_authentication",
    })
    assert res.status_code == 200
    step = res.json()["steps"][0]
    assert step["message"] is not None, "no message on a contact step"
    assert len(step["message"]["text"]) > 20
    assert step["message"]["source"] in ("template", "model",
                                         "template_after_guard_block")


@pytest.mark.parametrize("script", ENTRY_POINTS)
def test_entry_points_accept_help_without_side_effects(script):
    """`serve.py --help` used to start the server instead of printing help,
    because it had no argparse at all. Anything a user might type must be safe."""
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "argparse" in text, f"{script} parses no arguments"
    assert "add_argument" in text


def test_no_module_is_imported_only_by_its_own_tests():
    """A broader sweep than the list above: flag any chukta/ module that appears
    in tests but nowhere in production code."""
    prod = "\n".join(
        p.read_text(encoding="utf-8") for p in production_sources()
    )
    orphans = []
    for path in (ROOT / "chukta").glob("*.py"):
        if path.stem in ("__init__", "types"):
            continue  # types is imported everywhere; __init__ is the package
        if f".{path.stem} import" not in prod and f"chukta.{path.stem}" not in prod:
            orphans.append(path.stem)
    assert not orphans, f"orphaned modules: {orphans}"
