from datetime import datetime, timedelta, timezone

import pytest

from chukta.gates import CaseState, blocking, evaluate
from chukta.policy import load_policy
from chukta.types import (
    Action,
    ActionType,
    Channel,
    Customer,
    FailureEvent,
    MandateContext,
    MessageClass,
    PaymentType,
    RecoverabilityClass as RC,
)

# 12:00 UTC == 17:30 IST, comfortably inside every permitted window.
NOON_IST_SAFE = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
# 18:00 UTC == 23:30 IST, outside the promotional window.
LATE_NIGHT_IST = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def policy():
    return load_policy()


def mandate_event(amount_rupees, *, afa=False, notified_hours_ago=48,
                  category="general", revoked=False):
    notified = (
        NOON_IST_SAFE - timedelta(hours=notified_hours_ago)
        if notified_hours_ago is not None
        else None
    )
    return FailureEvent(
        event_id="e1",
        customer_id="c1",
        amount_paise=int(amount_rupees * 100),
        occurred_at=NOON_IST_SAFE,
        source="issuer",
        step="payment_authorization",
        reason="insufficient_funds",
        payment_type=PaymentType.MANDATE,
        mandate=MandateContext(
            afa_completed=afa,
            pre_debit_notified_at=notified,
            category=category,
            revoked=revoked,
        ),
    )


def charge():
    return Action(type=ActionType.RETRY_CHARGE, channel=Channel.NONE)


def sms(message_class=MessageClass.SERVICE, when=None):
    return Action(
        type=ActionType.PAYMENT_LINK,
        channel=Channel.SMS,
        message_class=message_class,
        scheduled_for=when,
    )


# --- RBI E-mandate Framework 2026 -------------------------------------------

def test_silent_retry_above_afa_free_limit_is_blocked(policy):
    ev = mandate_event(20000, afa=False)
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-01" in blocking(res)


def test_retry_below_afa_free_limit_is_allowed(policy):
    ev = mandate_event(1499, afa=False)
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-01" not in blocking(res)


def test_category_ceiling_lifts_the_limit_for_sips(policy):
    """RBI allows a higher no-AFA ceiling for insurance premiums, SIPs and
    credit-card bills."""
    ev = mandate_event(50000, afa=False, category="sip")
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-01" not in blocking(res)


def test_completed_afa_permits_a_large_debit(policy):
    ev = mandate_event(50000, afa=True)
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-01" not in blocking(res)


def test_missing_pre_debit_notice_blocks_the_retry(policy):
    ev = mandate_event(500, notified_hours_ago=None)
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-02" in blocking(res)


def test_pre_debit_notice_served_too_recently_blocks_the_retry(policy):
    """Served, but only 6 hours ago. The framework requires 24."""
    ev = mandate_event(500, notified_hours_ago=6)
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-02" in blocking(res)


