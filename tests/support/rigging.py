"""Engine-level test rigging: jack's reducers over the payment
topology, as a `rig.testing` fold — `booted().then(...)` reads the way
the reducer tests speak."""

from typing import Any

from rig.core import ConnectionDef, KeyedByField, create_engine
from rig.testing import Fold, fold

from jack.reducers import (
    CompletionReducer,
    ContactReducer,
    IssueReducer,
    PaymentReducer,
    PricingReducer,
    TowReducer,
    TripReducer,
)

PAYMENT_DEF = ConnectionDef(
    id="payment",
    handler_id="payment_link",
    command="send_payment_link",
    result="payment_link_sent",
    tracking="single",
)
CHECK_DEF = ConnectionDef(
    id="payment-check",
    handler_id="payment_check",
    command="check_payment",
    result="payment_status_checked",
    tracking=KeyedByField(keyed_by="link_id"),
)


def jack_engine() -> Any:
    return create_engine(
        reducers=[
            PricingReducer(),
            IssueReducer(),
            TowReducer(),
            TripReducer(),
            ContactReducer(),
            PaymentReducer(),
            CompletionReducer(),
        ]
    )


def booted() -> Fold:
    """A fold over a fresh engine with the payment connections folded
    in — what a session's boot turn produces. Chain events with
    ``.then(...)`` and assert on ``.commands(...)`` and ``.state``."""
    return fold(jack_engine(), boot=(PAYMENT_DEF, CHECK_DEF))
