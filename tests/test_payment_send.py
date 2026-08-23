from rig.core import CommandRejected
from rigging import booted, jack_engine

from jack.reducers import MAX_LINK_ATTEMPTS
from jack.vocabulary import (
    ContactRecorded,
    LocationsRecorded,
    PaymentLinkSent,
    PaymentStatusChecked,
    PricingConfigured,
    SendPaymentLink,
    TowJudged,
)

PRICING = PricingConfigured(amount_cents=15000)
TOW_YES = TowJudged(appropriate=True, reason="undrivable")
TOW_NO = TowJudged(appropriate=False, reason="just needs fuel")
TRIP = LocationsRecorded(pickup="5th and Main", dropoff="Joe's Garage")
CONTACT = ContactRecorded(phone="555-123-4567")
ALL_FACTS = (PRICING, TOW_YES, TRIP, CONTACT)
LINK_OK = PaymentLinkSent(
    link_id="link-1", url="https://pay.example/link-1", amount_cents=15000
)


def sends(result) -> list:
    return [a.command for a in result.actions if a.command.type == "send_payment_link"]


def test_all_facts_present_emits_send_with_price_phone_and_attempt_1() -> None:
    engine = jack_engine()
    state = booted(engine)
    result = None
    for event in ALL_FACTS:
        result = engine.step(state, event)
        state = result.state
    assert sends(result) == [
        SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=1)
    ]


def test_each_missing_fact_suppresses_the_send() -> None:
    engine = jack_engine()
    for omitted in ALL_FACTS:
        state = booted(engine)
        result = None
        for event in ALL_FACTS:
            if event is omitted:
                continue
            result = engine.step(state, event)
            state = result.state
        assert sends(result) == [], f"send emitted despite missing {omitted.type}"


def test_tow_judged_inappropriate_never_sends() -> None:
    engine = jack_engine()
    state = booted(engine)
    result = None
    for event in (PRICING, TOW_NO, TRIP, CONTACT):
        result = engine.step(state, event)
        state = result.state
    assert sends(result) == []


def test_link_sent_ok_records_link_and_stops_asking() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    result = engine.step(state, LINK_OK)
    payment = result.state.slices["payment"]
    assert payment.link_id == "link-1"
    assert payment.status == "pending"
    assert payment.attempts == 1
    assert sends(result) == []


def test_rejection_retires_the_standing_request_and_keeps_the_reason() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    rejection = CommandRejected(
        command="send_payment_link", key=None, reason="phone looks wrong"
    )
    result = engine.step(state, rejection)
    payment = result.state.slices["payment"]
    assert payment.halted == "rejected"
    assert payment.halt_reason == "phone looks wrong"
    assert sends(result) == []
    assert result.state.pending == {}


def test_new_contact_clears_a_halt_and_rearms_the_send() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    state = engine.step(
        state,
        CommandRejected(command="send_payment_link", key=None, reason="bad phone"),
    ).state
    result = engine.step(state, ContactRecorded(phone="555-999-8888"))
    assert result.state.slices["payment"].halted is None
    assert sends(result) == [
        SendPaymentLink(phone="555-999-8888", amount_cents=15000, attempt=1)
    ]


def test_send_failure_halts_like_a_rejection() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    failed = PaymentLinkSent(status="error", error="FakePaymentFailure: boom")
    result = engine.step(state, failed)
    payment = result.state.slices["payment"]
    assert payment.halted == "send_failed"
    assert payment.attempts == 0
    assert sends(result) == []


def test_expiry_rearms_the_send_with_the_next_attempt() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    state = engine.step(state, LINK_OK).state
    result = engine.step(
        state, PaymentStatusChecked(link_id="link-1", status="expired")
    )
    assert result.state.slices["payment"].status == "expired"
    assert sends(result) == [
        SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=2)
    ]


def test_attempt_cap_stops_resending_after_max_links() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    result = None
    for n in range(1, MAX_LINK_ATTEMPTS + 1):
        state = engine.step(
            state,
            PaymentLinkSent(
                link_id=f"link-{n}",
                url=f"https://pay.example/link-{n}",
                amount_cents=15000,
            ),
        ).state
        result = engine.step(
            state, PaymentStatusChecked(link_id=f"link-{n}", status="expired")
        )
        state = result.state
    assert state.slices["payment"].attempts == MAX_LINK_ATTEMPTS
    assert sends(result) == []


def test_paid_stops_everything() -> None:
    engine = jack_engine()
    state = booted(engine)
    for event in ALL_FACTS:
        state = engine.step(state, event).state
    state = engine.step(state, LINK_OK).state
    result = engine.step(state, PaymentStatusChecked(link_id="link-1", status="paid"))
    assert result.state.slices["payment"].status == "paid"
    assert sends(result) == []
