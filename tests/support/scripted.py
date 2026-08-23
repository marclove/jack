"""A scripted stand-in for the model connection (rig tutorial pattern)."""

from typing import Any

from rig.core import Message, ModelResponse, TextPart, ToolCallPart, Usage
from rig.runtime import DispatchContext, HandlerBase

USAGE = Usage(input_tokens=1, output_tokens=1)


def tool_call(name: str, args: dict, call_id: str) -> ModelResponse:
    return ModelResponse(
        status="ok",
        message=Message(
            role="assistant",
            parts=[ToolCallPart(call_id=call_id, name=name, args=args)],
        ),
        usage=USAGE,
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        status="ok",
        message=Message(role="assistant", parts=[TextPart(text=text)]),
        usage=USAGE,
    )


class ScriptedModelHandler(HandlerBase):
    """Answers each call_model with the next scripted response."""

    command = "call_model"
    result = "model_response"

    def __init__(self, script: list[ModelResponse]) -> None:
        self.script = list(script)

    async def dispatch(self, command: Any, context: DispatchContext) -> Any:
        return self.script.pop(0)
