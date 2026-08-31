"""Message composition: the one place a model earns its keep.

Every other decision in Chukta is a rule, deliberately. Writing recovery copy is
the exception - it is genuinely open-ended, and a template that says the same
sentence to a hundred thousand people reads like a template.

Three constraints make a model safe here:

**It cannot invent an action.** By the time this module runs, the class, the
intervention, the channel and the schedule are already decided and already
through the gates. The model receives a frame and a set of facts and returns
prose. It has no tool, no action vocabulary, and nothing it writes can change
what happens.

**Its output is checked by a deterministic guard.** `CoercionGuard` is rules and
regex, not a second model. A model grading a model shares the failure mode you
are trying to catch, and the whole point of the guard is that it holds when the
generator is having a bad day. Copy that fails the guard is never sent - the
template fallback is used instead, and the substitution is written to the audit
row.

**It is off the critical metric path.** Every number in `eval/` comes from runs
that never call it. A model in the measurement loop would make results
irreproducible, which is the one thing this project cannot trade away. When no
credential is present the template composer runs and the pipeline is unchanged.

`sim/response_model.py` scores the *frame*, not the words, so simulated results
are identical either way. That is a limitation, not a feature: the sensitivity
sweep says frames are the load-bearing assumption, and nothing here tests
whether better copy inside a frame actually converts better. It would take live
traffic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

MODEL = "claude-opus-5"

# The guard. Deterministic on purpose - see the module docstring.
#
# These patterns encode the four prohibitions in policy.yaml. They are
# deliberately blunt: a false positive costs one templated message, a false
# negative sends a threat to a customer over a failed Rs 500 renewal.
COERCIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\blegal\s+(action|proceeding|notice|recourse)\b", "threat of legal action"),
    (r"\b(sue|suing|lawsuit|litigat)\w*\b", "threat of legal action"),
    (r"\b(prosecut|criminal|fraud|theft|stole|stealing)\w*\b", "implication of criminality"),
    (r"\bdebt\s+collect\w*\b", "threat of escalation to collections"),
    (r"\b(credit\s+(score|bureau|rating)|cibil)\b", "threat of credit consequences"),
    (r"\b(inform|contact|notify)\w*\s+(your\s+)?(employer|family|relatives|contacts|references)\b",
     "threat of contacting family or employer"),
    (r"\b(shame|shameful|disgrace|irresponsible|dishonest|deadbeat)\b", "shaming language"),
    (r"\b(final\s+warning|last\s+chance|act\s+now\s+or)\b", "manufactured urgency"),
    (r"\b(blacklist\w*|blocked\s+permanently|banned\s+for\s+life)\b",
     "disproportionate threat"),
]

# Frames whose contract forbids consequence language outright. policy.yaml
# carries this as `must_not_include`; it is enforced here.
FRAME_FORBIDDEN = {
    "deliberate_choice": ("threat", "legal_consequence"),
}


@dataclass
class Message:
    """Composed copy, plus how it was produced. Both go in the audit row."""

    text: str
    frame: str
    source: str  # "model" | "template" | "template_after_guard_block"
    guard_findings: list[str] = field(default_factory=list)
    model_text_rejected: str | None = None

    @property
    def was_blocked(self) -> bool:
        return self.source == "template_after_guard_block"


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------


class CoercionGuard:
    """Hard block on coercive copy. Rules and regex, never a model."""

    def __init__(self, patterns: list[tuple[str, str]] | None = None):
        self.patterns = [
            (re.compile(p, re.IGNORECASE), label)
            for p, label in (patterns or COERCIVE_PATTERNS)
        ]

    def check(self, text: str, frame: str | None = None) -> list[str]:
        """Return every reason this copy must not be sent. Empty means clean."""
        findings = [label for rx, label in self.patterns if rx.search(text)]

        # A frame that forbids consequence language is stricter than the
        # baseline, so re-check the softer wording the general patterns allow.
        if frame in FRAME_FORBIDDEN:
            if re.search(r"\b(consequence|penalt|forfeit|lose\s+access\s+permanently)\w*\b",
                         text, re.IGNORECASE):
                findings.append(f"consequence language forbidden by frame '{frame}'")

        # Length is a safety property, not an aesthetic one: an SMS that splits
        # across segments arrives out of order often enough to matter.
        if len(text) > 320:
            findings.append(f"exceeds 320 characters ({len(text)})")
        return sorted(set(findings))


# ---------------------------------------------------------------------------
# composers
# ---------------------------------------------------------------------------


TEMPLATES: dict[str, str] = {
    "social_norm": (
        "Hi {name}, most customers on your plan have already settled this "
        "month. Your {amount} payment for {merchant} did not go through. "
        "You can complete it here: {link}"
    ),
    "deliberate_choice": (
        "Hi {name}, your {amount} payment to {merchant} did not go through. "
        "If you would like to continue, you can complete it here: {link}. "
        "If not, no action is needed."
    ),
    "simplification": (
        "Hi {name}, one step to fix your {merchant} payment of {amount}: "
        "{link}"
    ),
    "specific_action": (
        "Hi {name}, your {amount} payment to {merchant} needs updating. "
        "Tap {link} before {deadline} to keep things running."
    ),
    "loss_framing": (
        "Hi {name}, your {merchant} {service} stops on {deadline} because a "
        "{amount} payment did not go through. Restore it here: {link}"
    ),
}

FALLBACK_TEMPLATE = (
    "Hi {name}, your {amount} payment to {merchant} did not go through. "
    "You can complete it here: {link}"
)


class TemplateComposer:
    """Deterministic composition. No credential, no network, no variance.

    This is the fallback whenever the model is unavailable or its output is
    blocked, which means the system degrades to *working* rather than to
    *silent*. It is also what runs in every simulation.
    """

    def compose(self, frame: str | None, facts: dict[str, Any]) -> str:
        template = TEMPLATES.get(frame or "", FALLBACK_TEMPLATE)
        safe = {
            "name": facts.get("name", "there"),
            "amount": facts.get("amount", "your payment"),
            "merchant": facts.get("merchant", "your subscription"),
            "link": facts.get("link", "[payment link]"),
            "deadline": facts.get("deadline", "soon"),
            "service": facts.get("service", "subscription"),
        }
        try:
            return template.format(**safe)
        except KeyError:
            return FALLBACK_TEMPLATE.format(**safe)


SYSTEM_PROMPT = """You write short payment-recovery messages for Indian \
subscription merchants.

