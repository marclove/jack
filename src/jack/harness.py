"""JackHarness: jack as a rig ``Harness`` (rig's eval design §3).

One definition of the harness, three drivers: the CLI opens it over a
JSONL log, an eval suite rolls it out over fresh in-memory logs, and a
future optimizer applies candidates through the same ``wire``.
Applying a candidate is ``harness.wire(JackParams(**candidate))`` —
no source edits.

The constructor holds the per-call context (call id, price, and
zero-argument factories for the model handler and payment service).
The factories are called inside ``wire`` so every rollout gets fresh
instances — that is what keeps concurrent eval rollouts hermetic when
a suite shares one harness. ``wire`` itself is pure-ish: same params,
same wiring, no side effects beyond calling the factories.
"""

from collections.abc import Callable
from typing import Any

from rig.core import GuardBinding, SessionInit, agent_reducers
from rig.runtime import Connection, Wiring

from jack.guards import PaymentPolicyGuard
from jack.payments import (
    CheckPaymentHandler,
    SendPaymentLinkHandler,
    jack_error_result,
)
from jack.prompts import JackParams, format_price
from jack.reducers import (
    CompletionReducer,
    ContactReducer,
    IssueReducer,
    PaymentReducer,
    PricingReducer,
    TowReducer,
    TripReducer,
)
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


class JackHarness:
    """The factory every driver targets. ``wire`` is the one place a
    candidate's text meets structure: the instructions template is
    formatted with the tow price into ``SessionInit``, and the tool
    descriptions reach ``IntakeToolsHandler``'s schemas."""

    vocabulary = JACK_VOCABULARY
    params_type = JackParams

    def __init__(
        self,
        *,
        call_id: str,
        amount_cents: int,
        model: Callable[[], Any],
        payments: Callable[[], Any],
        currency: str = "USD",
    ) -> None:
        self.call_id = call_id
        self.amount_cents = amount_cents
        self.currency = currency
        self._model = model
        self._payments = payments

    def wire(self, params: JackParams) -> Wiring:
        service = self._payments()
        handlers = {
            "model": self._model(),
            "intake": IntakeToolsHandler(descriptions=params.tool_descriptions()),
            "payment_link": SendPaymentLinkHandler(service, self.call_id),
            "payment_check": CheckPaymentHandler(service),
            "payment_policy": PaymentPolicyGuard(),
        }
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
                config={"amount_cents": self.amount_cents},
            ),
        ]
        init = SessionInit(
            instructions=params.instructions.format(
                price=format_price(self.amount_cents, self.currency)
            )
        )
        return Wiring(
            reducers=jack_reducers(),
            handlers=handlers,
            connections=connections,
            init=init,
            error_result=jack_error_result,
        )
