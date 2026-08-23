"""Session assembly: reducers, handlers, connections, and the log.

Fresh log: full wiring plus ``SessionInit`` carrying the instructions.
Existing log: the log is the only truth — no connections, no init; the
handler registry alone must satisfy every logged ``handler_id``
(``model``, ``intake``, ``payment_link``, ``payment_check``,
``payment_policy``). Renaming a registry key is therefore a breaking
change for existing call logs.
"""

from pathlib import Path
from typing import Any

from rig.adapters.jsonl import JsonlEventLog
from rig.core import GuardBinding, SessionInit, agent_reducers
from rig.runtime import Connection, Session

from jack import prompts
from jack.guards import PaymentPolicyGuard
from jack.payments import (
    CheckPaymentHandler,
    SendPaymentLinkHandler,
    jack_error_result,
)
from jack.reducers import (
    CompletionReducer,
    ContactReducer,
    IssueReducer,
    PaymentReducer,
    PricingReducer,
    TowReducer,
    TripReducer,
)
from jack.services import FakePaymentService
from jack.tools import IntakeToolsHandler
from jack.vocabulary import JACK_VOCABULARY


def jack_reducers() -> list[Any]:
    """Fresh instances of every reducer a jack session folds with —
    the same list replay and resume must use, in the same order."""
    return [
        *agent_reducers(),
        PricingReducer(),
        IssueReducer(),
        TowReducer(),
        TripReducer(),
        ContactReducer(),
        PaymentReducer(),
        CompletionReducer(),
    ]


async def build_session(
    *,
    log_path: Path,
    payments_path: Path,
    call_id: str,
    model_handler: Any,
    amount_cents: int | None = None,
    currency: str = "USD",
    payment_service: Any = None,
    instructions_text: str | None = None,
) -> Session:
    service = payment_service or FakePaymentService(payments_path)
    resuming = log_path.exists() and log_path.stat().st_size > 0
    log = JsonlEventLog(log_path, vocabulary=JACK_VOCABULARY)
    handlers = {
        "model": model_handler,
        "intake": IntakeToolsHandler(),
        "payment_link": SendPaymentLinkHandler(service, call_id),
        "payment_check": CheckPaymentHandler(service),
        "payment_policy": PaymentPolicyGuard(),
    }
    if resuming:
        return await Session.open(
            reducers=jack_reducers(),
            handlers=handlers,
            log=log,
            error_result=jack_error_result,
        )
    if amount_cents is None:
        raise ValueError("amount_cents is required for a fresh call")
    connections = [
        Connection(id="model", handler="model"),
        Connection(id="intake", handler="intake"),
        Connection(
            id="payment",
            handler="payment_link",
            guards=(
                GuardBinding(connection_id="payment-policy", direction="outbound"),
            ),
        ),
        Connection(id="payment-check", handler="payment_check"),
        Connection(
            id="payment-policy",
            handler="payment_policy",
            config={"amount_cents": amount_cents},
        ),
    ]
    init = SessionInit(
        instructions=instructions_text or prompts.instructions(amount_cents, currency)
    )
    return await Session.open(
        reducers=jack_reducers(),
        handlers=handlers,
        connections=connections,
        log=log,
        init=init,
        error_result=jack_error_result,
    )