You are writing ONE SMS. The decision to send it has already been made and \
validated; your only job is the wording.

Hard rules, no exceptions:
- Under 300 characters.
- Never threaten legal action, collections, credit-score consequences, or \
contacting anyone's family or employer.
- Never imply the customer is dishonest, criminal, irresponsible, or \
shameful. A failed payment is usually a bank problem, not a character problem.
- Never manufacture urgency that is not real. No "final warning", no "last \
chance" unless a real stated deadline exists.
- Write for someone who is busy and mildly annoyed, not someone who has done \
something wrong.
- Plain English, no marketing voice, no exclamation marks.
- Output the message text only. No preamble, no quotes, no explanation."""


class ModelComposer:
    """Claude writes the copy inside a fixed behavioural frame.

    The frame comes from `policy.yaml` and carries its own `must_include`
    contract; the model fills it in. It never chooses the frame, the channel,
    the timing or the action.
    """

    def __init__(self, client: Any = None, model: str = MODEL):
        self.model = model
        self._client = client
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()

    def compose(self, frame: str | None, facts: dict[str, Any], frame_spec: dict) -> str:
        basis = frame_spec.get("basis", "")
        must = ", ".join(frame_spec.get("must_include", [])) or "amount, action link"
        must_not = ", ".join(frame_spec.get("must_not_include", []))

        prompt = [
            f"Frame: {frame or 'plain'}",
            f"What the frame does: {basis}" if basis else "",
            f"Must include: {must}",
            f"Must NOT include: {must_not}" if must_not else "",
            "",
            "Facts:",
            f"  customer name: {facts.get('name', 'unknown')}",
            f"  amount: {facts.get('amount')}",
            f"  merchant: {facts.get('merchant')}",
            f"  what failed: {facts.get('reason_plain', 'the payment did not go through')}",
            f"  payment link: {facts.get('link', '[link]')}",
            f"  deadline: {facts.get('deadline', 'none')}",
        ]

        response = self._client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": "\n".join(p for p in prompt if p)}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to write this message")
        return "".join(
            b.text for b in response.content if b.type == "text"
        ).strip().strip('"')


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def model_available() -> bool:
    """A credential is present. Absence is normal and not an error."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class Composer:
    """Model when available, template otherwise, guard over both.

    The guard runs on template output too. Templates are checked in today and
    could be edited tomorrow, and a guard that only inspects model output would
    not notice.
    """

    def __init__(
        self,
        policy: dict,
        model_composer: ModelComposer | None = None,
        template_composer: TemplateComposer | None = None,
        guard: CoercionGuard | None = None,
        use_model: bool | None = None,
    ):
        self.policy = policy
        self.templates = template_composer or TemplateComposer()
        self.guard = guard or CoercionGuard()
        self.use_model = model_available() if use_model is None else use_model
        self._model = model_composer
        if self.use_model and self._model is None:
            self._model = ModelComposer()

    def compose(self, frame: str | None, facts: dict[str, Any]) -> Message:
        fallback = self.templates.compose(frame, facts)

        if not self.use_model or self._model is None:
            findings = self.guard.check(fallback, frame)
            if findings:
                # The shipped templates are guard-clean; reaching here means a
                # template was edited into something unsendable. Fail loudly -
                # there is no safer text left to fall back to.
                raise ValueError(
                    f"template for frame '{frame}' fails the coercion guard: "
                    f"{'; '.join(findings)}"
                )
            return Message(text=fallback, frame=frame or "plain", source="template")

        spec = self.policy.get("frames", {}).get(frame or "", {})
        try:
            text = self._model.compose(frame, facts, spec)
        except Exception:
            # A model outage must not stop recovery. Degrade, do not fail.
            return Message(
                text=fallback, frame=frame or "plain", source="template"
            )

        findings = self.guard.check(text, frame)
        if findings:
            return Message(
                text=fallback,
                frame=frame or "plain",
                source="template_after_guard_block",
                guard_findings=findings,
                model_text_rejected=text,
            )
        return Message(text=text, frame=frame or "plain", source="model")
