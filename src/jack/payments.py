"""Handlers for jack's payment commands.

At-least-once: a resumed session may re-dispatch a logged command whose
effect already ran. ``SendPaymentLinkHandler`` passes (call id, attempt)
as the idempotency key so a replayed dispatch returns the same link and
never charges twice; a genuine retry after expiry carries a higher
attempt and gets a fresh link (spec §6).

Handlers do not catch service exceptions: each pair declares its
``failure`` factory (the ``from_error`` classmethod on its result
model), and the session invokes it on a raise — so a handler crash can
never leave a command permanently in flight, and the reducer folds the
failure like any other fact.
"""

from typing import Any

from rig.runtime import DispatchContext, HandlerBase

from jack.services import PaymentLinkService, PaymentStatusService
from jack.vocabulary import PaymentLinkSent, PaymentStatusChecked


class SendPaymentLinkHandler(HandlerBase):
    """Creates a payment link and delivers it by SMS via the service."""

    command = "send_payment_link"
    result = "payment_link_sent"
    failure = PaymentLinkSent.from_error

    def __init__(self, service: PaymentLinkService, call_id: str) -> None:
        self._service = service
        self._call_id = call_id

    async def dispatch(self, command: Any, context: DispatchContext) -> Any:
        key = f"{self._call_id}:{command.attempt}"
        link_id, url = await self._service.create_link(
            command.phone, command.amount_cents, key
        )
        return PaymentLinkSent(
            link_id=link_id, url=url, amount_cents=command.amount_cents
        )


class CheckPaymentHandler(HandlerBase):
    """Asks the service for a link's status. Keyed by ``link_id``; the
    key mirror copies it from the command to the result verbatim."""

    command = "check_payment"
    result = "payment_status_checked"
    keyed_by = "link_id"
    failure = PaymentStatusChecked.from_error

    def __init__(self, service: PaymentStatusService) -> None:
        self._service = service

    async def dispatch(self, command: Any, context: DispatchContext) -> Any:
        status = await self._service.status(command.link_id)
        return PaymentStatusChecked(link_id=command.link_id, status=status)
