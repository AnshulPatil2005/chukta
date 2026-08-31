"""Reply parsing tests.

Two of these matter more than the rest: opt-out must never be missed (it is a
regulatory obligation), and a promise must actually reach `G-OPS-07`. Before
this module existed, `promise_to_pay_until` was never set anywhere, so one of
the five documented stopping rules could not fire.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chukta.clock import to_ist
from chukta.gates import CaseState, blocking, evaluate
from chukta.policy import load_policy
from chukta.replies import MAX_PROMISE_DAYS, ReplyIntent, apply_to_case, parse
from chukta.types import (
    Action,
    ActionType,
    Channel,
    Customer,
    FailureEvent,
    MessageClass,
    RecoverabilityClass as RC,
)

# Sat 29 Aug 2026, 12:00 UTC == 17:30 IST
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def policy():
    return load_policy()


# -- opt out -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "STOP",
        "stop",
        "please stop messaging me",
        "unsubscribe",
        "opt out",
        "OPTOUT",
        "do not contact me again",
        "remove me from your list",
        "no more messages please",
        "leave me alone",
    ],
)
def test_opt_out_is_recognised_generously(text):
    """Someone typing "stop msgs" is opting out. A parser that demands the
    exact keyword is choosing a technicality over a clear instruction."""
    assert parse(text, NOW).intent is ReplyIntent.OPT_OUT


def test_opt_out_beats_every_other_intent():
    """"Stop, I already paid" is still an opt-out. Getting this precedence
    wrong is a regulatory problem, not a UX one."""
    assert parse("stop, I already paid this", NOW).intent is ReplyIntent.OPT_OUT
    assert parse("STOP - will pay tomorrow", NOW).intent is ReplyIntent.OPT_OUT


def test_opt_out_blocks_all_further_contact(policy):
    customer = Customer("c1")
    apply_to_case(parse("STOP", NOW), CaseState(), customer)
    assert customer.opted_out

    event = FailureEvent("e1", "c1", 250000, NOW, "customer",
                         "payment_authentication", "incorrect_otp")
    action = Action(type=ActionType.PAYMENT_LINK, channel=Channel.SMS,
                    message_class=MessageClass.TRANSACTIONAL, scheduled_for=NOW)
    res = evaluate(action, event, customer, RC.AUTH_DROPOFF, CaseState(),
                   policy, NOW)
    assert "G-TRAI-03" in blocking(res)


# -- promise to pay ----------------------------------------------------------


@pytest.mark.parametrize(
    "text,days_ahead",
    [
        ("I will pay tomorrow", 1),
        ("will pay today", 0),
        ("I'll pay in 3 days", 3),
        ("can pay after 1 week", 7),
        ("paying next week", 7),
    ],
)
def test_relative_dates(text, days_ahead):
    r = parse(text, NOW)
    assert r.intent is ReplyIntent.PROMISE_TO_PAY
    assert r.pay_by is not None
    expected = to_ist(NOW + timedelta(days=days_ahead)).date()
    assert to_ist(r.pay_by).date() == expected


def test_day_of_month():
    r = parse("will pay on the 5th", NOW)
    assert r.intent is ReplyIntent.PROMISE_TO_PAY
    assert to_ist(r.pay_by).day == 5
    assert r.pay_by > NOW


def test_named_month():
    r = parse("I will pay by 3 Sept", NOW)
    assert r.intent is ReplyIntent.PROMISE_TO_PAY
    assert (to_ist(r.pay_by).month, to_ist(r.pay_by).day) == (9, 3)


def test_salary_language():
    """Common, specific, and actionable - "after my salary" is a real date on
    this population."""
    r = parse("will pay after my salary", NOW)
    assert r.intent is ReplyIntent.PROMISE_TO_PAY
    assert r.pay_by is not None


def test_promise_runs_to_end_of_day():
    """Resuming contact at 09:00 on the day someone said they would pay is
    technically correct and generates complaints."""
    r = parse("will pay tomorrow", NOW)
    assert to_ist(r.pay_by).hour == 23


def test_an_absurd_promise_is_capped():
    r = parse("I will pay in 40 weeks", NOW)
    assert r.pay_by is not None
    assert (to_ist(r.pay_by) - to_ist(NOW)).days <= MAX_PROMISE_DAYS


def test_promise_without_a_date_does_not_pause_the_case():
    """Honouring an open-ended "I'll pay" would stop the case forever."""
    r = parse("I will pay", NOW)
    assert r.intent is ReplyIntent.PROMISE_TO_PAY
    assert r.pay_by is None
    state = CaseState()
    apply_to_case(r, state, Customer("c1"))
    assert state.promise_to_pay_until is None


def test_a_bare_date_is_not_a_promise():
    """No stated intent to pay, so nothing should change."""
    assert parse("tomorrow", NOW).intent is ReplyIntent.UNKNOWN
    assert parse("5 Sept", NOW).intent is ReplyIntent.UNKNOWN


# -- the gate this module exists to make reachable ---------------------------


def test_a_promise_actually_pauses_the_case(policy):
    """The load-bearing test. G-OPS-07 and TerminalState.PROMISE_TO_PAY were
    both unreachable before this module existed."""
    state = CaseState()
    note = apply_to_case(parse("I will pay on the 5th", NOW), state, Customer("c1"))
    assert state.promise_to_pay_until is not None
    assert "paused" in note

    event = FailureEvent("e1", "c1", 250000, NOW, "customer",
                         "payment_authentication", "incorrect_otp")
    action = Action(type=ActionType.PAYMENT_LINK, channel=Channel.SMS,
                    message_class=MessageClass.TRANSACTIONAL, scheduled_for=NOW)
    res = evaluate(action, event, Customer("c1"), RC.AUTH_DROPOFF, state,
                   policy, NOW)
    assert "G-OPS-07" in blocking(res)


def test_the_pause_expires(policy):
    state = CaseState()
    apply_to_case(parse("will pay tomorrow", NOW), state, Customer("c1"))
    after = state.promise_to_pay_until + timedelta(hours=1)

    event = FailureEvent("e1", "c1", 250000, NOW, "customer",
                         "payment_authentication", "incorrect_otp")
    action = Action(type=ActionType.PAYMENT_LINK, channel=Channel.SMS,
                    message_class=MessageClass.TRANSACTIONAL, scheduled_for=after)
    res = evaluate(action, event, Customer("c1"), RC.AUTH_DROPOFF, state,
                   policy, after)
    assert "G-OPS-07" not in blocking(res)


# -- other intents -----------------------------------------------------------


def test_dispute_stops_contact_and_routes_to_a_human():
    customer = Customer("c1")
    note = apply_to_case(parse("I did not order this, refund me", NOW),
                         CaseState(), customer)
    assert customer.opted_out
    assert "human" in note


def test_already_paid_is_recognised():
    assert parse("I have already paid this", NOW).intent is ReplyIntent.ALREADY_PAID


def test_unknown_text_changes_nothing():
    """Total function: gibberish must not stop a case."""
    state, customer = CaseState(), Customer("c1")
    for text in ["asdf", "", "   ", "what is this about?", "ok"]:
        assert apply_to_case(parse(text, NOW), state, customer) is None
    assert state.promise_to_pay_until is None
    assert not customer.opted_out
