"""Inbound reply parsing: the other half of a conversation.

`G-OPS-07` pauses a case when the customer promises to pay, and
`TerminalState.PROMISE_TO_PAY` is one of the five stopping rules. Until this
module existed, nothing anywhere set `promise_to_pay_until` - the gate was
unreachable and the stopping rule was decorative. The README claimed five ways
a case can end and only four could happen.

Two intents matter more than the rest:

**Opt out.** Legally binding under TCCCPR and immediate. Deliberately generous
in what it accepts: someone typing "stop msgs" is opting out, and a parser that
insists on the exact keyword is choosing a technicality over a clear intent.

**Promise to pay.** A stated date is the customer doing our job for us. The
right response is to stop contacting them until that date passes - continuing
to chase someone who told you when they would pay is how a recovery programme
generates complaints.

Parsing is deterministic. A model could read these better, but this decides
whether we keep messaging someone, and that belongs in code a reviewer can read
line by line. `parse()` is total: unrecognised text returns `UNKNOWN`, which
changes nothing, rather than a guess that stops a case.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .clock import IST, next_salary_date, to_ist

MAX_PROMISE_DAYS = 21  # beyond this a "promise" is a brush-off, not a plan


class ReplyIntent(str, enum.Enum):
    OPT_OUT = "opt_out"
    PROMISE_TO_PAY = "promise_to_pay"
    ALREADY_PAID = "already_paid"
    DISPUTE = "dispute"
    UNKNOWN = "unknown"


@dataclass
class ParsedReply:
    intent: ReplyIntent
    pay_by: datetime | None = None
    matched: str = ""
    confidence: str = "low"          # high | medium | low
    raw: str = ""


# TCCCPR opt-out. Broad on purpose - see the module docstring.
OPT_OUT = re.compile(
    r"\b(stop|unsubscribe|opt\s*out|do\s*not\s*(contact|message|text|call)|"
    r"remove\s+me|leave\s+me\s+alone|no\s+more\s+(messages|sms))\b",
    re.IGNORECASE,
)

ALREADY_PAID = re.compile(
    r"\b(already\s+paid|have\s+paid|payment\s+(done|made|sent)|paid\s+(it|this|already)|"
    r"settled\s+(it|this))\b",
    re.IGNORECASE,
)

DISPUTE = re.compile(
    r"\b(did\s*n[o']?t\s+(order|buy|subscribe|authorise|authorize)|"
    r"not\s+my\s+(payment|account|card)|cancel\s+(my\s+)?subscription|"
    r"wrong\s+amount|unauthorised|unauthorized|refund)\b",
    re.IGNORECASE,
)

# Intent to pay, without which a date is just a date.
PROMISE = re.compile(
    r"\b(will\s+pay|i[' ]?ll\s+pay|shall\s+pay|can\s+pay|pay\s+(it\s+)?(by|on|after)|"
    r"paying|payment\s+(by|on|after)|clear\s+(it|this)|settle\s+(it|this))\b",
    re.IGNORECASE,
)

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def _end_of_day(when: datetime) -> datetime:
    """Promises are to a day, not an instant. Give the customer the whole day -
    resuming contact at 09:00 on the day they said they would pay is the kind
    of technically-correct behaviour that generates complaints."""
    local = to_ist(when).replace(hour=23, minute=59, second=0, microsecond=0)
    return local


def _parse_when(text: str, now: datetime) -> tuple[datetime | None, str]:
    """Extract a pay-by date. Returns (date, what matched)."""
    local = to_ist(now)
    t = text.lower()

    if re.search(r"\btoday\b", t):
        return _end_of_day(now), "today"
    if re.search(r"\btomorrow\b|\btmrw\b|\bkal\b", t):
        return _end_of_day(now + timedelta(days=1)), "tomorrow"

    # "in 3 days", "after 2 weeks"
    m = re.search(r"\b(?:in|after)\s+(\d{1,2})\s*(day|days|week|weeks)\b", t)
    if m:
        n = int(m.group(1))
        days = n * (7 if m.group(2).startswith("week") else 1)
        return _end_of_day(now + timedelta(days=days)), m.group(0)

    # Salary language is common and specific enough to act on.
    if re.search(r"\b(salary|payday|pay\s*day|month\s*end|1st|first)\b.*\b(credit|come|get|after)\b", t) \
       or re.search(r"\bafter\s+(my\s+)?salary\b", t):
        return _end_of_day(next_salary_date(now)), "after salary"

    # "on the 5th", "by 12th"
    m = re.search(r"\b(?:on|by|before)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            when = _shift_to_day(local, day)
            return _end_of_day(when), m.group(0)

    # "5 sept", "sept 5", "5/9"
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+([a-z]{3,9})\b", t)
    if m and m.group(2)[:3] in MONTHS:
        when = _to_date(local, int(m.group(1)), MONTHS[m.group(2)[:3]])
        if when:
            return _end_of_day(when), m.group(0)
    m = re.search(r"\b([a-z]{3,9})\s+(\d{1,2})\b", t)
    if m and m.group(1)[:3] in MONTHS:
        when = _to_date(local, int(m.group(2)), MONTHS[m.group(1)[:3]])
        if when:
            return _end_of_day(when), m.group(0)

    # "next friday", "on monday"
    m = re.search(r"\b(?:next\s+|on\s+|by\s+)?(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        target = WEEKDAYS[m.group(1)]
        ahead = (target - local.weekday()) % 7 or 7
        return _end_of_day(now + timedelta(days=ahead)), m.group(0)

    if re.search(r"\bnext\s+week\b", t):
        return _end_of_day(now + timedelta(days=7)), "next week"
    return None, ""


def _shift_to_day(local: datetime, day: int) -> datetime:
    """Nearest future occurrence of a day-of-month."""
    try:
        candidate = local.replace(day=day)
    except ValueError:
        return local + timedelta(days=30)
    if candidate <= local:
        month = local.month + 1
        year = local.year + (month > 12)
        month = month - 12 if month > 12 else month
        try:
            candidate = local.replace(year=year, month=month, day=day)
        except ValueError:
            return local + timedelta(days=30)
    return candidate


def _to_date(local: datetime, day: int, month: int) -> datetime | None:
    if not (1 <= day <= 31):
        return None
    year = local.year
    try:
        candidate = local.replace(year=year, month=month, day=day)
    except ValueError:
        return None
    if candidate < local - timedelta(days=1):
        try:
            candidate = candidate.replace(year=year + 1)
        except ValueError:
            return None
    return candidate


def parse(text: str, now: datetime) -> ParsedReply:
    """Classify one inbound reply. Total - unknown input changes nothing."""
    raw = (text or "").strip()
    if not raw:
        return ParsedReply(ReplyIntent.UNKNOWN, raw=raw)

    # Opt-out wins over everything. "Stop, I already paid" is still an opt-out,
    # and getting that precedence wrong is a regulatory problem, not a UX one.
    m = OPT_OUT.search(raw)
    if m:
        return ParsedReply(ReplyIntent.OPT_OUT, matched=m.group(0),
                           confidence="high", raw=raw)

    m = DISPUTE.search(raw)
    if m:
        return ParsedReply(ReplyIntent.DISPUTE, matched=m.group(0),
                           confidence="medium", raw=raw)

    m = ALREADY_PAID.search(raw)
    if m:
        return ParsedReply(ReplyIntent.ALREADY_PAID, matched=m.group(0),
                           confidence="medium", raw=raw)

    m = PROMISE.search(raw)
    if m:
        when, matched_when = _parse_when(raw, now)
        if when is None:
            # Intent without a date. Real, but not actionable as a pause -
            # honouring an open-ended "I'll pay" would stop the case forever.
            return ParsedReply(ReplyIntent.PROMISE_TO_PAY, pay_by=None,
                               matched=m.group(0), confidence="low", raw=raw)
        horizon = to_ist(now) + timedelta(days=MAX_PROMISE_DAYS)
        if when > horizon:
            when = horizon
            matched_when += f" (capped at {MAX_PROMISE_DAYS}d)"
        return ParsedReply(ReplyIntent.PROMISE_TO_PAY, pay_by=when,
                           matched=f"{m.group(0)} / {matched_when}",
                           confidence="high", raw=raw)

    # A bare date with no stated intent to pay is not a promise.
    return ParsedReply(ReplyIntent.UNKNOWN, raw=raw)


def apply_to_case(reply: ParsedReply, state, customer) -> str | None:
    """Fold a parsed reply into case state. Returns what changed, for the audit.

    This is the only place a customer reply can alter what the agent does, so
    the whole surface is four lines and readable in one screen.
    """
    if reply.intent is ReplyIntent.OPT_OUT:
        customer.opted_out = True
        return "customer opted out; all further contact blocked by G-TRAI-03"

    if reply.intent is ReplyIntent.PROMISE_TO_PAY and reply.pay_by is not None:
        state.promise_to_pay_until = reply.pay_by
        return (
            f"customer promised to pay by "
            f"{to_ist(reply.pay_by).strftime('%d %b')}; case paused by G-OPS-07"
        )

    if reply.intent is ReplyIntent.DISPUTE:
        customer.opted_out = True
        return "customer disputes the charge; routed to human, contact stopped"

    return None
