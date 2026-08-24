from pathlib import Path

from rig.adapters.jsonl import JsonlEventLog
from rig.testing import (
    ScriptedModel,
    assert_replay_matches,
    reply,
    shape,
    tool_call,
    truncated,
)

from jack import prompts
from jack.services import FakePaymentService
from jack.session import build_session, jack_reducers
from jack.vocabulary import JACK_VOCABULARY, PollTick, PricingConfigured

INTAKE_SCRIPT = [
    reply(tool_call("intake__record_issue", summary="engine died", vehicle="Civic")),
    reply(tool_call("intake__judge_tow", appropriate=True, reason="undrivable")),
    reply(tool_call("intake__record_locations", pickup="A", dropoff="B")),
    reply(tool_call("intake__record_contact", phone="555-123-4567")),
    reply("Link sent."),
]


async def run_to_pending_link(tmp_path: Path):
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModel(list(INTAKE_SCRIPT)),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    await session.send("My car died")
    await session.run(PollTick())
    return session


async def test_replay_matches_live_state(tmp_path: Path) -> None:
    session = await run_to_pending_link(tmp_path)
    await assert_replay_matches(session, reducers=jack_reducers())


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
        model_handler=ScriptedModel(
            [
                reply(tool_call("intake__complete_intake", outcome="paid")),
                reply("Goodbye!"),
            ]
        ),
    )
    assert dict(resumed.state.slices) == live_slices

    FakePaymentService(tmp_path / "pay.json").mark_paid(link_id)
    await resumed.run(PollTick())
    assert resumed.state.slices["payment"].status == "paid"

    await resumed.send(prompts.PAYMENT_CONFIRMED_NOTICE)
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

    # Cut the log just after the logged command — storage agnostic
    # surgery — then write the crash shape back out as a JSONL log the
    # session builder can reopen.
    cut = await truncated(session.log, after="command:send_payment_link")
    truncated_path = tmp_path / "truncated.jsonl"
    crashed = JsonlEventLog(truncated_path, vocabulary=JACK_VOCABULARY)
    for entry in await cut.load():
        await crashed.append(entry)
    # sanity: the crash shape ends on the logged command
    assert (await shape(crashed))[-1] == "command:send_payment_link"

    resumed = await build_session(
        log_path=truncated_path,
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModel([reply("Link sent.")]),
    )
    # the send is in flight (a call_model logged in the same dispatch
    # batch may be in flight beside it — the script answers it)
    assert "send_payment_link" in resumed.state.pending
    await resumed.resume(mode="redispatch")
    payment = resumed.state.slices["payment"]
    assert payment.link_id == first_link  # same idempotency key, same link
    assert payment.status == "pending"
    assert resumed.state.pending == {}
