from pathlib import Path

from rig.adapters.jsonl import JsonlEventLog
from rig.core import create_engine
from scripted import ScriptedModelHandler, text_response, tool_call

from jack import prompts
from jack.services import FakePaymentService
from jack.session import build_session, jack_reducers
from jack.vocabulary import JackCommand, JackEvent, PollTick, PricingConfigured

INTAKE_SCRIPT = [
    tool_call(
        "intake__record_issue", {"summary": "engine died", "vehicle": "Civic"}, "c1"
    ),
    tool_call("intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"),
    tool_call("intake__record_locations", {"pickup": "A", "dropoff": "B"}, "c3"),
    tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c4"),
    text_response("Link sent."),
]


async def run_to_pending_link(tmp_path: Path):
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(list(INTAKE_SCRIPT)),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    async for _ in session.send("My car died"):
        pass
    await session.run(PollTick())
    return session


async def test_replay_matches_live_state(tmp_path: Path) -> None:
    session = await run_to_pending_link(tmp_path)
    entries = await session.log.load()
    events = [e.event for e in entries if e.kind == "event"]
    replayed = create_engine(reducers=jack_reducers()).replay(events)
    assert replayed.slices == session.state.slices
    assert replayed.pending == session.state.pending


async def test_resume_folds_identical_state_and_completes_the_call(
    tmp_path: Path,
) -> None:
    session = await run_to_pending_link(tmp_path)
    live_slices = dict(session.state.slices)
    link_id = session.state.slices["payment"].link_id
    assert link_id is not None
    del session  # the process "dies"

    resumed = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(
            [
                tool_call("intake__complete_intake", {"outcome": "paid"}, "c9"),
                text_response("Goodbye!"),
            ]
        ),
    )
    assert dict(resumed.state.slices) == live_slices

    FakePaymentService(tmp_path / "pay.json").mark_paid(link_id)
    await resumed.run(PollTick())
    assert resumed.state.slices["payment"].status == "paid"

    async for _ in resumed.send(prompts.PAYMENT_CONFIRMED_NOTICE):
        pass
    assert resumed.state.slices["completion"].outcome == "paid"


async def test_resume_after_kill_between_send_and_result_does_not_double_charge(
    tmp_path: Path,
) -> None:
    """Simulate a crash window: the send_payment_link command is logged
    but its result is not. Resume must re-dispatch and, thanks to the
    (call id, attempt) idempotency key, land on the same link."""
    session = await run_to_pending_link(tmp_path)
    first_link = session.state.slices["payment"].link_id
    assert first_link is not None
    raw_lines = (tmp_path / "call.jsonl").read_text().splitlines()
    entries = await session.log.load()
    cut = next(
        i
        for i, e in enumerate(entries)
        if e.kind == "command" and e.command.type == "send_payment_link"
    )
    truncated_path = tmp_path / "truncated.jsonl"
    truncated_path.write_text("\n".join(raw_lines[: cut + 1]) + "\n")
    # sanity: the truncated log parses and ends on the logged command
    check = JsonlEventLog(truncated_path, events=JackEvent, commands=JackCommand)
    tail = (await check.load())[-1]
    assert tail.kind == "command" and tail.command.type == "send_payment_link"

    resumed = await build_session(
        log_path=truncated_path,
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler([text_response("Link sent.")]),
    )
    # the send is in flight (a call_model logged in the same dispatch
    # batch may be in flight beside it — the script answers it)
    assert "send_payment_link" in resumed.state.pending
    async for _ in resumed.resume(mode="redispatch"):
        pass
    payment = resumed.state.slices["payment"]
    assert payment.link_id == first_link  # same idempotency key, same link
    assert payment.status == "pending"
    assert resumed.state.pending == {}
