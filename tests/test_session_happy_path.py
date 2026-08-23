from pathlib import Path

from scripted import ScriptedModelHandler, text_response, tool_call

from jack import prompts
from jack.services import FakePaymentService
from jack.session import build_session
from jack.vocabulary import PollTick, PricingConfigured

INTAKE_SCRIPT = [
    tool_call(
        "intake__record_issue", {"summary": "engine died", "vehicle": "Civic"}, "c1"
    ),
    tool_call("intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"),
    tool_call(
        "intake__record_locations",
        {"pickup": "5th and Main", "dropoff": "Joe's Garage"},
        "c3",
    ),
    tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c4"),
    text_response("A payment link is on its way to your phone."),
]
WRAP_UP_SCRIPT = [
    tool_call("intake__complete_intake", {"outcome": "paid"}, "c5"),
    text_response("You're all set. Goodbye!"),
]


async def drain(gen) -> list:
    return [event async for event in gen]


async def open_call(tmp_path: Path, script: list):
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    return session


async def test_full_intake_reaches_paid(tmp_path: Path) -> None:
    session = await open_call(tmp_path, [*INTAKE_SCRIPT, *WRAP_UP_SCRIPT])

    events = await drain(session.send("My car died on 5th and Main"))
    types = [e.type for e in events]
    # facts recorded, link sent within the same turn, guarded first
    assert "issue_recorded" in types
    assert "tow_judged" in types
    assert "locations_recorded" in types
    assert "contact_recorded" in types
    assert "guard_verdict" in types
    assert "payment_link_sent" in types

    payment = session.state.slices["payment"]
    assert payment.status == "pending"
    link_id = payment.link_id

    # first poll: still pending
    await session.run(PollTick())
    assert session.state.slices["payment"].status == "pending"

    # customer pays out of band
    fake = FakePaymentService(tmp_path / "pay.json")
    fake.mark_paid(link_id)
    await session.run(PollTick())
    assert session.state.slices["payment"].status == "paid"

    # notice closes the loop
    await drain(session.send(prompts.PAYMENT_CONFIRMED_NOTICE))
    completion = session.state.slices["completion"]
    assert completion is not None and completion.outcome == "paid"


async def test_write_order_command_check_verdict_result(tmp_path: Path) -> None:
    session = await open_call(tmp_path, list(INTAKE_SCRIPT))
    await drain(session.send("My car died"))
    entries = await session.log.load()
    shape = [
        (e.kind, e.event.type if e.kind == "event" else e.command.type) for e in entries
    ]
    i_send = shape.index(("command", "send_payment_link"))
    i_check = shape.index(("command", "guard_check"))
    i_verdict = shape.index(("event", "guard_verdict"))
    i_sent = shape.index(("event", "payment_link_sent"))
    assert i_send < i_check < i_verdict < i_sent
