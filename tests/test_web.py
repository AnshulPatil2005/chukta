"""Dashboard tests.

The dashboard exists to make the engine visible, which creates a specific
hazard: the moment it computes anything itself, there are two sources of truth
and they drift the first time one side is edited. A UI that renders a stale
verdict is worse than no UI, because it looks authoritative.

So the tests that matter here are agreement tests. They call the API and the
engine directly with the same input and require identical answers. If someone
later "optimises" the endpoint by inlining a rule, these fail.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="dashboard is optional; see requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

from web.app import app, _event, _now_at_ist_hour, DiagnoseRequest  # noqa: E402
from chukta.gates import CaseState, evaluate  # noqa: E402
from chukta.policy import PolicyEngine, load_policy  # noqa: E402
from chukta.taxonomy import classify  # noqa: E402
from chukta.types import Customer  # noqa: E402

client = TestClient(app)
POLICY = load_policy()
ENGINE = PolicyEngine(POLICY)


def post(**kw) -> dict:
    res = client.post("/api/diagnose", json=kw)
    assert res.status_code == 200, res.text
    return res.json()


# -- agreement with the engine ----------------------------------------------


@pytest.mark.parametrize(
    "reason,source,step",
    [
        ("card_expired", "issuer", "payment_authorization"),
        ("insufficient_funds", "issuer", "payment_authorization"),
        ("incorrect_otp", "customer", "payment_authentication"),
        ("payment_cancelled", "customer", "payment_authorization"),
        ("acquirer_route_unavailable", "gateway", "payment_authorization"),
        ("totally_made_up_slug", "network", "payment_response"),
    ],
)
def test_api_diagnosis_matches_calling_classify_directly(reason, source, step):
    body = dict(reason=reason, source=source, step=step, amount_rupees=1299.0)
    api = post(**body)["diagnosis"]

    req = DiagnoseRequest(**body)
    klass, evidence = classify(_event(req, _now_at_ist_hour(req.ist_hour)))

    assert api["klass"] == klass.value
    assert api["tier"] == evidence["tier"]
    assert api["confidence"] == evidence["confidence"]


def test_api_gate_verdicts_match_calling_evaluate_directly():
    """The whole point of the gate panel is that it is not a mock-up."""
    body = dict(
        reason="insufficient_funds",
        source="bank",
        step="payment_authorization",
        amount_rupees=45000.0,
        payment_type="mandate",
        afa_completed=False,
    )
    api_step = post(**body)["steps"][0]

    req = DiagnoseRequest(**body)
    now = _now_at_ist_hour(req.ist_hour)
    event = _event(req, now)
    klass, _ = classify(event)
    action = ENGINE.decide(event, klass, 0, now)
    gates = evaluate(
        action,
        event,
        Customer(customer_id=event.customer_id),
        klass,
        CaseState(),
        POLICY,
        action.scheduled_for or now,
    )

    assert [g.rule_id for g in gates] == [g["rule_id"] for g in api_step["gates"]]
    assert [g.passed for g in gates] == [g["passed"] for g in api_step["gates"]]


def test_the_mandate_case_blocks_on_three_independent_rules():
    """Nothing short-circuits: the audit row shows the complete verdict, not
    the first objection."""
    out = post(
        reason="insufficient_funds",
        source="bank",
        step="payment_authorization",
        amount_rupees=45000.0,
        payment_type="mandate",
        afa_completed=False,
    )
    blocked = out["steps"][0]["blocked_by"]
    assert set(blocked) == {"G-RBI-01", "G-RBI-02", "G-OPS-05"}


# -- the what-if controls ----------------------------------------------------


def test_attaching_an_offer_reclassifies_the_message():
    """TCCCPR: a bare recovery notice is service traffic; add an offer and it
    becomes promotional, with a narrower window and DND obligations."""
    plain = post(reason="incorrect_otp", source="customer",
                 step="payment_authentication", send_now=True, ist_hour=11)
    offer = post(reason="incorrect_otp", source="customer",
                 step="payment_authentication", send_now=True, ist_hour=11,
                 attach_offer=True)
    assert plain["steps"][0]["message_class"] == "service"
    assert offer["steps"][0]["message_class"] == "promotional"


@pytest.mark.parametrize("hour,blocked", [(11, False), (20, False), (21, True), (23, True)])
def test_promotional_window_closes_at_21_ist(hour, blocked):
    out = post(
        reason="incorrect_otp",
        source="customer",
        step="payment_authentication",
        attach_offer=True,
        send_now=True,
        ist_hour=hour,
    )
    gate = next(g for g in out["steps"][0]["gates"] if g["rule_id"] == "G-TRAI-01")
    assert (not gate["passed"]) is blocked


def test_service_traffic_is_not_bound_by_the_promotional_window():
    """The classification is the sharp part, so it is pinned in both
    directions - a service message at 23:00 IST is permitted."""
    out = post(reason="incorrect_otp", source="customer",
               step="payment_authentication", send_now=True, ist_hour=23)
    gate = next(g for g in out["steps"][0]["gates"] if g["rule_id"] == "G-TRAI-01")
    assert gate["passed"]


def test_dnd_blocks_promotional_but_not_service_traffic():
    common = dict(reason="incorrect_otp", source="customer",
                  step="payment_authentication", send_now=True,
                  ist_hour=11, dnd_registered=True)
    assert "G-TRAI-02" not in post(**common)["steps"][0]["blocked_by"]
    assert "G-TRAI-02" in post(**common, attach_offer=True)["steps"][0]["blocked_by"]


def test_scheduler_and_gate_are_independent_layers():
    """Left alone, the policy schedules contacts inside the permitted window,
    so the gate never has to fire. That is the design working - and it is why
    the demo needs send_now to show the backstop at all."""
    scheduled = post(reason="incorrect_otp", source="customer",
                     step="payment_authentication", attach_offer=True,
                     ist_hour=23, send_now=False)
    gate = next(g for g in scheduled["steps"][0]["gates"] if g["rule_id"] == "G-TRAI-01")
    assert gate["passed"], "the scheduler should have moved this out of quiet hours"


# -- the dashboard executes nothing -----------------------------------------


def test_the_api_never_executes_anything():
    out = post(reason="incorrect_otp", source="customer",
               step="payment_authentication")
    for step in out["steps"]:
        if step["rendered"]:
            assert step["rendered"]["status"] == "dry_run"


def test_policy_endpoint_serves_the_real_file():
    text = client.get("/api/policy").text
    assert "afa_free_limit_rupees" in text
    assert "promotional_window_ist" in text


def test_vocab_is_derived_from_the_engine_not_hardcoded():
    """Adding a slug to taxonomy.py must update the UI without touching JS."""
    from chukta.taxonomy import REASON_RULES

    vocab = client.get("/api/vocab").json()
    assert set(vocab["reasons"]) == set(REASON_RULES)
    assert vocab["rbi"]["afa_free_limit_rupees"] == POLICY["rbi"]["afa_free_limit_rupees"]


# -- the fresh-clone experience ---------------------------------------------
#
# `runs/` is gitignored because it holds outputs. That meant someone who cloned
# the repo and ran `serve.py` saw two of four tabs blank - a bad first
# impression for the exact person the repo is meant to persuade.

def test_results_generates_a_run_when_none_exists(tmp_path, monkeypatch):
    """A single two-arm run takes seconds, so generate rather than show a
    blank tab."""
    monkeypatch.setattr("web.app.RUNS", tmp_path)
    data = client.get("/api/results").json()
    assert data["generated"] is True
    assert data["run"] is not None
    assert data["qini"] is not None


def test_a_missing_sweep_names_the_command_instead_of_running_it(tmp_path, monkeypatch):
    """Twelve seeds plus sensitivity is most of a minute. That does not belong
    in an HTTP handler."""
    monkeypatch.setattr("web.app.RUNS", tmp_path)
    data = client.get("/api/results").json()
    assert data["sweep"] is None
    assert "eval.sweep" in data["how_to_generate"]


def test_the_inspector_works_with_no_run_data_at_all(tmp_path, monkeypatch):
    """The hero tab must never depend on generated artefacts."""
    monkeypatch.setattr("web.app.RUNS", tmp_path)
    out = post(reason="incorrect_otp", source="customer",
               step="payment_authentication")
    assert out["steps"]
    assert out["diagnosis"]["klass"] == "auth_dropoff"


def test_the_dashboard_holds_no_credentials():
    """It is safe to expose: the executor is in dry run and never loaded a key.
    If this ever changes, deploying the dashboard would publish a credential."""
    import web.app as w

    assert w.EXECUTOR.dry_run is True
    assert w.EXECUTOR.credentials is None
