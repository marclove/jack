from pathlib import Path

from scripted import ScriptedModelHandler, text_response, tool_call

from jack import prompts
from jack.services import FakePaymentService
from jack.session import build_session
from jack.vocabulary import PricingConfigured


async def drain(gen) -> list:
    return [event async for event in gen]


async def test_bad_phone_is_rejected_then_corrected(tmp_path: Path) -> None:
    script = [
        tool_call(
            "intake__record_issue", {"summary": "dead", "vehicle": "Civic"}, "c1"
        ),
        tool_call(
            "intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"
        ),
        tool_call("intake__record_locations", {"pickup": "A", "dropoff": "B"}, "c3"),
        tool_call("intake__record_contact", {"phone": "not a number"}, "c4"),
        text_response("Hmm, let me check that number."),
        # second round: the notice arrives, model re-records the contact
        tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c5"),
        text_response("Link sent."),
    ]
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))

    events = await drain(session.send("My car died"))
    types = [e.type for e in events]
    assert "command_rejected" in types
    assert "payment_link_sent" not in types
    payment = session.state.slices["payment"]
    assert payment.halted == "rejected"
    assert payment.halt_reason is not None
    assert "phone" in payment.halt_reason

    events = await drain(
        session.send(prompts.link_rejected_notice(payment.halt_reason))
    )
    types = [e.type for e in events]
    assert "contact_recorded" in types
    assert "payment_link_sent" in types
    assert session.state.slices["payment"].status == "pending"


async def test_no_tow_needed_completes_without_payment(tmp_path: Path) -> None:
    script = [
        tool_call(
            "intake__record_issue", {"summary": "out of fuel", "vehicle": "Civic"}, "c1"
        ),
        tool_call(
            "intake__judge_tow", {"appropriate": False, "reason": "fuel delivery"}, "c2"
        ),
        tool_call("intake__complete_intake", {"outcome": "no_tow_needed"}, "c3"),
        text_response("A fuel truck is a better fit — goodbye!"),
    ]
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    await drain(session.send("I ran out of gas"))

    assert session.state.slices["completion"].outcome == "no_tow_needed"
    payment = session.state.slices["payment"]
    assert payment.link_id is None and payment.attempts == 0


async def test_injected_send_failure_maps_to_send_failed_halt(tmp_path: Path) -> None:
    """Fault injection through the whole stack (spec §10): the fake raises
    on create_link, the session routes the raise through jack_error_result,
    the reducer folds the error result and halts the standing request."""
    script = [
        tool_call(
            "intake__record_issue", {"summary": "dead", "vehicle": "Civic"}, "c1"
        ),
        tool_call(
            "intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"
        ),
        tool_call("intake__record_locations", {"pickup": "A", "dropoff": "B"}, "c3"),
        tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c4"),
        text_response("One moment."),
        # after the failure notice, the model re-records the contact to retry
        tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c5"),
        text_response("Link sent."),
    ]
    failing = FakePaymentService(tmp_path / "pay.json", fail_creates=1)
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
        payment_service=failing,
    )
    await session.run(PricingConfigured(amount_cents=15000))

    events = await drain(session.send("My car died"))
    types = [e.type for e in events]
    assert "payment_link_sent" in types  # the error-status result event
    payment = session.state.slices["payment"]
    assert payment.halted == "send_failed"
    assert payment.halt_reason is not None
    assert "FakePaymentFailure" in payment.halt_reason
    assert payment.attempts == 0

    await drain(session.send(prompts.link_failed_notice(payment.halt_reason)))
    assert session.state.slices["payment"].status == "pending"
