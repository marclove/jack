from pathlib import Path

from rig.adapters.jsonl import JsonlEventLog
from rig.runtime.log import CommandEntry, EventEntry

from jack.vocabulary import (
    CheckPayment,
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    JACK_VOCABULARY,
    LocationsRecorded,
    PaymentLinkSent,
    PaymentStatusChecked,
    PollTick,
    PricingConfigured,
    SendPaymentLink,
    TowJudged,
)

ALL_EVENTS = [
    PricingConfigured(amount_cents=15000),
    IssueRecorded(summary="engine died", vehicle="2015 Honda Civic"),
    TowJudged(appropriate=True, reason="engine failure, undrivable"),
    LocationsRecorded(pickup="5th and Main", dropoff="Joe's Garage, Elm St"),
    ContactRecorded(phone="555-123-4567"),
    PaymentLinkSent(
        link_id="link-1", url="https://pay.example/link-1", amount_cents=15000
    ),
    PaymentLinkSent(status="error", error="FakePaymentFailure: boom"),
    PaymentStatusChecked(link_id="link-1", status="pending"),
    PollTick(),
    IntakeCompleted(outcome="paid"),
]
ALL_COMMANDS = [
    SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=1),
    CheckPayment(link_id="link-1"),
]


async def test_jack_vocabulary_round_trips_through_the_jsonl_log(
    tmp_path: Path,
) -> None:
    log = JsonlEventLog(tmp_path / "call.jsonl", vocabulary=JACK_VOCABULARY)
    seq = 0
    for event in ALL_EVENTS:
        await log.append(EventEntry(seq=seq, ts=float(seq), event=event))
        seq += 1
    for command in ALL_COMMANDS:
        await log.append(
            CommandEntry(seq=seq, ts=float(seq), command=command, connection_id="test")
        )
        seq += 1

    reloaded = JsonlEventLog(tmp_path / "call.jsonl", vocabulary=JACK_VOCABULARY)
    entries = await reloaded.load()
    events = [e.event for e in entries if e.kind == "event"]
    commands = [e.command for e in entries if e.kind == "command"]
    assert events == ALL_EVENTS
    assert commands == ALL_COMMANDS
