from rigging import booted, jack_engine

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


def checks(result) -> list:
    return [a.command for a in result.actions if a.command.type == "check_payment"]


def pending_link_state(engine):
    state = booted(engine)
    for event in (*FACTS, LINK):
        state = engine.step(state, event).state
    return state


def test_no_tick_no_check() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    # Fold an unrelated event; emit runs but must not ask without a tick.
    result = engine.step(state, ContactRecorded(phone="555-123-4567"))
    assert checks(result) == []


def test_one_tick_entitles_exactly_one_check() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    result = engine.step(state, PollTick())
    assert checks(result) == [CheckPayment(link_id="link-1")]
    assert result.state.pending["check_payment"] == frozenset({"link-1"})


def test_pending_result_consumes_the_tick_and_does_not_respin() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    state = engine.step(state, PollTick()).state
    result = engine.step(
        state, PaymentStatusChecked(link_id="link-1", status="pending")
    )
    payment = result.state.slices["payment"]
    assert (payment.ticks, payment.checks) == (1, 1)
    assert checks(result) == []
    assert result.state.pending == {}


def test_error_result_also_consumes_the_tick() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    state = engine.step(state, PollTick()).state
    result = engine.step(
        state,
        PaymentStatusChecked(link_id="link-1", status="error", error="boom"),
    )
    payment = result.state.slices["payment"]
    assert payment.status == "pending"  # an error result leaves status alone
    assert (payment.ticks, payment.checks) == (1, 1)
    assert checks(result) == []


def test_next_tick_rearms_the_check() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    state = engine.step(state, PollTick()).state
    state = engine.step(
        state, PaymentStatusChecked(link_id="link-1", status="pending")
    ).state
    result = engine.step(state, PollTick())
    assert checks(result) == [CheckPayment(link_id="link-1")]


def test_no_check_after_paid_even_with_ticks() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    state = engine.step(state, PollTick()).state
    state = engine.step(
        state, PaymentStatusChecked(link_id="link-1", status="paid")
    ).state
    result = engine.step(state, PollTick())
    assert checks(result) == []


def test_new_link_resets_entitlement() -> None:
    engine = jack_engine()
    state = pending_link_state(engine)
    state = engine.step(state, PollTick()).state
    state = engine.step(
        state, PaymentStatusChecked(link_id="link-1", status="expired")
    ).state
    # second link created (attempt 2)
    state = engine.step(
        state,
        PaymentLinkSent(
            link_id="link-2", url="https://pay.example/link-2", amount_cents=15000
        ),
    ).state
    payment = state.slices["payment"]
    assert (payment.ticks, payment.checks) == (0, 0)
    result = engine.step(state, PollTick())
    assert checks(result) == [CheckPayment(link_id="link-2")]
