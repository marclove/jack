"""Outbound policy on the payment connection (spec §6).

The guard re-derives nothing from conversation state: it checks the
command against its own connection config, so the amount must agree
between two independent sources — reducer state (which built the
command from the pricing slice) and the guard's config. Fail closed:
missing config is a rejection, never an approval.
"""

import re
from typing import Any

from rig.core import GuardCheck, GuardVerdict
from rig.runtime import DispatchContext, HandlerBase

PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s().]{6,19}$")


class PaymentPolicyGuard(HandlerBase):
    """Approves a send_payment_link whose amount matches the configured
    price and whose phone is plausibly a mobile number; rejects
    otherwise, with the reasons joined into one message."""

    command = "guard_check"
    result = "guard_verdict"
    keyed_by = "check_id"

    async def dispatch(
        self, command: GuardCheck, context: DispatchContext
    ) -> GuardVerdict:
        subject: dict[str, Any] = command.subject
        problems: list[str] = []
        expected = context.config.get("amount_cents")
        if expected is None:
            problems.append("guard has no configured amount")
        elif subject.get("amount_cents") != expected:
            problems.append(
                f"amount {subject.get('amount_cents')} does not match "
                f"the configured amount {expected}"
            )
        phone = subject.get("phone") or ""
        if not PHONE_RE.match(phone):
            problems.append(f"phone {phone!r} does not look like a mobile number")
        if problems:
            return GuardVerdict(
                check_id=command.check_id,
                verdict="reject",
                direction=command.direction,
                reason="; ".join(problems),
            )
        return GuardVerdict(
            check_id=command.check_id,
            verdict="approve",
            direction=command.direction,
        )