def test_revoked_mandate_blocks_any_charge(policy):
    ev = mandate_event(500, revoked=True)
    res = evaluate(charge(), ev, Customer("c1"), RC.MANDATE, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-RBI-03" in blocking(res)


# --- TRAI TCCCPR -------------------------------------------------------------

def test_promotional_sms_at_2330_ist_is_blocked(policy):
    ev = mandate_event(500)
    res = evaluate(
        sms(MessageClass.PROMOTIONAL, when=LATE_NIGHT_IST), ev, Customer("c1"),
        RC.FUNDING, CaseState(), policy, LATE_NIGHT_IST,
    )
    assert "G-TRAI-01" in blocking(res)


def test_service_sms_at_2330_ist_is_permitted(policy):
    """The classification is the whole point: a bare recovery notice is service
    traffic and is not time-boxed. Attach an offer and the previous test
    applies instead."""
    ev = mandate_event(500)
    res = evaluate(
        sms(MessageClass.TRANSACTIONAL, when=LATE_NIGHT_IST), ev, Customer("c1"),
        RC.FUNDING, CaseState(), policy, LATE_NIGHT_IST,
    )
    assert "G-TRAI-01" not in blocking(res)


def test_dnd_blocks_promotional_but_not_service(policy):
    ev = mandate_event(500)
    cust = Customer("c1", dnd_registered=True)
    promo = evaluate(sms(MessageClass.PROMOTIONAL, when=NOON_IST_SAFE), ev, cust,
                     RC.FUNDING, CaseState(), policy, NOON_IST_SAFE)
    txn = evaluate(sms(MessageClass.TRANSACTIONAL, when=NOON_IST_SAFE), ev, cust,
                   RC.FUNDING, CaseState(), policy, NOON_IST_SAFE)
    assert "G-TRAI-02" in blocking(promo)
    assert "G-TRAI-02" not in blocking(txn)


def test_opted_out_customer_receives_nothing(policy):
    ev = mandate_event(500)
    cust = Customer("c1", opted_out=True)
    res = evaluate(sms(when=NOON_IST_SAFE), ev, cust, RC.FUNDING, CaseState(),
                   policy, NOON_IST_SAFE)
    assert "G-TRAI-03" in blocking(res)


# --- operational -------------------------------------------------------------

def test_merchant_config_failure_never_reaches_the_customer(policy):
    ev = mandate_event(500)
    res = evaluate(sms(when=NOON_IST_SAFE), ev, Customer("c1"),
                   RC.MERCHANT_CONFIG, CaseState(), policy, NOON_IST_SAFE)
    assert "G-OPS-06" in blocking(res)


def test_contact_budget_exhaustion_blocks_further_contact(policy):
    ev = mandate_event(500)
    budget = policy["defaults"]["contact_budget"]
    state = CaseState(contacts=budget)
    res = evaluate(sms(when=NOON_IST_SAFE), ev, Customer("c1"), RC.FUNDING,
                   state, policy, NOON_IST_SAFE)
    assert "G-OPS-03" in blocking(res)


def test_large_amount_is_held_for_human_approval(policy):
    ev = mandate_event(90000, afa=True, category="sip")
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert "G-OPS-05" in blocking(res)


def test_promise_to_pay_pauses_the_case(policy):
    ev = mandate_event(500)
    state = CaseState(promise_to_pay_until=NOON_IST_SAFE + timedelta(days=3))
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, state, policy,
                   NOON_IST_SAFE)
    assert "G-OPS-07" in blocking(res)


def test_every_gate_runs_even_after_one_fails(policy):
    """The audit row shows the complete verdict, not the first objection."""
    ev = mandate_event(90000, afa=False, notified_hours_ago=None)
    res = evaluate(charge(), ev, Customer("c1"), RC.FUNDING, CaseState(), policy,
                   NOON_IST_SAFE)
    assert {"G-RBI-01", "G-RBI-02", "G-OPS-05"} <= set(blocking(res))


# --- G-OPS-08: do not spend goodwill where it does not pay ------------------
#
# This gate IS the project contribution, so it gets pinned hard. Before it
# existed, `eval/compare_systems.py` showed the Chukta arm producing results
# byte-identical to an unbounded reason-aware baseline: the differentiator was
# analysed and never built. These tests exist so that cannot recur silently.

def plain_event(amount_rupees, reason="incorrect_otp"):
    return FailureEvent(
        event_id="e1",
        customer_id="c1",
        amount_paise=int(amount_rupees * 100),
        occurred_at=NOON_IST_SAFE,
        source="customer",
        step="payment_authentication",
        reason=reason,
    )


def test_low_uplift_case_is_not_contacted(policy):
    """customer_intent has a prior of 0.05, so even a large amount scores low -
    the customer said no, and asking again is how you earn a complaint."""
    ev = plain_event(2000, reason="payment_cancelled")
    res = evaluate(sms(), ev, Customer("c1"), RC.CUSTOMER_INTENT, CaseState(),
                   policy, NOON_IST_SAFE)
    assert "G-OPS-08" in blocking(res)


def test_high_uplift_case_is_contacted(policy):
    """auth_dropoff at a decent amount is the highest-uplift segment there is."""
    ev = plain_event(5000)
    res = evaluate(sms(), ev, Customer("c1"), RC.AUTH_DROPOFF, CaseState(),
                   policy, NOON_IST_SAFE)
    assert "G-OPS-08" not in blocking(res)


def test_the_threshold_scales_with_amount_not_just_class(policy):
    """Same class, different money. A Rs 50 auth dropout is not worth an SMS."""
    small = evaluate(sms(), plain_event(50), Customer("c1"), RC.AUTH_DROPOFF,
                     CaseState(), policy, NOON_IST_SAFE)
    large = evaluate(sms(), plain_event(5000), Customer("c1"), RC.AUTH_DROPOFF,
                     CaseState(), policy, NOON_IST_SAFE)
    assert "G-OPS-08" in blocking(small)
    assert "G-OPS-08" not in blocking(large)


