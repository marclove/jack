"""Pure reducers: jack's state as a fold over the event log.

Fact reducers hold the latest recorded fact (or None). ``PaymentReducer``
(the interesting one) owns the payment lifecycle; see its docstring.
All reducers are pure — no I/O, clocks, or randomness (rig core rules).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jack.vocabulary import (
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    LocationsRecorded,
    PricingConfigured,
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
    """Payment lifecycle. Stub — real logic lands with the payment tests."""

    slice = "payment"
    initial = PaymentState()

    def reduce(self, state: PaymentState, event: Any) -> PaymentState:
        return state

    def emit(self, slices: Mapping[str, Any]) -> list[Any]:
        return []
