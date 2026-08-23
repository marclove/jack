"""jack's domain vocabulary: the facts and requests of one intake call.

Events are facts folded into state; commands are requests for effects.
Every model's ``type`` tag is derived from its class name by
``FrozenModel``. ``JACK_VOCABULARY`` extends rig's built-in unions so
the JSONL log can write and read jack's types — a log built with rig's
defaults cannot reopen a jack call (spec §4).
"""

from typing import Literal

from rig.core import FrozenModel, vocabulary


class PricingConfigured(FrozenModel):
    """The flat tow price, entering as data on the log at session open.

    Reducers are pure and cannot read configuration, so pricing folds
    from this event into the pricing slice (spec §5)."""

    amount_cents: int
    currency: str = "USD"


class IssueRecorded(FrozenModel):
    """The customer's problem, as facts the model extracted."""

    summary: str
    vehicle: str


class TowJudged(FrozenModel):
    """The model's judgment on whether a tow is appropriate."""

    appropriate: bool
    reason: str


class LocationsRecorded(FrozenModel):
    """Pickup and destination for the tow."""

    pickup: str
    dropoff: str


class ContactRecorded(FrozenModel):
    """The customer's mobile number. Also re-arms a halted payment send
    (spec §6): recording contact clears a rejection or send failure."""

    phone: str


class PaymentLinkSent(FrozenModel):
    """Result of ``send_payment_link``. ``status="error"`` reports a
    failed send (handlers report failure, they do not raise); the link
    fields are then None and ``error`` says why."""

    status: Literal["ok", "error"] = "ok"
    link_id: str | None = None
    url: str | None = None
    amount_cents: int | None = None
    error: str | None = None


class PaymentStatusChecked(FrozenModel):
    """Result of ``check_payment``. ``link_id`` mirrors the command's key
    verbatim (the key mirror) — never recomputed, never defaulted.
    ``status="error"`` reports a failed check; the tick that paid for it
    is consumed either way (spec §6)."""

    link_id: str
    status: Literal["pending", "paid", "expired", "error"]
    error: str | None = None


class PollTick(FrozenModel):
    """One unit of polling entitlement. The CLI owns the clock and
    appends ticks; the payment reducer emits one ``check_payment`` per
    unconsumed tick (spec §6)."""


class IntakeCompleted(FrozenModel):
    """The call is over. The CLI exits when this folds."""

    outcome: Literal["paid", "no_tow_needed", "abandoned"]


class SendPaymentLink(FrozenModel):
    """Ask the payment service to create a link and deliver it by SMS.

    ``attempt`` scopes idempotency: a resumed session re-dispatching the
    same attempt gets the same link; a retry after expiry (a higher
    attempt) gets a fresh one (spec §6)."""

    phone: str
    amount_cents: int
    attempt: int


class CheckPayment(FrozenModel):
    """Ask the payment service for a link's status. Keyed by ``link_id``,
    which ``payment_status_checked`` mirrors back."""

    link_id: str


JACK_VOCABULARY = vocabulary(
    events=[
        PricingConfigured,
        IssueRecorded,
        TowJudged,
        LocationsRecorded,
        ContactRecorded,
        PaymentLinkSent,
        PaymentStatusChecked,
        PollTick,
        IntakeCompleted,
    ],
    commands=[SendPaymentLink, CheckPayment],
)
