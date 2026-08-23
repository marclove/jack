"""Engine-level test rigging: a pure engine with jack's reducers and the
payment topology folded in, as a session's boot turn would produce it."""

from typing import Any

from rig.core import ConnectionAdded, ConnectionDef, KeyedByField, create_engine

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


def booted(engine: Any) -> Any:
    """Initial state with the payment connections folded in."""
    state = engine.initial_state()
    for definition in (PAYMENT_DEF, CHECK_DEF):
        state = engine.step(state, ConnectionAdded(connection=definition)).state
    return state
