from rigging import booted, jack_engine

from jack.vocabulary import (
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    LocationsRecorded,
    PollTick,
    PricingConfigured,
    TowJudged,
)


def test_slices_start_empty() -> None:
    engine = jack_engine()
    state = booted(engine)
    for name in ("pricing", "issue", "tow", "trip", "contact", "completion"):
        assert state.slices[name] is None


def test_each_fact_event_fills_exactly_its_slice() -> None:
    engine = jack_engine()
    state = booted(engine)
    pricing = PricingConfigured(amount_cents=15000)
    issue = IssueRecorded(summary="flat tire", vehicle="Civic")
    tow = TowJudged(appropriate=False, reason="spare available")
    trip = LocationsRecorded(pickup="A", dropoff="B")
    contact = ContactRecorded(phone="555-000-1111")
    done = IntakeCompleted(outcome="no_tow_needed")

    for event in (pricing, issue, tow, trip, contact, done):
        state = engine.step(state, event).state

    assert state.slices["pricing"] == pricing
    assert state.slices["issue"] == issue
    assert state.slices["tow"] == tow
    assert state.slices["trip"] == trip
    assert state.slices["contact"] == contact
    assert state.slices["completion"] == done


def test_unrelated_events_leave_slices_alone() -> None:
    engine = jack_engine()
    state = booted(engine)
    state = engine.step(state, PollTick()).state
    assert state.slices["issue"] is None


def test_latest_fact_wins() -> None:
    engine = jack_engine()
    state = booted(engine)
    state = engine.step(state, ContactRecorded(phone="555-000-1111")).state
    state = engine.step(state, ContactRecorded(phone="555-222-3333")).state
    assert state.slices["contact"].phone == "555-222-3333"