def test_charge_retries_are_never_blocked_by_the_uplift_threshold(policy):
    """A retry costs fees, not goodwill, and cannot cause a cancellation.
    Suppressing retries on low-uplift cases would forfeit revenue to prevent a
    harm that is not there."""
    res = evaluate(charge(), plain_event(50), Customer("c1"), RC.FUNDING,
                   CaseState(), policy, NOON_IST_SAFE)
    assert "G-OPS-08" not in [g.rule_id for g in res]


def test_the_gate_is_actually_wired_into_the_shipped_policy(policy):
    """The previous bounded-contact idea (`contact_budget: 2`) silently never
    bound. Assert the threshold is present and non-trivial, so a policy edit
    that removes it fails here rather than in a benchmark nobody reran."""
    assert policy["defaults"].get("min_uplift_score", 0) > 0
    assert set(policy["class_priors"]) == {c.value for c in RC}


# --- G-OPS-00: the kill switch ----------------------------------------------
#
# The emergency stop had no test at all until 30 Aug. An untested kill switch
# is worse than none: you believe you can stop the agent and find out otherwise
# during the incident you needed it for.

def test_kill_switch_blocks_a_charge(policy):
    policy["defaults"]["kill_switch"] = True
    res = evaluate(charge(), mandate_event(500), Customer("c1"), RC.FUNDING,
                   CaseState(), policy, NOON_IST_SAFE)
    assert "G-OPS-00" in blocking(res)


def test_kill_switch_blocks_outreach(policy):
    policy["defaults"]["kill_switch"] = True
    ev = plain_event(5000)
    res = evaluate(sms(), ev, Customer("c1"), RC.AUTH_DROPOFF, CaseState(),
                   policy, NOON_IST_SAFE)
    assert "G-OPS-00" in blocking(res)


def test_kill_switch_blocks_every_action_type(policy):
    """It is the emergency stop. Nothing gets through it, including actions
    that touch no customer and move no money."""
    policy["defaults"]["kill_switch"] = True
    ev = plain_event(5000)
    for action in (
        charge(),
        sms(),
        Action(type=ActionType.MERCHANT_ALERT, channel=Channel.INTERNAL),
        Action(type=ActionType.NO_ACTION, channel=Channel.NONE),
        Action(type=ActionType.REMANDATE, channel=Channel.NONE),
    ):
        res = evaluate(action, ev, Customer("c1"), RC.AUTH_DROPOFF, CaseState(),
                       policy, NOON_IST_SAFE)
        assert "G-OPS-00" in blocking(res), action.type


def test_kill_switch_is_off_by_default(policy):
    """It has to be, or nothing runs. Pinned so a typo in policy.yaml that
    leaves it engaged fails here rather than silently halting recovery."""
    assert policy["defaults"]["kill_switch"] is False
    res = evaluate(sms(), plain_event(5000), Customer("c1"), RC.AUTH_DROPOFF,
                   CaseState(), policy, NOON_IST_SAFE)
    assert "G-OPS-00" not in blocking(res)


def test_kill_switch_is_evaluated_first(policy):
    """It should be the first rule in the audit row - a reader scanning a
    blocked decision should see the emergency stop before anything else."""
    policy["defaults"]["kill_switch"] = True
    res = evaluate(sms(), plain_event(5000), Customer("c1"), RC.AUTH_DROPOFF,
                   CaseState(), policy, NOON_IST_SAFE)
    assert res[0].rule_id == "G-OPS-00"


def test_a_recovery_notice_is_service_not_transactional(policy):
    """TCCCPR defines a transactional message as one sent within THIRTY MINUTES
    of a customer-initiated transaction - an OTP, a payment confirmation. A
    recovery notice goes out 24 hours to 8 days later, so it is a service
    message.

    This project shipped it labelled `transactional` until 30 Aug, when the
    regulation was finally read rather than the summaries of it. The delivery
    behaviour was already correct, which is exactly why no test caught it: the
    gates produced the right outcome for the wrong stated reason.
    """
    for name, cfg in policy["classes"].items():
        for i, step in enumerate(cfg.get("steps", [])):
            mc = step.get("message_class")
            if mc is not None:
                assert mc != "transactional", (
                    f"{name}.steps[{i}] is labelled transactional; nothing Chukta "
                    "sends lands inside the 30-minute window"
                )


def test_service_and_promotional_have_separate_windows(policy):
    """A single shared window would make the distinction cosmetic."""
    assert policy["trai"]["service_window_ist"] != policy["trai"]["promotional_window_ist"]
    assert "service" not in policy["trai"]["honour_dnd_for"]
