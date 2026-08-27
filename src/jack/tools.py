"""The intake tool handler: how conversation becomes typed facts.

The model calls a tool; this handler answers with the paired
``tool_result`` plus the corresponding domain event as an extra event
(rig handler contract: dispatch may return "the paired result plus
extras, applied in order"). The domain event — not the tool result
text — is what reducers fold, so state is typed end to end (spec §7).

Tool descriptions, parameter schemas, and acknowledgement texts are all
model visible, so they live in ``jack.prompts``, never here.
"""

from typing import Any

from pydantic import ValidationError
from rig.core import HarnessError, KeyedBy, TextPart, ToolResult, ToolSchema
from rig.runtime import DispatchContext, HandlerDescription, HandlerPair

from jack import prompts
from jack.vocabulary import (
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    LocationsRecorded,
    TowJudged,
)

_EVENT_BUILDERS: dict[str, type] = {
    "record_issue": IssueRecorded,
    "judge_tow": TowJudged,
    "record_locations": LocationsRecorded,
    "record_contact": ContactRecorded,
    "complete_intake": IntakeCompleted,
}


class IntakeToolsHandler:
    """Claims execute_tool/tool_result keyed by call_id and declares the
    five intake tools. At-least-once safe: recording the same fact twice
    folds to the same state (latest fact wins)."""

    def describe(self) -> HandlerDescription:
        return HandlerDescription(
            pairs=(
                HandlerPair(
                    command="execute_tool",
                    result="tool_result",
                    tracking=KeyedBy(keyed_by="call_id"),
                ),
            ),
            tools=tuple(
                ToolSchema(
                    name=name,
                    description=prompts.TOOL_DESCRIPTIONS[name],
                    parameters=prompts.TOOL_PARAMETERS[name],
                )
                for name in _EVENT_BUILDERS
            ),
        )

    async def dispatch(self, command: Any, context: DispatchContext) -> Any:
        builder = _EVENT_BUILDERS.get(command.name)
        if builder is None:
            return self._error(command, f"unknown tool '{command.name}'")
        try:
            event = builder(**command.args)
        except (ValidationError, TypeError) as exc:
            return self._error(command, f"invalid arguments: {exc}")
        ack = ToolResult(
            call_id=command.call_id,
            status="ok",
            content=[TextPart(text=prompts.TOOL_ACKS[command.name])],
        )
        return [ack, event]

    @staticmethod
    def _error(command: Any, message: str) -> ToolResult:
        return ToolResult(
            call_id=command.call_id,
            status="error",
            content=[],
            error=HarnessError(code="tool_error", message=message),
        )
