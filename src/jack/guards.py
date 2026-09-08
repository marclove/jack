"""Outbound policy on the payment connection (spec §6).

The guard re-derives nothing from conversation state: it checks the
command against its own connection config, so the amount must agree
between two independent sources — reducer state (which built the
command from the pricing slice) and the guard's config. Fail closed:
missing config is a rejection, never an approval.
"""

import re
from collections.abc import Mapping
from typing import Any

from rig.runtime import Verdict, approve, reject

PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s().]{6,19}$")


async def payment_policy(
    subject: dict[str, Any],
    direction: str,
    config: Mapping[str, Any],
) -> Verdict:
    """Approves a send_payment_link whose amount matches the configured
    price and whose phone is plausibly a mobile number; rejects
    otherwise, with the reasons joined into one message. Wire it with
    ``rig.runtime.guard(payment_policy)``."""
    problems: list[str] = []
    expected = config.get("amount_cents")
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
        return reject("; ".join(problems))
    return approve()
