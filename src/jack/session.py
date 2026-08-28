"""Session assembly: the harness plus the log.

Fresh log: ``JackHarness.wire`` produces the full wiring and the
``SessionInit`` carrying the formatted instructions. Existing log: the
log is the only truth — no connections, no init; the handler registry
alone must satisfy every logged ``handler_id`` (``model``, ``intake``,
``payment_link``, ``payment_check``, ``payment_policy``). Renaming a
registry key is therefore a breaking change for existing call logs.
"""

from pathlib import Path
from typing import Any

from rig.adapters.jsonl import JsonlEventLog
from rig.runtime import Session

from jack.guards import PaymentPolicyGuard
from jack.harness import JackHarness, jack_reducers
from jack.payments import (
    CheckPaymentHandler,
    SendPaymentLinkHandler,
    jack_error_result,
)
from jack.prompts import JackParams
from jack.services import FakePaymentService
from jack.tools import IntakeToolsHandler
from jack.vocabulary import JACK_VOCABULARY

__all__ = ["build_session", "jack_reducers"]


async def build_session(
    *,
    log_path: Path,
    payments_path: Path,
    call_id: str,
    model_handler: Any,
    amount_cents: int | None = None,
    currency: str = "USD",
    payment_service: Any = None,
    params: JackParams | None = None,
) -> Session:
    """Open a jack session over its JSONL log. A fresh log wires
    through ``JackHarness`` with ``params`` (default: the baseline
    ``JackParams()``); an existing log reopens with handlers only —
    candidates never apply retroactively to a recorded call."""
    service = payment_service or FakePaymentService(payments_path)
    resuming = log_path.exists() and log_path.stat().st_size > 0
    log = JsonlEventLog(log_path, vocabulary=JACK_VOCABULARY)
    if resuming:
        handlers = {
            "model": model_handler,
            "intake": IntakeToolsHandler(),
            "payment_link": SendPaymentLinkHandler(service, call_id),
            "payment_check": CheckPaymentHandler(service),
            "payment_policy": PaymentPolicyGuard(),
        }
        return await Session.open(
            reducers=jack_reducers(),
            handlers=handlers,
            log=log,
            error_result=jack_error_result,
        )
    if amount_cents is None:
        raise ValueError("amount_cents is required for a fresh call")
    harness = JackHarness(
        call_id=call_id,
        amount_cents=amount_cents,
        currency=currency,
        model=lambda: model_handler,
        payments=lambda: service,
    )
    return await Session.open(harness.wire(params or JackParams()), log=log)
