"""Core data model.

Every type here is deliberately plain: the audit log is JSONL and the whole
point is that a human can read a decision row without a schema registry.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


class RecoverabilityClass(str, enum.Enum):
    """What kind of failure this is, in terms of what can be done about it."""

    TRANSIENT = "transient"
    FUNDING = "funding"
    INSTRUMENT_INVALID = "instrument_invalid"
    AUTH_DROPOFF = "auth_dropoff"
    CUSTOMER_INTENT = "customer_intent"
    MANDATE = "mandate"
    MERCHANT_CONFIG = "merchant_config"
    UNKNOWN = "unknown"


class ActionType(str, enum.Enum):
    RETRY_CHARGE = "retry_charge"
    PAYMENT_LINK = "payment_link"
    UPDATE_INSTRUMENT = "update_instrument"
    REMANDATE = "remandate"
    MERCHANT_ALERT = "merchant_alert"
    NO_ACTION = "no_action"


class Channel(str, enum.Enum):
    """Outbound channel. Drives which TRAI rules apply."""

    NONE = "none"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    INTERNAL = "internal"  # merchant-facing, no consumer rules apply


class MessageClass(str, enum.Enum):
    """TRAI TCCCPR traffic classification.

    Corrected 30 Aug 2026 against the regulation itself, having previously been
    wrong. TCCCPR defines a *Transactional Message* as one sent "in response to
    a customer initiated transaction **within thirty minutes** of the
    transaction" - OTPs, payment confirmations, balance alerts.

    A recovery notice goes out 24 hours to 8 days after a failed payment, so it
    is not transactional under that definition. It is a **Service Message**:
    still no explicit consent required, still not blockable by DND, but a
    distinct category. Everything Chukta sends is SERVICE.

    The delivery behaviour is the same for both, which is why the error did not
    show up in any test - the gates produced correct outcomes for the wrong
    stated reason. That is worth fixing anyway: a compliance claim that rests on
    a misread definition is not one you want to defend in front of someone who
    has read it.

    The moment a message carries an offer, a discount or a cross-sell it becomes
    PROMOTIONAL, and the narrower window plus DND and opt-out obligations apply.
    """

    NONE = "none"
    SERVICE = "service"
    # Genuinely within 30 minutes of a customer-initiated transaction. Chukta
    # emits none of these today; the value exists so the distinction is
    # representable rather than collapsed.
    TRANSACTIONAL = "transactional"
    PROMOTIONAL = "promotional"  # 140-series


class PaymentType(str, enum.Enum):
    ONE_TIME = "one_time"
    MANDATE = "mandate"


class TerminalState(str, enum.Enum):
    """The five stopping rules. Nothing else ends a case."""

    RECOVERED = "recovered"
    HARD_DECLINE = "hard_decline"
    ATTEMPT_CAP = "attempt_cap"
    OPTED_OUT = "opted_out"
    PROMISE_TO_PAY = "promise_to_pay"


@dataclass
class MandateContext:
    """Fields the RBI E-mandate Framework 2026 gates need to see."""

    afa_completed: bool = False
    pre_debit_notified_at: datetime | None = None
    category: str = "general"  # general | insurance | sip | credit_card_bill
    revoked: bool = False


@dataclass
class Customer:
    customer_id: str
    dnd_registered: bool = False
    opted_out: bool = False
    contacts_received: int = 0
    last_contacted_at: datetime | None = None
    timezone: str = "Asia/Kolkata"
    # Simulation-only ground truth. Never read by the policy engine.
    quadrant: str | None = None


@dataclass
class FailureEvent:
    """One at-risk event.

    `source`, `step` and `reason` mirror Razorpay's own error triplet. We keep
    them raw rather than normalising on ingest so the audit row shows exactly
    what the gateway said.
    """

    event_id: str
    customer_id: str
    amount_paise: int
    occurred_at: datetime
    source: str
    step: str
    reason: str
    code: str = "BAD_REQUEST_ERROR"
    payment_type: PaymentType = PaymentType.ONE_TIME
    mandate: MandateContext | None = None
    attempt_no: int = 0
    currency: str = "INR"

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100.0


@dataclass
class Action:
    """A proposed action. Proposed is not executed - it must clear the gates."""

    type: ActionType
    channel: Channel = Channel.NONE
    message_class: MessageClass = MessageClass.NONE
    message_frame: str | None = None
    scheduled_for: datetime | None = None
    rule_id: str = "unmatched"
    rationale: str = ""
    # Populated by the control policy so off-policy evaluation is possible.
    # See eval/dr.py - without this, DR cannot be computed at all.
    p_action: float | None = None


@dataclass
class GateResult:
    rule_id: str
    passed: bool
    detail: str = ""


@dataclass
class Decision:
    """One row of the audit trail."""

    event_id: str
    customer_id: str
    arm: str
    decided_at: datetime
    klass: RecoverabilityClass
    evidence: dict[str, Any]
    action: Action
    gates: list[GateResult] = field(default_factory=list)
    executed: bool = False
    execution_result: dict[str, Any] | None = None
    terminal_state: TerminalState | None = None

    @property
    def allowed(self) -> bool:
        return all(g.passed for g in self.gates)

    @property
    def blocking_rules(self) -> list[str]:
        return [g.rule_id for g in self.gates if not g.passed]

    def to_row(self) -> dict[str, Any]:
        """Flatten to a JSON-serialisable audit row."""
        row = asdict(self)
        row["decided_at"] = self.decided_at.isoformat()
        row["klass"] = self.klass.value
        row["allowed"] = self.allowed
        row["blocking_rules"] = self.blocking_rules
        act = row["action"]
        act["type"] = self.action.type.value
        act["channel"] = self.action.channel.value
        act["message_class"] = self.action.message_class.value
        if self.action.scheduled_for is not None:
            act["scheduled_for"] = self.action.scheduled_for.isoformat()
        if self.terminal_state is not None:
            row["terminal_state"] = self.terminal_state.value
        return row
