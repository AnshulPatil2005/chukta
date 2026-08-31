"""Pre-execution verification.

Every proposed action passes through here before anything is executed. The
gate layer sits BETWEEN decide and execute, not after - checking a proposed
action is cheaper and safer than inspecting side effects once money has moved.

Two properties matter more than the rules themselves:

  * Every gate runs. We do not short-circuit on the first failure, because the
    audit row should show the complete verdict, not the first objection.
  * A blocked action is logged with its blocking rule ID. Nothing is silently
    dropped - the block log is the evidence that the guardrails fire at all.

Rule IDs are stable and quotable: G-RBI-*, G-TRAI-*, G-OPS-*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .clock import in_window, to_ist
from .types import (
    Action,
    ActionType,
    Channel,
    Customer,
    FailureEvent,
    GateResult,
    MessageClass,
    PaymentType,
    RecoverabilityClass,
)

# Actions that move money or ask an issuer for authorisation.
CHARGE_ACTIONS = frozenset({ActionType.RETRY_CHARGE})
# Actions that reach the customer.
CONTACT_CHANNELS = frozenset({Channel.SMS, Channel.WHATSAPP, Channel.EMAIL})


@dataclass
class CaseState:
    """Per-case bookkeeping the gates read."""

    attempts: int = 0
    contacts: int = 0
    last_contact_at: datetime | None = None
    exposure_rupees: float = 0.0
    promise_to_pay_until: datetime | None = None
    blocked_by: list[str] = field(default_factory=list)


def evaluate(
    action: Action,
    event: FailureEvent,
    customer: Customer,
    klass: RecoverabilityClass,
    state: CaseState,
    policy: dict,
    now: datetime,
) -> list[GateResult]:
    """Run every gate. Returns one GateResult per rule considered."""
    results: list[GateResult] = []
    defaults = policy["defaults"]
    rbi = policy["rbi"]
    trai = policy["trai"]

    is_charge = action.type in CHARGE_ACTIONS
    is_contact = action.channel in CONTACT_CHANNELS

    # --- kill switch ---------------------------------------------------------
    results.append(
        GateResult(
            "G-OPS-00",
            not defaults.get("kill_switch", False),
            "kill switch engaged" if defaults.get("kill_switch") else "",
        )
    )

    # --- RBI E-mandate Framework 2026 ---------------------------------------
    if event.payment_type is PaymentType.MANDATE and is_charge:
        mandate = event.mandate
        limit = rbi["afa_free_limit_rupees"]
        if mandate is not None:
            limit = rbi["afa_free_limit_by_category"].get(mandate.category, limit)

        afa_ok = (mandate is not None and mandate.afa_completed) or (
            event.amount_rupees <= limit
        )
        results.append(
            GateResult(
                "G-RBI-01",
                afa_ok,
                ""
                if afa_ok
                else (
                    f"recurring debit of Rs {event.amount_rupees:,.0f} exceeds the "
                    f"AFA-free limit of Rs {limit:,.0f} and no AFA is on file; "
                    "a silent retry is not permitted"
                ),
            )
        )

        notice_hours = rbi["pre_debit_notice_hours"]
        notified_at = mandate.pre_debit_notified_at if mandate else None
        notice_ok = notified_at is not None and (
            now - notified_at >= timedelta(hours=notice_hours)
        )
        results.append(
            GateResult(
                "G-RBI-02",
                notice_ok,
                ""
                if notice_ok
                else (
                    f"pre-debit notification not served at least {notice_hours}h "
                    "before this attempt"
                ),
            )
        )

        revoked = mandate is not None and mandate.revoked
        results.append(
            GateResult(
                "G-RBI-03",
                not revoked,
                "mandate revoked; authority to debit no longer exists" if revoked else "",
            )
        )

    # --- TRAI TCCCPR --------------------------------------------------------
    if is_contact:
        # One window per traffic class. Defaulting an unknown class to the
        # promotional window is the safe direction to be wrong in.
        window_key = {
            MessageClass.PROMOTIONAL: "promotional_window_ist",
            MessageClass.SERVICE: "service_window_ist",
            MessageClass.TRANSACTIONAL: "transactional_window_ist",
        }.get(action.message_class, "promotional_window_ist")
        window = tuple(trai[window_key])
        when = action.scheduled_for or now
        timing_ok = in_window(when, window)
        results.append(
            GateResult(
                "G-TRAI-01",
                timing_ok,
                ""
                if timing_ok
                else (
                    f"{action.message_class.value} traffic at "
                    f"{to_ist(when).strftime('%H:%M')} IST falls outside the "
                    f"permitted window {window[0]:02d}:00-{window[1]:02d}:00"
                ),
            )
        )

        dnd_applies = (
            customer.dnd_registered
            and action.message_class.value in trai.get("honour_dnd_for", [])
        )
        results.append(
            GateResult(
                "G-TRAI-02",
                not dnd_applies,
                "customer is DND-registered and this is promotional traffic"
                if dnd_applies
                else "",
            )
        )

        results.append(
            GateResult(
                "G-TRAI-03",
                not customer.opted_out,
                "customer has opted out" if customer.opted_out else "",
            )
        )

    # --- never contact a customer about our own bug --------------------------
    if klass is RecoverabilityClass.MERCHANT_CONFIG:
        ok = not is_contact
        results.append(
            GateResult(
                "G-OPS-06",
                ok,
                ""
                if ok
                else "merchant-config failure; the customer did nothing and must not be contacted",
            )
        )

    # --- operational limits --------------------------------------------------
    if is_charge:
        cap = defaults["max_attempts"]
        ok = state.attempts < cap
        results.append(
            GateResult("G-OPS-01", ok, "" if ok else f"attempt cap of {cap} reached")
        )

    if is_contact:
        cooldown = defaults["contact_cooldown_hours"]
        ok = state.last_contact_at is None or (
            now - state.last_contact_at >= timedelta(hours=cooldown)
        )
        results.append(
            GateResult(
                "G-OPS-02",
                ok,
                "" if ok else f"within the {cooldown}h contact cooldown",
            )
        )

        budget = defaults["contact_budget"]
        ok = state.contacts < budget
        results.append(
            GateResult(
                "G-OPS-03",
                ok,
                "" if ok else f"contact budget of {budget} exhausted for this case",
            )
        )

    if is_charge:
        ceiling = defaults["exposure_ceiling_rupees"]
        ok = state.exposure_rupees + event.amount_rupees <= ceiling
        results.append(
            GateResult(
                "G-OPS-04",
                ok,
                "" if ok else f"cumulative exposure would exceed Rs {ceiling:,.0f}",
            )
        )

        threshold = defaults["human_approval_above_rupees"]
        ok = event.amount_rupees <= threshold
        results.append(
            GateResult(
                "G-OPS-05",
                ok,
                ""
                if ok
                else (
                    f"Rs {event.amount_rupees:,.0f} is above the Rs {threshold:,.0f} "
                    "auto-execute threshold; queued for human approval"
                ),
            )
        )

    # --- do not spend goodwill where it does not pay -------------------------
    # The Qini curve says most of the incremental revenue sits in a minority of
    # the file, and that working the rest costs contacts and cancellations for
    # nothing. This is the gate that acts on it.
    #
    # It applies to OUTREACH only. A charge retry costs fees, not goodwill, and
    # cannot cause a cancellation - suppressing retries on low-uplift cases
    # would forfeit revenue to protect against a harm that is not there.
    if is_contact:
        threshold = defaults.get("min_uplift_score")
        if threshold is not None:
            priors = policy.get("class_priors", {})
            score = priors.get(klass.value, 0.2) * event.amount_rupees
            ok = score >= threshold
            results.append(
                GateResult(
                    "G-OPS-08",
                    ok,
                    ""
                    if ok
                    else (
                        f"expected uplift {score:,.0f} is below the {threshold:,.0f} "
                        f"outreach threshold ({klass.value} at Rs "
                        f"{event.amount_rupees:,.0f}); not worth a contact"
                    ),
                )
            )

    # --- promise to pay pauses everything ------------------------------------
    if state.promise_to_pay_until is not None and now < state.promise_to_pay_until:
        results.append(
            GateResult(
                "G-OPS-07",
                False,
                f"customer promised to pay by "
                f"{to_ist(state.promise_to_pay_until).strftime('%d %b')}; clock paused",
            )
        )

    return results


def passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)


def blocking(results: list[GateResult]) -> list[str]:
    return [r.rule_id for r in results if not r.passed]
