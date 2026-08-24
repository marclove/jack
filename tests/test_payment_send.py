from rig.core import CommandRejected
from rigging import booted

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


def test_all_facts_present_emits_send_with_price_phone_and_attempt_1() -> None:
    run = booted().then(*ALL_FACTS)
    assert run.commands("send_payment_link") == [
        SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=1)
    ]


def test_each_missing_fact_suppresses_the_send() -> None:
    for omitted in ALL_FACTS:
        run = booted().then(*(e for e in ALL_FACTS if e is not omitted))
        assert (
            run.commands("send_payment_link") == []
        ), f"send emitted despite missing {omitted.type}"


def test_tow_judged_inappropriate_never_sends() -> None:
    run = booted().then(PRICING, TOW_NO, TRIP, CONTACT)
    assert run.commands("send_payment_link") == []


def test_link_sent_ok_records_link_and_stops_asking() -> None:
    run = booted().then(*ALL_FACTS, LINK_OK)
    payment = run.state.slices["payment"]
    assert payment.link_id == "link-1"
    assert payment.status == "pending"
    assert payment.attempts == 1
    assert run.commands("send_payment_link") == []


def test_rejection_retires_the_standing_request_and_keeps_the_reason() -> None:
    run = booted().then(
        *ALL_FACTS,
        CommandRejected(
            command="send_payment_link", key=None, reason="phone looks wrong"
        ),
    )
    payment = run.state.slices["payment"]
    assert payment.halted == "rejected"
    assert payment.halt_reason == "phone looks wrong"
    assert run.commands("send_payment_link") == []
    assert run.state.pending == {}


def test_new_contact_clears_a_halt_and_rearms_the_send() -> None:
    run = booted().then(
        *ALL_FACTS,
        CommandRejected(command="send_payment_link", key=None, reason="bad phone"),
        ContactRecorded(phone="555-999-8888"),
    )
    assert run.state.slices["payment"].halted is None
    assert run.commands("send_payment_link") == [
        SendPaymentLink(phone="555-999-8888", amount_cents=15000, attempt=1)
    ]


def test_send_failure_halts_like_a_rejection() -> None:
    failed = PaymentLinkSent(status="error", error="FakePaymentFailure: boom")
    run = booted().then(*ALL_FACTS, failed)
    payment = run.state.slices["payment"]
    assert payment.halted == "send_failed"
    assert payment.attempts == 0
    assert run.commands("send_payment_link") == []


def test_expiry_rearms_the_send_with_the_next_attempt() -> None:
    run = booted().then(
        *ALL_FACTS, LINK_OK, PaymentStatusChecked(link_id="link-1", status="expired")
    )
    assert run.state.slices["payment"].status == "expired"
    assert run.commands("send_payment_link") == [
        SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=2)
    ]


def test_attempt_cap_stops_resending_after_max_links() -> None:
    run = booted().then(*ALL_FACTS)
    for n in range(1, MAX_LINK_ATTEMPTS + 1):
        run = run.then(
            PaymentLinkSent(
                link_id=f"link-{n}",
                url=f"https://pay.example/link-{n}",
                amount_cents=15000,
            ),
            PaymentStatusChecked(link_id=f"link-{n}", status="expired"),
        )
    assert run.state.slices["payment"].attempts == MAX_LINK_ATTEMPTS
    assert run.commands("send_payment_link") == []


def test_paid_stops_everything() -> None:
    run = booted().then(
        *ALL_FACTS, LINK_OK, PaymentStatusChecked(link_id="link-1", status="paid")
    )
    assert run.state.slices["payment"].status == "paid"
    assert run.commands("send_payment_link") == []
