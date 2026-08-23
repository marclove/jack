"""One live smoke behind an env flag (spec §10): a short scripted
customer reaches payment_link_sent against the real API on the cheapest
served model, with the fake payment service."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("JACK_LIVE_SMOKE"),
    reason="set JACK_LIVE_SMOKE=1 (and ANTHROPIC_API_KEY) to run",
)

CUSTOMER_LINES = [
    "Hi, my car's engine just died on the highway, it won't restart at all. "
    "It's a 2015 Honda Civic.",
    "I'm at the corner of 5th and Main in Springfield. Please tow it to "
    "Joe's Garage on Elm Street. Yes, I want the tow.",
    "My mobile number is 555-123-4567. Go ahead.",
]


async def test_live_intake_reaches_a_payment_link(tmp_path: Path) -> None:
    import anthropic
    from rig.adapters.anthropic import anthropic_handler

    from jack import prompts
    from jack.session import build_session
    from jack.vocabulary import PricingConfigured

    client = anthropic.AsyncAnthropic()
    session = await build_session(
        log_path=tmp_path / "smoke.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="smoke",
        model_handler=anthropic_handler(client, model=prompts.SMOKE_MODEL),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    for line in CUSTOMER_LINES:
        await session.send(line)
        if session.state.slices["payment"].link_id is not None:
            break

    payment = session.state.slices["payment"]
    assert payment.link_id is not None, (
        "no payment link after the scripted dialogue; inspect "
        f"{tmp_path / 'smoke.jsonl'}"
    )
    assert payment.status == "pending"
