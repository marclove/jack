"""Pure reducers: jack's state as a fold over the event log.

Fact reducers hold the latest recorded fact (or None). ``PaymentReducer``
(the interesting one) owns the payment lifecycle; see its docstring.
All reducers are pure — no I/O, clocks, or randomness (rig core rules).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from rig.core import CommandRejected

from jack.vocabulary import (
    CheckPayment,
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    LocationsRecorded,
    PaymentLinkSent,
    PaymentStatusChecked,
    PollTick,
    PricingConfigured,
    SendPaymentLink,
    TowJudged,
)

MAX_LINK_ATTEMPTS = 2


class _LatestFactReducer:
    """Holds the most recent instance of one event class, or None."""

    slice: str
    event_class: type
    initial: Any = None

    def reduce(self, state: Any, event: Any) -> Any:
        if isinstance(event, self.event_class):
            return event
        return state

    def emit(self, slices: Mapping[str, Any]) -> list[Any]:
        return []


class PricingReducer(_LatestFactReducer):
    slice = "pricing"
    event_class = PricingConfigured


class IssueReducer(_LatestFactReducer):
    slice = "issue"
    event_class = IssueRecorded


class TowReducer(_LatestFactReducer):
    slice = "tow"
    event_class = TowJudged


class TripReducer(_LatestFactReducer):
    slice = "trip"
    event_class = LocationsRecorded


class ContactReducer(_LatestFactReducer):
    slice = "contact"
    event_class = ContactRecorded


class CompletionReducer(_LatestFactReducer):
    slice = "completion"
    event_class = IntakeCompleted


@dataclass(frozen=True)
class PaymentState:
    """The payment slice. ``status`` is None until a link exists, then
    tracks the latest known link status. ``ticks``/``checks`` implement
    tick entitlement (spec §6): each poll_tick entitles one check.
    ``halted`` records why the standing send request was retired
    ("rejected" or "send_failed"); a new contact_recorded clears it."""

    link_id: str | None = None
    url: str | None = None
    status: str | None = None
    attempts: int = 0
    ticks: int = 0
    checks: int = 0
    halted: str | None = None
    halt_reason: str | None = None


class PaymentReducer:
    """The payment lifecycle as a fold (spec §6).

    Standing request contract: ``emit`` asks for ``send_payment_link``
    while state wants one — never only on the event that made it wanted.
    The engine's pending mark stops the repeated ask from dispatching
    twice; this reducer never reads those marks.

    A send is wanted when: pricing is configured, the tow is judged
    appropriate, locations and contact are recorded, the request is not
    halted, fewer than ``MAX_LINK_ATTEMPTS`` links exist, and no live
    link is pending or paid. A guard rejection or a send failure halts
    the request (else it would re-ask forever); a fresh
    ``contact_recorded`` clears the halt. Expiry re-arms the send at the
    next attempt number.

    ``check_payment`` emission is tick-entitled: one check per
    ``poll_tick``, only while a link is pending. Every
    ``payment_status_checked`` — error results included — consumes the
    tick that paid for it, so a turn can never spin.
    """

    slice = "payment"
    initial = PaymentState()

    def reduce(self, state: PaymentState, event: Any) -> PaymentState:
        match event:
            case PaymentLinkSent(status="ok") as sent:
                return PaymentState(
                    link_id=sent.link_id,
                    url=sent.url,
                    status="pending",
                    attempts=state.attempts + 1,
                )
            case PaymentLinkSent(status="error") as sent:
                return replace(state, halted="send_failed", halt_reason=sent.error)
            case PaymentStatusChecked() as checked:
                new_status = state.status
                if checked.status in ("pending", "paid", "expired"):
                    new_status = checked.status
                return replace(state, status=new_status, checks=state.checks + 1)
            case CommandRejected(command="send_payment_link", reason=reason):
                return replace(state, halted="rejected", halt_reason=reason)
            case ContactRecorded():
                if state.halted in ("rejected", "send_failed"):
                    return replace(state, halted=None, halt_reason=None)
                return state
            case PollTick():
                return replace(state, ticks=state.ticks + 1)
            case _:
                return state

    def emit(self, slices: Mapping[str, Any]) -> list[Any]:
        payment: PaymentState = slices["payment"]
        out: list[Any] = []
        pricing = slices["pricing"]
        tow = slices["tow"]
        trip = slices["trip"]
        contact = slices["contact"]
        send_wanted = (
            pricing is not None
            and tow is not None
            and tow.appropriate
            and trip is not None
            and contact is not None
            and payment.halted is None
            and payment.attempts < MAX_LINK_ATTEMPTS
            and payment.status in (None, "expired")
        )
        if send_wanted:
            out.append(
                SendPaymentLink(
                    phone=contact.phone,
                    amount_cents=pricing.amount_cents,
                    attempt=payment.attempts + 1,
                )
            )
        if (
            payment.status == "pending"
            and payment.link_id is not None
            and payment.ticks > payment.checks
        ):
            out.append(CheckPayment(link_id=payment.link_id))
        return out
