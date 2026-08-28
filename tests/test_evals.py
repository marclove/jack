"""Scored behavioral evals through rig.eval (rig's eval design §6):
jack rolled out as a Harness against scripted conversations, with
evaluators asserting the outcomes that matter — the regression half of
the eval harness. The same scenarios and evaluators would drive an
optimizer; here CI asserts score thresholds.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rig.core.engine import SessionState
from rig.eval import Scenario, Score, Scripted, run_scenario
from rig.testing import ScriptedModel, reply, tool_call

from jack import prompts
from jack.harness import JackHarness
from jack.prompts import JackParams
from jack.services import FakePaymentService
from jack.vocabulary import PollTick, PricingConfigured

PRICE_CENTS = 15000

NO_TOW_SCRIPT = [
    reply(
        tool_call(
            "intake__record_issue", summary="keys locked inside", vehicle="2015 Civic"
        )
    ),
    reply(
        tool_call(
            "intake__judge_tow", appropriate=False, reason="lockout, no tow needed"
        )
    ),
    reply(tool_call("intake__complete_intake", outcome="no_tow_needed")),
    reply("A locksmith can open it for you — no tow needed. Goodbye!"),
]

TOW_SCRIPT = [
    reply(
        tool_call("intake__record_issue", summary="engine died", vehicle="2015 Civic")
    ),
    reply(tool_call("intake__judge_tow", appropriate=True, reason="undrivable")),
    reply(
        tool_call(
            "intake__record_locations", pickup="5th and Main", dropoff="Joe's Garage"
        )
    ),
    reply(tool_call("intake__record_contact", phone="555-123-4567")),
    reply("A payment link is on its way to your phone."),
    reply(tool_call("intake__complete_intake", outcome="paid")),
    reply("You're all set. Goodbye!"),
]


def harness_for(tmp_path: Path, script: list[Any]) -> JackHarness:
    return JackHarness(
        call_id="eval-call",
        amount_cents=PRICE_CENTS,
        model=lambda: ScriptedModel(list(script)),
        payments=lambda: FakePaymentService(tmp_path / "pay.json"),
    )


async def score_no_tow(state: SessionState, entries: Sequence[Any]) -> Score:
    completion = state.slices["completion"]
    if completion is None:
        return Score(value=0.0, feedback="the call never completed")
    if completion.outcome != "no_tow_needed":
        return Score(
            value=0.0,
            feedback=f"expected outcome no_tow_needed, got {completion.outcome}",
        )
    return Score(value=1.0, feedback="advised the customer and closed without a tow")


async def score_paid_tow(state: SessionState, entries: Sequence[Any]) -> Score:
    types = [e.event.type for e in entries if e.kind == "event"]
    if "payment_link_sent" not in types:
        return Score(value=0.0, feedback="no payment link was ever sent")
    completion = state.slices["completion"]
    if completion is None or completion.outcome != "paid":
        return Score(
            value=0.5,
            feedback="link sent but the call did not close as paid",
        )
    return Score(value=1.0, feedback="tow booked, payment collected, call closed")


class PayingCustomer:
    """A counterparty that plays the customer AND jack's CLI driver:
    boots pricing, reports the breakdown, pays the link out of band,
    delivers the poll tick, then relays the payment notice — exactly
    what the terminal loop does around a live call."""

    def __init__(self, payments_path: Path) -> None:
        self._payments_path = payments_path
        self._paid = False

    async def next_input(
        self, state: SessionState, events: Sequence[Any]
    ) -> Any | None:
        types = [getattr(e, "type", None) for e in events]
        if "pricing_configured" not in types:
            return PricingConfigured(amount_cents=PRICE_CENTS)
        if "user_message" not in types:
            return "My car died on 5th and Main. It's a 2015 Civic."
        if state.slices["completion"] is not None:
            return None
        payment = state.slices["payment"]
        if payment.status == "pending" and payment.link_id and not self._paid:
            FakePaymentService(self._payments_path).mark_paid(payment.link_id)
            self._paid = True
            return PollTick()
        if payment.status == "paid":
            return prompts.PAYMENT_CONFIRMED_NOTICE
        return None


async def test_no_tow_call_scores_full_marks(tmp_path: Path) -> None:
    scenario = Scenario(
        name="lockout-no-tow",
        counterparty=lambda: Scripted(
            [
                PricingConfigured(amount_cents=PRICE_CENTS),
                "I locked my keys in my 2015 Civic.",
            ]
        ),
        evaluator=score_no_tow,
    )
    result = await run_scenario(
        harness_for(tmp_path, NO_TOW_SCRIPT),
        JackParams(),
        scenario,
        max_turns=8,
        experiment="jack-evals",
    )
    assert result.error is None
    assert result.score.value >= 1.0, result.score.feedback


async def test_paid_tow_call_scores_full_marks(tmp_path: Path) -> None:
    scenario = Scenario(
        name="tow-to-paid",
        counterparty=lambda: PayingCustomer(tmp_path / "pay.json"),
        evaluator=score_paid_tow,
    )
    result = await run_scenario(
        harness_for(tmp_path, TOW_SCRIPT),
        JackParams(),
        scenario,
        max_turns=8,
        experiment="jack-evals",
    )
    assert result.error is None
    assert result.score.value >= 1.0, result.score.feedback


async def test_candidate_tool_descriptions_reach_the_schemas(tmp_path: Path) -> None:
    """Candidate application is parameter passing: a candidate's tool
    description lands in the wired handler's schema, no source edits."""
    candidate = JackParams(record_issue_tool="Capture the fault and the car.")
    wiring = harness_for(tmp_path, NO_TOW_SCRIPT).wire(candidate)
    schemas = {
        t.name: t.description for t in wiring.handlers["intake"].describe().tools
    }
    assert schemas["record_issue"] == "Capture the fault and the car."
