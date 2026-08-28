"""Every string the model can see, as a named injectable parameter.

rig's rule (design doc §9.1): prompts, tool descriptions, notices, and
model choice are the most-tuned parts of a harness, so none of them may
be a literal inside a reducer, handler, or the CLI. Change wording here;
nothing else moves.

``JackParams`` at the bottom is the typed form of this surface — the
declared candidate an optimizer or eval suite tunes (rig's eval
design). Its defaults are the constants in this file; the notices,
acks, and parameter schemas are not on the surface yet and stay plain
constants.
"""

from pydantic import Field

from rig.core import Params

DEFAULT_MODEL = "claude-opus-5"
SMOKE_MODEL = "claude-haiku-4-5"


def format_price(amount_cents: int, currency: str) -> str:
    value = amount_cents / 100
    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}"


_INSTRUCTIONS_TEMPLATE = """\
You are Jack, a roadside assistance intake agent for Agero. You are on a call
with a customer who has a vehicle problem. Speak plainly and briefly, one
question at a time.

Your job, in order:
1. Find out what is wrong. When you know the problem and the vehicle, call
   record_issue.
2. Decide whether a tow is appropriate. Mechanical failure, an accident, or
   anything that makes the vehicle unsafe to drive warrants a tow; a jump
   start, fuel delivery, or lockout does not. Call judge_tow with your
   decision either way.
3. If a tow is appropriate: ask where the vehicle is and where it should be
   towed, then call record_locations. Tell the customer the tow costs
   {price}, ask for their mobile number, and call record_contact. A payment
   link is sent to their phone automatically once these facts are recorded;
   tell the customer to expect it.
4. Wait for payment. System notices in the conversation will tell you when
   the payment arrives or the link fails; relay what they say to the
   customer and follow their instructions.
5. When a notice confirms payment, tell the customer the tow is booked, call
   complete_intake with outcome "paid", and say goodbye.

If no tow is needed, give brief practical advice, call complete_intake with
outcome "no_tow_needed", and say goodbye. If the customer gives up or payment
cannot be completed, call complete_intake with outcome "abandoned".

Record facts with tools as soon as the customer provides them. Never invent
values the customer did not give you.
"""


def instructions(amount_cents: int, currency: str = "USD") -> str:
    return _INSTRUCTIONS_TEMPLATE.format(price=format_price(amount_cents, currency))


PAYMENT_CONFIRMED_NOTICE = (
    "[system notice] The customer's payment was received. Tell them the tow "
    "is booked, call complete_intake with outcome 'paid', and say goodbye."
)


def link_rejected_notice(reason: str) -> str:
    return (
        f"[system notice] The payment link could not be sent: {reason}. Ask "
        "the customer to confirm their mobile number and record it again "
        "with record_contact."
    )


def link_failed_notice(reason: str) -> str:
    return (
        f"[system notice] Sending the payment link failed: {reason}. "
        "Apologize for the delay, confirm the customer's mobile number, and "
        "record it again with record_contact to retry."
    )


def link_expired_notice(final: bool) -> str:
    if final:
        return (
            "[system notice] The payment link expired again and no more links "
            "will be sent. Apologize and call complete_intake with outcome "
            "'abandoned'."
        )
    return (
        "[system notice] The payment link expired. A new link has been sent "
        "to the customer's phone; tell them to use the newest message."
    )


TOOL_DESCRIPTIONS = {
    "record_issue": (
        "Record the customer's problem once you know what is wrong and what "
        "vehicle they have."
    ),
    "judge_tow": (
        "Record your judgment on whether a tow is appropriate, with a short "
        "reason. Call this exactly once you have enough information."
    ),
    "record_locations": (
        "Record where the vehicle is now and where it should be towed."
    ),
    "record_contact": (
        "Record the customer's mobile phone number for the payment link. "
        "Call it again if the number was wrong or a notice asks you to."
    ),
    "complete_intake": (
        "Close the call with its outcome. Call this exactly once, at the end."
    ),
}

TOOL_PARAMETERS = {
    "record_issue": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "What is wrong, in one sentence.",
            },
            "vehicle": {
                "type": "string",
                "description": "The vehicle, e.g. '2015 Honda Civic'.",
            },
        },
        "required": ["summary", "vehicle"],
    },
    "judge_tow": {
        "type": "object",
        "properties": {
            "appropriate": {
                "type": "boolean",
                "description": "Whether a tow is warranted.",
            },
            "reason": {
                "type": "string",
                "description": "One-sentence justification.",
            },
        },
        "required": ["appropriate", "reason"],
    },
    "record_locations": {
        "type": "object",
        "properties": {
            "pickup": {
                "type": "string",
                "description": "Where the vehicle is now.",
            },
            "dropoff": {
                "type": "string",
                "description": "Where it should be towed.",
            },
        },
        "required": ["pickup", "dropoff"],
    },
    "record_contact": {
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "description": "The customer's mobile number.",
            },
        },
        "required": ["phone"],
    },
    "complete_intake": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["paid", "no_tow_needed", "abandoned"],
                "description": "How the call ended.",
            },
        },
        "required": ["outcome"],
    },
}

TOOL_ACKS = {
    "record_issue": "Issue recorded.",
    "judge_tow": "Judgment recorded.",
    "record_locations": "Locations recorded.",
    "record_contact": "Contact recorded.",
    "complete_intake": "Intake closed.",
}


class JackParams(Params):
    """Jack's tunable surface (rig eval design §2): the instructions
    template and the five intake tool descriptions. Applying a
    candidate is ``JackParams(**candidate)``; the defaults are this
    file's constants. ``{price}`` in ``instructions`` is replaced with
    the formatted tow price when the harness wires a session — the
    tunable text is the template, the price is data."""

    instructions: str = Field(
        default=_INSTRUCTIONS_TEMPLATE,
        description=(
            "System instructions template for the intake agent; '{price}' "
            "is replaced with the formatted tow price at session start."
        ),
    )
    record_issue_tool: str = Field(
        default=TOOL_DESCRIPTIONS["record_issue"],
        description="Tool description for record_issue.",
    )
    judge_tow_tool: str = Field(
        default=TOOL_DESCRIPTIONS["judge_tow"],
        description="Tool description for judge_tow.",
    )
    record_locations_tool: str = Field(
        default=TOOL_DESCRIPTIONS["record_locations"],
        description="Tool description for record_locations.",
    )
    record_contact_tool: str = Field(
        default=TOOL_DESCRIPTIONS["record_contact"],
        description="Tool description for record_contact.",
    )
    complete_intake_tool: str = Field(
        default=TOOL_DESCRIPTIONS["complete_intake"],
        description="Tool description for complete_intake.",
    )

    def tool_descriptions(self) -> dict[str, str]:
        """The five descriptions keyed by bare tool name — the shape
        ``IntakeToolsHandler`` consumes. Tool names are identity, never
        surface (rig MVP §9.1); only the description text is tunable."""
        return {
            "record_issue": self.record_issue_tool,
            "judge_tow": self.judge_tow_tool,
            "record_locations": self.record_locations_tool,
            "record_contact": self.record_contact_tool,
            "complete_intake": self.complete_intake_tool,
        }
