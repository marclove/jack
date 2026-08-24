from rigging import booted

from jack.vocabulary import (
    CheckPayment,
    ContactRecorded,
    LocationsRecorded,
    PaymentLinkSent,
    PaymentStatusChecked,
    PollTick,
    PricingConfigured,
    TowJudged,
)

FACTS = (
    PricingConfigured(amount_cents=15000),
    TowJudged(appropriate=True, reason="undrivable"),
    LocationsRecorded(pickup="A", dropoff="B"),
    ContactRecorded(phone="555-123-4567"),
)
LINK = PaymentLinkSent(
    link_id="link-1", url="https://pay.example/link-1", amount_cents=15000
)


def pending_link():
    return booted().then(*FACTS, LINK)


def test_no_tick_no_check() -> None:
    # Fold an unrelated event; emit runs but must not ask without a tick.
    run = pending_link().then(ContactRecorded(phone="555-123-4567"))
    assert run.commands("check_payment") == []


def test_one_tick_entitles_exactly_one_check() -> None:
    run = pending_link().then(PollTick())
    assert run.commands("check_payment") == [CheckPayment(link_id="link-1")]
    assert run.state.pending["check_payment"] == frozenset({"link-1"})


def test_pending_result_consumes_the_tick_and_does_not_respin() -> None:
    run = pending_link().then(
        PollTick(), PaymentStatusChecked(link_id="link-1", status="pending")
    )
    payment = run.state.slices["payment"]
    assert (payment.ticks, payment.checks) == (1, 1)
    assert run.commands("check_payment") == []
    assert run.state.pending == {}


def test_error_result_also_consumes_the_tick() -> None:
    run = pending_link().then(
        PollTick(),
        PaymentStatusChecked(link_id="link-1", status="error", error="boom"),
    )
    payment = run.state.slices["payment"]
    assert payment.status == "pending"  # an error result leaves status alone
    assert (payment.ticks, payment.checks) == (1, 1)
    assert run.commands("check_payment") == []


def test_next_tick_rearms_the_check() -> None:
    run = pending_link().then(
        PollTick(),
        PaymentStatusChecked(link_id="link-1", status="pending"),
        PollTick(),
    )
    assert run.commands("check_payment") == [CheckPayment(link_id="link-1")]


def test_no_check_after_paid_even_with_ticks() -> None:
    run = pending_link().then(
        PollTick(),
        PaymentStatusChecked(link_id="link-1", status="paid"),
        PollTick(),
    )
    assert run.commands("check_payment") == []


def test_new_link_resets_entitlement() -> None:
    run = pending_link().then(
        PollTick(),
        PaymentStatusChecked(link_id="link-1", status="expired"),
        # second link created (attempt 2)
        PaymentLinkSent(
            link_id="link-2", url="https://pay.example/link-2", amount_cents=15000
        ),
    )
    payment = run.state.slices["payment"]
    assert (payment.ticks, payment.checks) == (0, 0)
    run = run.then(PollTick())
    assert run.commands("check_payment") == [CheckPayment(link_id="link-2")]
