from rig.core import ExecuteTool
from rig.runtime import DispatchContext

from jack.tools import IntakeToolsHandler

CTX = DispatchContext()


def call(name: str, args: dict, call_id: str = "t1") -> ExecuteTool:
    return ExecuteTool(call_id=call_id, name=name, args=args)


def test_describe_declares_five_tools_keyed_by_call_id() -> None:
    description = IntakeToolsHandler().describe()
    (pair,) = description.pairs
    assert (pair.command, pair.result) == ("execute_tool", "tool_result")
    assert description.tools is not None
    assert {t.name for t in description.tools} == {
        "record_issue",
        "judge_tow",
        "record_locations",
        "record_contact",
        "complete_intake",
    }
    for tool in description.tools:
        assert tool.description
        assert tool.parameters["type"] == "object"


async def test_dispatch_returns_ack_plus_domain_event() -> None:
    handler = IntakeToolsHandler()
    result = await handler.dispatch(
        call("record_issue", {"summary": "dead battery", "vehicle": "Civic"}), CTX
    )
    ack, event = result
    assert ack.type == "tool_result"
    assert ack.call_id == "t1"
    assert ack.status == "ok"
    assert event.type == "issue_recorded"
    assert event.summary == "dead battery"


async def test_every_tool_maps_to_its_event() -> None:
    handler = IntakeToolsHandler()
    cases = {
        "judge_tow": ({"appropriate": True, "reason": "undrivable"}, "tow_judged"),
        "record_locations": ({"pickup": "A", "dropoff": "B"}, "locations_recorded"),
        "record_contact": ({"phone": "555-123-4567"}, "contact_recorded"),
        "complete_intake": ({"outcome": "paid"}, "intake_completed"),
    }
    for name, (args, event_type) in cases.items():
        _, event = await handler.dispatch(call(name, args), CTX)
        assert event.type == event_type


async def test_unknown_tool_is_an_error_result_not_a_raise() -> None:
    handler = IntakeToolsHandler()
    result = await handler.dispatch(call("warp_drive", {}), CTX)
    assert result.type == "tool_result"
    assert result.status == "error"
    assert result.call_id == "t1"


async def test_invalid_args_are_an_error_result_not_a_raise() -> None:
    handler = IntakeToolsHandler()
    result = await handler.dispatch(call("record_contact", {"phon": "555"}), CTX)
    assert result.type == "tool_result"
    assert result.status == "error"
    assert "phone" in result.error.message
