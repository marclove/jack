# jack Intake Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build jack, a roadside assistance intake agent on rig: conversation-driven fact collection, a state-gated guarded payment link send, tick-paced payment polling, and kill/resume as the daily workflow.

**Architecture:** Typed domain events fold into per-concern slices; the payment reducer emits `send_payment_link` as a standing request only when state holds all facts, guarded outbound by a policy handler that cross-checks connection config. Polling cadence enters as `poll_tick` events from the CLI (tick entitlement pattern). Fakes sit behind service protocols, file-backed so `jack pay` and resume work across processes.

**Tech Stack:** Python 3.12, poetry, rig (path dependency with the anthropic extra), pydantic v2, pytest + pytest-asyncio, ruff, ty.

**Spec:** `docs/design/2026-08-23-intake-harness-design.md` — read it first; this plan implements it section by section.

## Global Constraints

- Python `^3.12`; rig via `rig = {path = "../rig-py", develop = true, extras = ["anthropic"]}`.
- Every string the model can see lives in `src/jack/prompts.py` as a named parameter (spec §8). No model-facing literal anywhere else — review every task for this.
- Handlers never raise for effect failure; failures map through `jack_error_result` into the paired result event with `status="error"`.
- The key mirror: `check_payment.link_id` is copied verbatim onto `payment_status_checked.link_id`, always.
- Standing requests: `emit` asks while the condition holds in state; it never sees the triggering event.
- Reducers are pure — no I/O, clocks, or randomness. Time enters only as `poll_tick` events.
- `MAX_LINK_ATTEMPTS = 2` (spec §6): at most two payment links per call.
- Default conversation model `claude-opus-5`; live smoke uses `claude-haiku-4-5` behind `JACK_LIVE_SMOKE=1`.
- Every place rig fights back gets a `docs/friction-log.md` entry in the same commit.
- `make ci` (ruff check, ruff format --check, ty, pytest) must pass at the end of every task.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.gitignore`, `README.md`, `docs/friction-log.md`, `src/jack/__init__.py`, `tests/__init__.py` (empty), `tests/test_package.py`

**Interfaces:**
- Produces: an installable `jack` package; `make test|lint|typecheck|format|ci` targets every later task relies on.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "jack"
version = "0.1.0"
description = "Roadside assistance intake harness built on rig"
requires-python = ">=3.12,<4.0"
dependencies = ["rig[anthropic]"]

[project.scripts]
jack = "jack.cli:main"

[tool.poetry]
packages = [{ include = "jack", from = "src" }]

[tool.poetry.dependencies]
rig = { path = "../rig-py", develop = true, extras = ["anthropic"] }

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
pytest-asyncio = "^0.24"
ruff = "^0.6"
ty = "*"

[tool.ruff]
line-length = 88
src = ["src", "tests"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["tests/support"]

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

If `poetry install` rejects any of this (poetry/ty version drift), fix the constraint minimally and note what changed in the commit message.

- [ ] **Step 2: Write `Makefile`** (tabs, not spaces, for recipe lines)

```makefile
.PHONY: test lint format typecheck ci

test:
	poetry run pytest

lint:
	poetry run ruff check .
	poetry run ruff format --check .

format:
	poetry run ruff format .

typecheck:
	poetry run ty check

ci: lint typecheck test
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
calls/
dist/
```

- [ ] **Step 4: Write `README.md`**

```markdown
# jack

A roadside assistance intake agent built on [rig](../rig-py). A customer
describes their problem in a terminal conversation; jack collects the facts,
judges whether a tow is appropriate, sends a payment link, polls for payment,
and completes the call. Design: `docs/design/2026-08-23-intake-harness-design.md`.

## Run

    poetry install
    export ANTHROPIC_API_KEY=...
    poetry run jack new            # start a call
    poetry run jack resume <id>    # reopen a killed call
    poetry run jack pay <id>       # fake paying the link (second terminal)

`make ci` runs lint, typecheck, and tests.
```

- [ ] **Step 5: Seed `docs/friction-log.md`**

```markdown
# rig friction log

Every place rig fought back while building jack: missing capability, awkward
API, unclear docs, or a pattern jack had to invent. One entry per finding,
added in the same commit as the work that surfaced it.

## 2026-08-23 — no timer or scheduling story

Payment polling needs a cadence, and core is pure (no clocks). jack invented
the tick entitlement pattern: the CLI appends `poll_tick` events on an
interval, and the reducer emits one `check_payment` per unconsumed tick.
Works and replays exactly, but every harness with polling will reinvent it.
rig should decide whether to bless the pattern (docs) or absorb it (a runtime
scheduling facility that appends tick events).

## 2026-08-23 — domain state cannot prompt the model

When payment is confirmed (a domain event), the model needs to be told so it
can wrap up the call. Nothing in rig turns state into a model-visible input;
jack's CLI watches slices and injects named notice messages. Fine for one
harness, but "state change → tell the model" seems universal enough that rig
may want a first-class hook.
```

- [ ] **Step 6: Create the package and a first test**

`src/jack/__init__.py`:

```python
"""jack: a roadside assistance intake harness built on rig."""
```

`tests/__init__.py`: empty file. `tests/test_package.py`:

```python
def test_package_imports() -> None:
    import jack  # noqa: F401
```

- [ ] **Step 7: Install and run CI**

Run: `cd /Users/marc/projects/jack && poetry install && make ci`
Expected: install succeeds, all targets green, 1 test passes.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "chore: project scaffolding, Makefile, friction log seed"
```

---

### Task 2: Domain vocabulary and log unions

**Files:**
- Create: `src/jack/vocabulary.py`
- Test: `tests/test_vocabulary.py`

**Interfaces:**
- Produces: event classes `PricingConfigured(amount_cents, currency)`, `IssueRecorded(summary, vehicle)`, `TowJudged(appropriate, reason)`, `LocationsRecorded(pickup, dropoff)`, `ContactRecorded(phone)`, `PaymentLinkSent(status, link_id, url, amount_cents, error)`, `PaymentStatusChecked(link_id, status, error)`, `PollTick()`, `IntakeCompleted(outcome)`; commands `SendPaymentLink(phone, amount_cents, attempt)`, `CheckPayment(link_id)`; unions `JackEvent`, `JackCommand`. Every later task imports from here.

- [ ] **Step 1: Write the failing test**

`tests/test_vocabulary.py`:

```python
from pathlib import Path

from rig.adapters.jsonl import JsonlEventLog

from jack.vocabulary import (
    CheckPayment,
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    JackCommand,
    JackEvent,
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
    PaymentLinkSent(link_id="link-1", url="https://pay.example/link-1", amount_cents=15000),
    PaymentLinkSent(status="error", error="FakePaymentFailure: boom"),
    PaymentStatusChecked(link_id="link-1", status="pending"),
    PollTick(),
    IntakeCompleted(outcome="paid"),
]
ALL_COMMANDS = [
    SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=1),
    CheckPayment(link_id="link-1"),
]


async def test_jack_vocabulary_round_trips_through_the_jsonl_log(tmp_path: Path) -> None:
    log = JsonlEventLog(tmp_path / "call.jsonl", events=JackEvent, commands=JackCommand)
    for event in ALL_EVENTS:
        await log.append_event(event)
    for command in ALL_COMMANDS:
        await log.append_command(command, connection_id="test")

    reloaded = JsonlEventLog(tmp_path / "call.jsonl", events=JackEvent, commands=JackCommand)
    entries = await reloaded.load()
    events = [e.event for e in entries if e.kind == "event"]
    commands = [e.command for e in entries if e.kind == "command"]
    assert events == ALL_EVENTS
    assert commands == ALL_COMMANDS
```

Note: if `JsonlEventLog`'s append method names differ (check `src/rig/adapters/jsonl/log.py` — they may be a single `append` taking an entry), adapt the test to the real API and keep the round trip assertion identical.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_vocabulary.py -v` — Expected: FAIL (module `jack.vocabulary` not found).

- [ ] **Step 3: Write `src/jack/vocabulary.py`**

```python
"""jack's domain vocabulary: the facts and requests of one intake call.

Events are facts folded into state; commands are requests for effects.
``JackEvent`` / ``JackCommand`` extend rig's built-in unions so the JSONL
log can write and read jack's types — a log built with rig's defaults
cannot reopen a jack call (spec §4).
"""

from typing import Annotated, Literal

from pydantic import Field

from rig.core import Command, Event, FrozenModel


class PricingConfigured(FrozenModel):
    """The flat tow price, entering as data on the log at session open.

    Reducers are pure and cannot read configuration, so pricing folds
    from this event into the pricing slice (spec §5)."""

    type: Literal["pricing_configured"] = "pricing_configured"
    amount_cents: int
    currency: str = "USD"


class IssueRecorded(FrozenModel):
    """The customer's problem, as facts the model extracted."""

    type: Literal["issue_recorded"] = "issue_recorded"
    summary: str
    vehicle: str


class TowJudged(FrozenModel):
    """The model's judgment on whether a tow is appropriate."""

    type: Literal["tow_judged"] = "tow_judged"
    appropriate: bool
    reason: str


class LocationsRecorded(FrozenModel):
    """Pickup and destination for the tow."""

    type: Literal["locations_recorded"] = "locations_recorded"
    pickup: str
    dropoff: str


class ContactRecorded(FrozenModel):
    """The customer's mobile number. Also re-arms a halted payment send
    (spec §6): recording contact clears a rejection or send failure."""

    type: Literal["contact_recorded"] = "contact_recorded"
    phone: str


class PaymentLinkSent(FrozenModel):
    """Result of ``send_payment_link``. ``status="error"`` reports a
    failed send (handlers report failure, they do not raise); the link
    fields are then None and ``error`` says why."""

    type: Literal["payment_link_sent"] = "payment_link_sent"
    status: Literal["ok", "error"] = "ok"
    link_id: str | None = None
    url: str | None = None
    amount_cents: int | None = None
    error: str | None = None


class PaymentStatusChecked(FrozenModel):
    """Result of ``check_payment``. ``link_id`` mirrors the command's key
    verbatim (the key mirror) — never recomputed, never defaulted.
    ``status="error"`` reports a failed check; the tick that paid for it
    is consumed either way (spec §6)."""

    type: Literal["payment_status_checked"] = "payment_status_checked"
    link_id: str
    status: Literal["pending", "paid", "expired", "error"]
    error: str | None = None


class PollTick(FrozenModel):
    """One unit of polling entitlement. The CLI owns the clock and
    appends ticks; the payment reducer emits one ``check_payment`` per
    unconsumed tick (spec §6)."""

    type: Literal["poll_tick"] = "poll_tick"


class IntakeCompleted(FrozenModel):
    """The call is over. The CLI exits when this folds."""

    type: Literal["intake_completed"] = "intake_completed"
    outcome: Literal["paid", "no_tow_needed", "abandoned"]


class SendPaymentLink(FrozenModel):
    """Ask the payment service to create a link and deliver it by SMS.

    ``attempt`` scopes idempotency: a resumed session re-dispatching the
    same attempt gets the same link; a retry after expiry (a higher
    attempt) gets a fresh one (spec §6)."""

    type: Literal["send_payment_link"] = "send_payment_link"
    phone: str
    amount_cents: int
    attempt: int


class CheckPayment(FrozenModel):
    """Ask the payment service for a link's status. Keyed by ``link_id``,
    which ``payment_status_checked`` mirrors back."""

    type: Literal["check_payment"] = "check_payment"
    link_id: str


JackEvent = Annotated[
    Event
    | PricingConfigured
    | IssueRecorded
    | TowJudged
    | LocationsRecorded
    | ContactRecorded
    | PaymentLinkSent
    | PaymentStatusChecked
    | PollTick
    | IntakeCompleted,
    Field(discriminator="type"),
]

JackCommand = Annotated[
    Command | SendPaymentLink | CheckPayment, Field(discriminator="type")
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_vocabulary.py -v` — Expected: PASS.

- [ ] **Step 5: Run `make ci`, then commit**

```bash
git add -A && git commit -m "feat: domain vocabulary and log unions"
```

---

### Task 3: Fact reducers

**Files:**
- Create: `src/jack/reducers.py`, `tests/support/rigging.py`
- Test: `tests/test_fact_reducers.py`

**Interfaces:**
- Consumes: vocabulary from Task 2.
- Produces: `PricingReducer` (slice `pricing`, holds `PricingConfigured | None`), `IssueReducer` (`issue`), `TowReducer` (`tow`), `TripReducer` (`trip`), `ContactReducer` (`contact`), `CompletionReducer` (`completion`) — each slice holds its event instance or `None`. Also test helper `jack_engine()` and `booted(engine)` in `tests/support/rigging.py`.

- [ ] **Step 1: Write the test support helper**

`tests/support/rigging.py`:

```python
"""Engine-level test rigging: a pure engine with jack's reducers and the
payment topology folded in, as a session's boot turn would produce it."""

from typing import Any

from rig.core import ConnectionAdded, ConnectionDef, KeyedByField, create_engine

from jack.reducers import (
    CompletionReducer,
    ContactReducer,
    IssueReducer,
    PaymentReducer,
    PricingReducer,
    TowReducer,
    TripReducer,
)

PAYMENT_DEF = ConnectionDef(
    id="payment",
    handler_id="payment_link",
    command="send_payment_link",
    result="payment_link_sent",
    tracking="single",
)
CHECK_DEF = ConnectionDef(
    id="payment-check",
    handler_id="payment_check",
    command="check_payment",
    result="payment_status_checked",
    tracking=KeyedByField(keyed_by="link_id"),
)


def jack_engine() -> Any:
    return create_engine(
        reducers=[
            PricingReducer(),
            IssueReducer(),
            TowReducer(),
            TripReducer(),
            ContactReducer(),
            PaymentReducer(),
            CompletionReducer(),
        ]
    )


def booted(engine: Any) -> Any:
    """Initial state with the payment connections folded in."""
    state = engine.initial_state()
    for definition in (PAYMENT_DEF, CHECK_DEF):
        state = engine.step(state, ConnectionAdded(connection=definition)).state
    return state


def fold(engine: Any, state: Any, *events: Any) -> Any:
    """Fold events in order, returning the last step result (state + actions)."""
    result = None
    for event in events:
        result = engine.step(state, event)
        state = result.state
    assert result is not None
    return result
```

Note: `PaymentReducer` does not exist until Task 4. For this task, create it in `reducers.py` as a stub with `slice = "payment"`, `initial = PaymentState()` per the Task 4 dataclass, `reduce` returning state unchanged, and `emit` returning `[]` — Task 4 replaces the stub with real logic. Define `PaymentState` (Task 4 step 3's dataclass) now so the stub type-checks; Task 4 fills in behavior only.

- [ ] **Step 2: Write the failing tests**

`tests/test_fact_reducers.py`:

```python
from jack.vocabulary import (
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    LocationsRecorded,
    PollTick,
    PricingConfigured,
    TowJudged,
)
from rigging import booted, jack_engine


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/test_fact_reducers.py -v` — Expected: FAIL (no `jack.reducers`).

- [ ] **Step 4: Write `src/jack/reducers.py`** (fact reducers + payment stub)

```python
"""Pure reducers: jack's state as a fold over the event log.

Fact reducers hold the latest recorded fact (or None). ``PaymentReducer``
(the interesting one) owns the payment lifecycle; see its docstring.
All reducers are pure — no I/O, clocks, or randomness (rig core rules).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from rig.core import CommandRejected

from jack.vocabulary import (
    CheckPayment,
    ContactRecorded,
    IntakeCompleted,
    IssueRecorded,
    LocationsRecorded,
    PaymentLinkSent,
    PaymentStatusChecked,
    PollTick,
    PricingConfigured,
    SendPaymentLink,
    TowJudged,
)

MAX_LINK_ATTEMPTS = 2


class _LatestFactReducer:
    """Holds the most recent instance of one event class, or None."""

    slice: str
    event_class: type

    def reduce(self, state: Any, event: Any) -> Any:
        if isinstance(event, self.event_class):
            return event
        return state

    initial: Any = None


class PricingReducer(_LatestFactReducer):
    slice = "pricing"
    event_class = PricingConfigured


class IssueReducer(_LatestFactReducer):
    slice = "issue"
    event_class = IssueRecorded


class TowReducer(_LatestFactReducer):
    slice = "tow"
    event_class = TowJudged


class TripReducer(_LatestFactReducer):
    slice = "trip"
    event_class = LocationsRecorded


class ContactReducer(_LatestFactReducer):
    slice = "contact"
    event_class = ContactRecorded


class CompletionReducer(_LatestFactReducer):
    slice = "completion"
    event_class = IntakeCompleted


@dataclass(frozen=True)
class PaymentState:
    """The payment slice. ``status`` is None until a link exists, then
    tracks the latest known link status. ``ticks``/``checks`` implement
    tick entitlement (spec §6): each poll_tick entitles one check.
    ``halted`` records why the standing send request was retired
    ("rejected" or "send_failed"); a new contact_recorded clears it."""

    link_id: str | None = None
    url: str | None = None
    status: str | None = None
    attempts: int = 0
    ticks: int = 0
    checks: int = 0
    halted: str | None = None
    halt_reason: str | None = None


class PaymentReducer:
    """Payment lifecycle. Stub in Task 3; real logic lands in Task 4/5."""

    slice = "payment"
    initial = PaymentState()

    def reduce(self, state: PaymentState, event: Any) -> PaymentState:
        return state

    def emit(self, slices: Mapping[str, Any]) -> list[Any]:
        return []
```

(The unused imports — `CheckPayment`, `SendPaymentLink`, `PaymentLinkSent`, `PaymentStatusChecked`, `PollTick`, `CommandRejected`, `replace` — are used by Task 4/5; if ruff complains now, add them in Task 4 instead of importing early.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_fact_reducers.py -v` — Expected: PASS.

- [ ] **Step 6: `make ci`, commit**

```bash
git add -A && git commit -m "feat: fact reducers and engine test rigging"
```

---

### Task 4: Payment reducer — link lifecycle

**Files:**
- Modify: `src/jack/reducers.py` (replace the `PaymentReducer` stub)
- Test: `tests/test_payment_send.py`

**Interfaces:**
- Consumes: `PaymentState`, fact reducers, `rigging.py` helpers.
- Produces: `PaymentReducer.reduce` handling `PaymentLinkSent`, `PaymentStatusChecked` (fold only; entitlement math in Task 5), `CommandRejected`, `ContactRecorded`, `PollTick`; `emit` producing `SendPaymentLink` under the spec §6 preconditions. Later tasks rely on exactly these emit conditions.

- [ ] **Step 1: Write the failing tests**

`tests/test_payment_send.py`:

```python
from rig.core import CommandRejected

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
from rigging import booted, jack_engine

PRICING = PricingConfigured(amount_cents=15000)
TOW_YES = TowJudged(appropriate=True, reason="undrivable")
TOW_NO = TowJudged(appropriate=False, reason="just needs fuel")
TRIP = LocationsRecorded(pickup="5th and Main", dropoff="Joe's Garage")
CONTACT = ContactRecorded(phone="555-123-4567")
ALL_FACTS = (PRICING, TOW_YES, TRIP, CONTACT)
LINK_OK = PaymentLinkSent(link_id="link-1", url="https://pay.example/link-1", amount_cents=15000)


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
    for n in range(1, MAX_LINK_ATTEMPTS + 1):
        state = engine.step(
            state,
            PaymentLinkSent(
                link_id=f"link-{n}", url=f"https://pay.example/link-{n}", amount_cents=15000
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_payment_send.py -v` — Expected: FAIL (stub emits nothing / folds nothing; the first, rejection, and rearm tests fail).

- [ ] **Step 3: Replace the `PaymentReducer` stub in `src/jack/reducers.py`**

```python
class PaymentReducer:
    """The payment lifecycle as a fold (spec §6).

    Standing request contract: ``emit`` asks for ``send_payment_link``
    while state wants one — never only on the event that made it wanted.
    The engine's pending mark stops the repeated ask from dispatching
    twice; this reducer never reads those marks.

    A send is wanted when: pricing is configured, the tow is judged
    appropriate, locations and contact are recorded, the request is not
    halted, fewer than ``MAX_LINK_ATTEMPTS`` links exist, and no live
    link is pending or paid. A guard rejection or a send failure halts
    the request (else it would re-ask forever); a fresh
    ``contact_recorded`` clears the halt. Expiry re-arms the send at the
    next attempt number.

    ``check_payment`` emission is tick-entitled: one check per
    ``poll_tick``, only while a link is pending (Task 5).
    """

    slice = "payment"
    initial = PaymentState()

    def reduce(self, state: PaymentState, event: Any) -> PaymentState:
        match event:
            case PaymentLinkSent(status="ok") as sent:
                return PaymentState(
                    link_id=sent.link_id,
                    url=sent.url,
                    status="pending",
                    attempts=state.attempts + 1,
                )
            case PaymentLinkSent(status="error") as sent:
                return replace(
                    state, halted="send_failed", halt_reason=sent.error
                )
            case PaymentStatusChecked() as checked:
                new_status = state.status
                if checked.status in ("pending", "paid", "expired"):
                    new_status = checked.status
                return replace(state, status=new_status, checks=state.checks + 1)
            case CommandRejected(command="send_payment_link", reason=reason):
                return replace(state, halted="rejected", halt_reason=reason)
            case ContactRecorded():
                if state.halted in ("rejected", "send_failed"):
                    return replace(state, halted=None, halt_reason=None)
                return state
            case PollTick():
                return replace(state, ticks=state.ticks + 1)
            case _:
                return state

    def emit(self, slices: Mapping[str, Any]) -> list[Any]:
        payment: PaymentState = slices["payment"]
        out: list[Any] = []
        pricing = slices["pricing"]
        tow = slices["tow"]
        trip = slices["trip"]
        contact = slices["contact"]
        send_wanted = (
            pricing is not None
            and tow is not None
            and tow.appropriate
            and trip is not None
            and contact is not None
            and payment.halted is None
            and payment.attempts < MAX_LINK_ATTEMPTS
            and payment.status in (None, "expired")
        )
        if send_wanted:
            out.append(
                SendPaymentLink(
                    phone=contact.phone,
                    amount_cents=pricing.amount_cents,
                    attempt=payment.attempts + 1,
                )
            )
        if (
            payment.status == "pending"
            and payment.link_id is not None
            and payment.ticks > payment.checks
        ):
            out.append(CheckPayment(link_id=payment.link_id))
        return out
```

Add the now-needed imports (`replace` from dataclasses; `CommandRejected` from `rig.core`; the vocabulary names) if Task 3 deferred them.

Note the expiry/attempt interaction the tests pin down: `attempts` counts successful link creations; a failed send does not increment it (the retry after re-arm is still the same attempt number, so the fake's idempotency returns a fresh try under the same key only if the previous create actually failed — which is exactly right).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_payment_send.py tests/test_fact_reducers.py -v` — Expected: all PASS (`test_paid_stops_everything` and the fold-only parts of Task 5's behavior come along for free; the tick test lives in Task 5).

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: payment reducer link lifecycle with guard-rejection rearm"
```

---

### Task 5: Payment reducer — tick-entitled polling

**Files:**
- Modify: `src/jack/reducers.py` (only if tests reveal gaps — the emit logic landed in Task 4)
- Test: `tests/test_payment_polling.py`

**Interfaces:**
- Consumes: Task 4's `PaymentReducer`.
- Produces: verified tick entitlement semantics that the session tests and CLI rely on.

- [ ] **Step 1: Write the failing tests**

`tests/test_payment_polling.py`:

```python
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
from rigging import booted, jack_engine

FACTS = (
    PricingConfigured(amount_cents=15000),
    TowJudged(appropriate=True, reason="undrivable"),
    LocationsRecorded(pickup="A", dropoff="B"),
    ContactRecorded(phone="555-123-4567"),
)
LINK = PaymentLinkSent(link_id="link-1", url="https://pay.example/link-1", amount_cents=15000)


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
    assert payment.status == "pending"  # unknown result leaves status alone
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
```

- [ ] **Step 2: Run tests**

Run: `poetry run pytest tests/test_payment_polling.py -v` — Expected: mostly PASS from Task 4's implementation; fix any failures in `reducers.py` (the likely gap: `PaymentLinkSent(status="ok")` must reset `ticks`/`checks` to 0 — Task 4's `reduce` constructs a fresh `PaymentState`, which does this; verify).

- [ ] **Step 3: `make ci`, commit**

```bash
git add -A && git commit -m "test: tick-entitled polling semantics pinned"
```

---

### Task 6: Payment services — protocols and the file-backed fake

**Files:**
- Create: `src/jack/services.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: nothing from jack (stdlib + asyncio only).
- Produces:
  - `class PaymentLinkService(Protocol)` with `async def create_link(self, phone: str, amount_cents: int, idempotency_key: str) -> tuple[str, str]` (returns `(link_id, url)`)
  - `class PaymentStatusService(Protocol)` with `async def status(self, link_id: str) -> str` (returns `"pending" | "paid" | "expired"`)
  - `class FakePaymentFailure(RuntimeError)`
  - `class FakePaymentService(state_path, *, latency=0.0, fail_creates=0, expire_after_checks=None)` implementing both protocols, plus `mark_paid(link_id)`, `mark_latest_paid() -> str | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_services.py`:

```python
from pathlib import Path

import pytest

from jack.services import FakePaymentFailure, FakePaymentService


async def test_create_link_is_idempotent_per_key(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    first = await fake.create_link("555-1", 15000, "call:1")
    again = await fake.create_link("555-1", 15000, "call:1")
    other = await fake.create_link("555-1", 15000, "call:2")
    assert first == again
    assert other != first


async def test_state_survives_a_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "pay.json"
    fake = FakePaymentService(path)
    link_id, _ = await fake.create_link("555-1", 15000, "call:1")
    fake.mark_paid(link_id)

    reopened = FakePaymentService(path)
    assert await reopened.status(link_id) == "paid"


async def test_status_lifecycle_pending_then_paid(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    link_id, url = await fake.create_link("555-1", 15000, "call:1")
    assert url.endswith(link_id)
    assert await fake.status(link_id) == "pending"
    fake.mark_paid(link_id)
    assert await fake.status(link_id) == "paid"


async def test_mark_latest_paid_targets_the_newest_pending_link(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    await fake.create_link("555-1", 15000, "call:1")
    second, _ = await fake.create_link("555-1", 15000, "call:2")
    assert fake.mark_latest_paid() == second
    assert await fake.status(second) == "paid"


def test_mark_latest_paid_with_no_links_returns_none(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    assert fake.mark_latest_paid() is None


async def test_fail_creates_injects_failures_then_recovers(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json", fail_creates=1)
    with pytest.raises(FakePaymentFailure):
        await fake.create_link("555-1", 15000, "call:1")
    link_id, _ = await fake.create_link("555-1", 15000, "call:1")
    assert link_id


async def test_expire_after_checks(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json", expire_after_checks=2)
    link_id, _ = await fake.create_link("555-1", 15000, "call:1")
    assert await fake.status(link_id) == "pending"
    assert await fake.status(link_id) == "pending"
    assert await fake.status(link_id) == "expired"


async def test_unknown_link_raises(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    with pytest.raises(KeyError):
        await fake.status("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_services.py -v` — Expected: FAIL (no module).

- [ ] **Step 3: Write `src/jack/services.py`**

```python
"""Payment service boundary (spec §7).

Two small protocols the payment handlers depend on, and a fake that
implements both. The fake is file backed so it works across processes:
``jack pay`` in a second terminal and a resumed session both see the
same links. Real Twilio/Stripe implementations are later classes behind
the same protocols; nothing else in jack changes.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol

LinkStatus = Literal["pending", "paid", "expired"]


class PaymentLinkService(Protocol):
    """Creates a payment link and delivers it to the phone by SMS.

    At-least-once contract: ``idempotency_key`` must make repeats safe —
    the same key returns the same link, never a second charge."""

    async def create_link(
        self, phone: str, amount_cents: int, idempotency_key: str
    ) -> tuple[str, str]: ...


class PaymentStatusService(Protocol):
    """Reports a link's status. Naturally idempotent."""

    async def status(self, link_id: str) -> str: ...


class FakePaymentFailure(RuntimeError):
    """Injected failure from the fake, for exercising error paths."""


class FakePaymentService:
    """File-backed fake implementing both protocols.

    Knobs: ``latency`` (seconds added to every call), ``fail_creates``
    (the next N create_link calls raise), ``expire_after_checks`` (a
    pending link flips to expired on the Nth status call). Knobs are
    process-local; the link table persists at ``state_path``."""

    def __init__(
        self,
        state_path: Path,
        *,
        latency: float = 0.0,
        fail_creates: int = 0,
        expire_after_checks: int | None = None,
    ) -> None:
        self._path = state_path
        self._latency = latency
        self._fail_creates = fail_creates
        self._expire_after_checks = expire_after_checks

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"links": {}, "order": []}
        return json.loads(self._path.read_text())

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    async def create_link(
        self, phone: str, amount_cents: int, idempotency_key: str
    ) -> tuple[str, str]:
        if self._latency:
            await asyncio.sleep(self._latency)
        if self._fail_creates > 0:
            self._fail_creates -= 1
            raise FakePaymentFailure("injected create_link failure")
        data = self._load()
        digest = hashlib.sha1(idempotency_key.encode()).hexdigest()[:8]
        link_id = f"link-{digest}"
        if link_id not in data["links"]:
            data["links"][link_id] = {
                "phone": phone,
                "amount_cents": amount_cents,
                "status": "pending",
                "checks": 0,
            }
            data["order"].append(link_id)
            self._save(data)
        url = f"https://pay.example/{link_id}"
        return link_id, url

    async def status(self, link_id: str) -> str:
        if self._latency:
            await asyncio.sleep(self._latency)
        data = self._load()
        link = data["links"][link_id]  # KeyError on unknown link is deliberate
        link["checks"] += 1
        if (
            link["status"] == "pending"
            and self._expire_after_checks is not None
            and link["checks"] >= self._expire_after_checks
        ):
            link["status"] = "expired"
        self._save(data)
        return link["status"]

    def mark_paid(self, link_id: str) -> None:
        data = self._load()
        data["links"][link_id]["status"] = "paid"
        self._save(data)

    def mark_latest_paid(self) -> str | None:
        data = self._load()
        for link_id in reversed(data["order"]):
            if data["links"][link_id]["status"] == "pending":
                self.mark_paid(link_id)
                return link_id
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_services.py -v` — Expected: PASS.

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: payment service protocols and file-backed fake"
```

---

### Task 7: Payment handlers and the error mapper

**Files:**
- Create: `src/jack/payments.py`
- Test: `tests/test_payment_handlers.py`

**Interfaces:**
- Consumes: `FakePaymentService` / protocols from Task 6, vocabulary from Task 2, `rig.runtime.HandlerBase`, `rig.runtime.DispatchContext`, `rig.runtime` `default_error_result` (import from `rig.runtime`; if not exported there, import from `rig.runtime.handler`).
- Produces:
  - `SendPaymentLinkHandler(service: PaymentLinkService, call_id: str)` — claims `send_payment_link` → `payment_link_sent`, single tracking.
  - `CheckPaymentHandler(service: PaymentStatusService)` — claims `check_payment` → `payment_status_checked`, keyed by `link_id`.
  - `jack_error_result(command, error)` — maps failures on jack's commands to error result events, delegating everything else to rig's default. Passed to `Session.open(error_result=...)` in Task 11.

- [ ] **Step 1: Write the failing tests**

`tests/test_payment_handlers.py`:

```python
from pathlib import Path

from rig.runtime import DispatchContext

from jack.payments import CheckPaymentHandler, SendPaymentLinkHandler, jack_error_result
from jack.services import FakePaymentFailure, FakePaymentService
from jack.vocabulary import CheckPayment, SendPaymentLink

CTX = DispatchContext()


async def test_send_creates_a_link_and_reports_it(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    handler = SendPaymentLinkHandler(fake, call_id="call-1")
    result = await handler.dispatch(
        SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=1), CTX
    )
    assert result.type == "payment_link_sent"
    assert result.status == "ok"
    assert result.link_id and result.url and result.amount_cents == 15000


async def test_send_is_idempotent_per_attempt_but_fresh_per_retry(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    handler = SendPaymentLinkHandler(fake, call_id="call-1")
    command = SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=1)
    first = await handler.dispatch(command, CTX)
    redispatched = await handler.dispatch(command, CTX)  # at-least-once replay
    assert redispatched.link_id == first.link_id
    second_attempt = await handler.dispatch(
        SendPaymentLink(phone="555-123-4567", amount_cents=15000, attempt=2), CTX
    )
    assert second_attempt.link_id != first.link_id


async def test_check_mirrors_the_link_id(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    link_id, _ = await fake.create_link("555-123-4567", 15000, "call-1:1")
    handler = CheckPaymentHandler(fake)
    result = await handler.dispatch(CheckPayment(link_id=link_id), CTX)
    assert result.type == "payment_status_checked"
    assert result.link_id == link_id  # the key mirror
    assert result.status == "pending"


def test_error_mapper_covers_jack_commands() -> None:
    boom = FakePaymentFailure("boom")
    send_error = jack_error_result(
        SendPaymentLink(phone="555-1", amount_cents=15000, attempt=1), boom
    )
    assert send_error.type == "payment_link_sent"
    assert send_error.status == "error"
    assert "boom" in send_error.error

    check_error = jack_error_result(CheckPayment(link_id="link-9"), boom)
    assert check_error.type == "payment_status_checked"
    assert check_error.status == "error"
    assert check_error.link_id == "link-9"  # mirror survives failure


def test_error_mapper_delegates_standard_commands() -> None:
    from rig.core import CallModel

    result = jack_error_result(CallModel(messages=[]), RuntimeError("x"))
    assert result.type == "model_response"
    assert result.status == "error"
```

Note: if `CallModel(messages=[])` fails validation, construct the minimal valid `CallModel` (check `rig.core.vocabulary`) — the assertion is what matters.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_payment_handlers.py -v` — Expected: FAIL (no module).

- [ ] **Step 3: Write `src/jack/payments.py`**

```python
"""Handlers for jack's payment commands, and the error mapper.

At-least-once: a resumed session may re-dispatch a logged command whose
effect already ran. ``SendPaymentLinkHandler`` passes (call id, attempt)
as the idempotency key so a replayed dispatch returns the same link and
never charges twice; a genuine retry after expiry carries a higher
attempt and gets a fresh link (spec §6).

Handlers do not catch service exceptions: the session routes any raise
through ``jack_error_result``, which returns the paired result event
with ``status="error"`` — so a handler crash can never leave a command
permanently in flight, and the reducer folds the failure like any other
fact.
"""

from typing import Any

from rig.runtime import DispatchContext, HandlerBase, default_error_result

from jack.services import PaymentLinkService, PaymentStatusService
from jack.vocabulary import PaymentLinkSent, PaymentStatusChecked


class SendPaymentLinkHandler(HandlerBase):
    """Creates a payment link and delivers it by SMS via the service."""

    command = "send_payment_link"
    result = "payment_link_sent"

    def __init__(self, service: PaymentLinkService, call_id: str) -> None:
        self._service = service
        self._call_id = call_id

    async def dispatch(self, command: Any, context: DispatchContext) -> Any:
        key = f"{self._call_id}:{command.attempt}"
        link_id, url = await self._service.create_link(
            command.phone, command.amount_cents, key
        )
        return PaymentLinkSent(
            link_id=link_id, url=url, amount_cents=command.amount_cents
        )


class CheckPaymentHandler(HandlerBase):
    """Asks the service for a link's status. Keyed by ``link_id``; the
    key mirror copies it from the command to the result verbatim."""

    command = "check_payment"
    result = "payment_status_checked"
    keyed_by = "link_id"

    def __init__(self, service: PaymentStatusService) -> None:
        self._service = service

    async def dispatch(self, command: Any, context: DispatchContext) -> Any:
        status = await self._service.status(command.link_id)
        return PaymentStatusChecked(link_id=command.link_id, status=status)


def jack_error_result(command: Any, error: BaseException) -> Any:
    """Error mapper for jack's vocabulary (rig handler contract §6.1):
    converts a handler exception into the paired error result so the
    pending mark always clears. Standard commands fall through to rig's
    default mapper."""
    message = f"{type(error).__name__}: {error}"
    match command.type:
        case "send_payment_link":
            return PaymentLinkSent(status="error", error=message)
        case "check_payment":
            return PaymentStatusChecked(
                link_id=command.link_id, status="error", error=message
            )
        case _:
            return default_error_result(command, error)
```

If `default_error_result` is not exported from `rig.runtime`, import it as `from rig.runtime.handler import default_error_result` and add a friction log entry ("error mapper composition requires a private import").

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_payment_handlers.py -v` — Expected: PASS.

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: payment handlers with idempotent sends and error mapper"
```

---

### Task 8: Prompts

**Files:**
- Create: `src/jack/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: nothing.
- Produces (every model-visible string in jack, spec §8):
  - `DEFAULT_MODEL = "claude-opus-5"`, `SMOKE_MODEL = "claude-haiku-4-5"`
  - `format_price(amount_cents: int, currency: str) -> str`
  - `instructions(amount_cents: int, currency: str = "USD") -> str`
  - `PAYMENT_CONFIRMED_NOTICE: str`
  - `link_rejected_notice(reason: str) -> str`, `link_failed_notice(reason: str) -> str`
  - `link_expired_notice(final: bool) -> str`
  - `TOOL_DESCRIPTIONS: dict[str, str]`, `TOOL_PARAMETERS: dict[str, dict]`, `TOOL_ACKS: dict[str, str]` — keys: `record_issue`, `judge_tow`, `record_locations`, `record_contact`, `complete_intake`.

- [ ] **Step 1: Write the failing tests**

`tests/test_prompts.py`:

```python
from jack import prompts

TOOL_NAMES = {
    "record_issue",
    "judge_tow",
    "record_locations",
    "record_contact",
    "complete_intake",
}


def test_instructions_carry_the_formatted_price() -> None:
    text = prompts.instructions(15000)
    assert "$150.00" in text


def test_format_price() -> None:
    assert prompts.format_price(15000, "USD") == "$150.00"
    assert prompts.format_price(989, "USD") == "$9.89"
    assert prompts.format_price(15000, "EUR") == "150.00 EUR"


def test_tool_tables_agree_on_names() -> None:
    assert set(prompts.TOOL_DESCRIPTIONS) == TOOL_NAMES
    assert set(prompts.TOOL_PARAMETERS) == TOOL_NAMES
    assert set(prompts.TOOL_ACKS) == TOOL_NAMES


def test_notices_mention_what_the_model_must_do() -> None:
    assert "complete_intake" in prompts.PAYMENT_CONFIRMED_NOTICE
    assert "bad phone" in prompts.link_rejected_notice("bad phone")
    assert "again" in prompts.link_failed_notice("timeout")
    assert "abandoned" in prompts.link_expired_notice(final=True)
    assert "new link" in prompts.link_expired_notice(final=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_prompts.py -v` — Expected: FAIL.

- [ ] **Step 3: Write `src/jack/prompts.py`**

```python
"""Every string the model can see, as a named injectable parameter.

rig's rule (design doc §9.1): prompts, tool descriptions, notices, and
model choice are the most-tuned parts of a harness, so none of them may
be a literal inside a reducer, handler, or the CLI. Change wording here;
nothing else moves.
"""

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
            "summary": {"type": "string", "description": "What is wrong, in one sentence."},
            "vehicle": {"type": "string", "description": "The vehicle, e.g. '2015 Honda Civic'."},
        },
        "required": ["summary", "vehicle"],
    },
    "judge_tow": {
        "type": "object",
        "properties": {
            "appropriate": {"type": "boolean", "description": "Whether a tow is warranted."},
            "reason": {"type": "string", "description": "One-sentence justification."},
        },
        "required": ["appropriate", "reason"],
    },
    "record_locations": {
        "type": "object",
        "properties": {
            "pickup": {"type": "string", "description": "Where the vehicle is now."},
            "dropoff": {"type": "string", "description": "Where it should be towed."},
        },
        "required": ["pickup", "dropoff"],
    },
    "record_contact": {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "description": "The customer's mobile number."},
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_prompts.py -v` — Expected: PASS.

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: prompts module holding every model-visible string"
```

---

### Task 9: Intake tool handler

**Files:**
- Create: `src/jack/tools.py`
- Test: `tests/test_intake_tools.py`

**Interfaces:**
- Consumes: prompts tables (Task 8), vocabulary (Task 2), `rig.core` `KeyedBy`, `TextPart`, `ToolResult`, `ToolSchema`, `HarnessError`; `rig.runtime` `DispatchContext`, `HandlerDescription`, `HandlerPair`.
- Produces: `IntakeToolsHandler()` claiming `execute_tool` → `tool_result` keyed by `call_id`, declaring the five tools; `dispatch` returns `[tool_result, domain_event]` (the paired result plus one extra event — the extra is what reducers fold).

- [ ] **Step 1: Write the failing tests**

`tests/test_intake_tools.py`:

```python
from rig.core import ExecuteTool
from rig.runtime import DispatchContext

from jack.tools import IntakeToolsHandler

CTX = DispatchContext()


def call(name: str, args: dict, call_id: str = "t1") -> ExecuteTool:
    return ExecuteTool(call_id=call_id, name=name, args=args)


def test_describe_declares_five_tools_keyed_by_call_id() -> None:
    description = IntakeToolsHandler().describe()
    (pair,) = description.pairs
    assert (pair.command, pair.result) == ("execute_tool", "tool_result")
    assert {t.name for t in description.tools} == {
        "record_issue",
        "judge_tow",
        "record_locations",
        "record_contact",
        "complete_intake",
    }
    for tool in description.tools:
        assert tool.description
        assert tool.parameters["type"] == "object"


async def test_dispatch_returns_ack_plus_domain_event() -> None:
    handler = IntakeToolsHandler()
    result = await handler.dispatch(
        call("record_issue", {"summary": "dead battery", "vehicle": "Civic"}), CTX
    )
    ack, event = result
    assert ack.type == "tool_result"
    assert ack.call_id == "t1"
    assert ack.status == "ok"
    assert event.type == "issue_recorded"
    assert event.summary == "dead battery"


async def test_every_tool_maps_to_its_event() -> None:
    handler = IntakeToolsHandler()
    cases = {
        "judge_tow": ({"appropriate": True, "reason": "undrivable"}, "tow_judged"),
        "record_locations": ({"pickup": "A", "dropoff": "B"}, "locations_recorded"),
        "record_contact": ({"phone": "555-123-4567"}, "contact_recorded"),
        "complete_intake": ({"outcome": "paid"}, "intake_completed"),
    }
    for name, (args, event_type) in cases.items():
        _, event = await handler.dispatch(call(name, args), CTX)
        assert event.type == event_type


async def test_unknown_tool_is_an_error_result_not_a_raise() -> None:
    handler = IntakeToolsHandler()
    result = await handler.dispatch(call("warp_drive", {}), CTX)
    assert result.type == "tool_result"
    assert result.status == "error"
    assert result.call_id == "t1"


async def test_invalid_args_are_an_error_result_not_a_raise() -> None:
    handler = IntakeToolsHandler()
    result = await handler.dispatch(call("record_contact", {"phon": "555"}), CTX)
    assert result.type == "tool_result"
    assert result.status == "error"
    assert "phone" in result.error.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_intake_tools.py -v` — Expected: FAIL.

- [ ] **Step 3: Write `src/jack/tools.py`**

```python
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
                    tracking=KeyedBy("call_id"),
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_intake_tools.py -v` — Expected: PASS. If the `[ack, event]` list return is rejected anywhere, stop and check `rig.runtime.handler.Handler` docs again — and if the surface genuinely doesn't support extras from a tool handler, add a friction log entry and surface the conflict rather than working around it (spec §7).

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: intake tool handler raising domain events as extras"
```

---

### Task 10: Payment policy guard

**Files:**
- Create: `src/jack/guards.py`
- Test: `tests/test_guard.py`

**Interfaces:**
- Consumes: `rig.core` `GuardCheck`, `GuardVerdict`; `rig.runtime` `DispatchContext`, `HandlerBase`.
- Produces: `PaymentPolicyGuard()` claiming `guard_check` → `guard_verdict` keyed by `check_id`; reads `context.config["amount_cents"]`; exports `PHONE_RE`.

- [ ] **Step 1: Write the failing tests**

`tests/test_guard.py`:

```python
from rig.core import GuardCheck
from rig.runtime import DispatchContext

from jack.guards import PaymentPolicyGuard

CONFIG_CTX = DispatchContext(config={"amount_cents": 15000})


def check(subject: dict) -> GuardCheck:
    return GuardCheck(
        check_id="7:payment-policy:outbound",
        subject_type="send_payment_link",
        subject=subject,
        direction="outbound",
    )


async def test_valid_command_is_approved() -> None:
    verdict = await PaymentPolicyGuard().dispatch(
        check({"phone": "555-123-4567", "amount_cents": 15000, "attempt": 1}),
        CONFIG_CTX,
    )
    assert verdict.verdict == "approve"
    assert verdict.check_id == "7:payment-policy:outbound"


async def test_amount_mismatch_is_rejected_with_a_reason() -> None:
    verdict = await PaymentPolicyGuard().dispatch(
        check({"phone": "555-123-4567", "amount_cents": 999, "attempt": 1}),
        CONFIG_CTX,
    )
    assert verdict.verdict == "reject"
    assert "amount" in verdict.reason


async def test_implausible_phone_is_rejected() -> None:
    for phone in ("", "n/a", "call me maybe", "12"):
        verdict = await PaymentPolicyGuard().dispatch(
            check({"phone": phone, "amount_cents": 15000, "attempt": 1}), CONFIG_CTX
        )
        assert verdict.verdict == "reject", f"accepted {phone!r}"
        assert "phone" in verdict.reason


async def test_plausible_phone_formats_are_accepted() -> None:
    for phone in ("555-123-4567", "+1 (555) 123-4567", "5551234567"):
        verdict = await PaymentPolicyGuard().dispatch(
            check({"phone": phone, "amount_cents": 15000, "attempt": 1}), CONFIG_CTX
        )
        assert verdict.verdict == "approve", f"rejected {phone!r}"


async def test_missing_config_rejects_rather_than_approves() -> None:
    verdict = await PaymentPolicyGuard().dispatch(
        check({"phone": "555-123-4567", "amount_cents": 15000, "attempt": 1}),
        DispatchContext(),
    )
    assert verdict.verdict == "reject"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_guard.py -v` — Expected: FAIL.

- [ ] **Step 3: Write `src/jack/guards.py`**

```python
"""Outbound policy on the payment connection (spec §6).

The guard re-derives nothing from conversation state: it checks the
command against its own connection config, so the amount must agree
between two independent sources — reducer state (which built the
command from the pricing slice) and the guard's config. Fail closed:
missing config is a rejection, never an approval.
"""

import re
from typing import Any

from rig.core import GuardCheck, GuardVerdict
from rig.runtime import DispatchContext, HandlerBase

PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s().]{6,19}$")


class PaymentPolicyGuard(HandlerBase):
    """Approves a send_payment_link whose amount matches the configured
    price and whose phone is plausibly a mobile number; rejects
    otherwise, with the reasons joined into one message."""

    command = "guard_check"
    result = "guard_verdict"
    keyed_by = "check_id"

    async def dispatch(
        self, command: GuardCheck, context: DispatchContext
    ) -> GuardVerdict:
        subject: dict[str, Any] = command.subject
        problems: list[str] = []
        expected = context.config.get("amount_cents")
        if expected is None:
            problems.append("guard has no configured amount")
        elif subject.get("amount_cents") != expected:
            problems.append(
                f"amount {subject.get('amount_cents')} does not match "
                f"the configured amount {expected}"
            )
        phone = subject.get("phone") or ""
        if not PHONE_RE.match(phone):
            problems.append(f"phone {phone!r} does not look like a mobile number")
        if problems:
            return GuardVerdict(
                check_id=command.check_id,
                verdict="reject",
                direction=command.direction,
                reason="; ".join(problems),
            )
        return GuardVerdict(
            check_id=command.check_id,
            verdict="approve",
            direction=command.direction,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_guard.py -v` — Expected: PASS.

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: payment policy guard checking config against command"
```

---

### Task 11: Session wiring and the scripted happy path

**Files:**
- Create: `src/jack/session.py`, `tests/support/scripted.py`
- Test: `tests/test_session_happy_path.py`

**Interfaces:**
- Consumes: everything so far.
- Produces:
  - `build_session(*, log_path: Path, payments_path: Path, call_id: str, model_handler, amount_cents: int | None = None, currency: str = "USD", payment_service=None, instructions_text: str | None = None) -> Session` — opens fresh when the log is missing/empty (requires `amount_cents`), resumes otherwise (passes no connections and no init; the log is the only truth). Returns the `Session`; the caller sends `PricingConfigured` on fresh calls.
  - Test support: `ScriptedModelHandler(script)`, `tool_call(name, args, call_id)`, `text_response(text)` in `tests/support/scripted.py`.
- Note: tool names the scripted model calls are namespaced `intake__<bare_name>` — the connection id prefix is part of the name the model sees.

- [ ] **Step 1: Write the scripted-model support**

`tests/support/scripted.py`:

```python
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
```

- [ ] **Step 2: Write `src/jack/session.py`**

```python
"""Session assembly: reducers, handlers, connections, and the log.

Fresh log: full wiring plus ``SessionInit`` carrying the instructions.
Existing log: the log is the only truth — no connections, no init; the
handler registry alone must satisfy every logged ``handler_id``
(``model``, ``intake``, ``payment_link``, ``payment_check``,
``payment_policy``). Renaming a registry key is therefore a breaking
change for existing call logs.
"""

from pathlib import Path
from typing import Any

from rig.adapters.jsonl import JsonlEventLog
from rig.core import GuardBinding, SessionInit, agent_reducers
from rig.runtime import Connection, Session

from jack import prompts
from jack.guards import PaymentPolicyGuard
from jack.payments import CheckPaymentHandler, SendPaymentLinkHandler, jack_error_result
from jack.reducers import (
    CompletionReducer,
    ContactReducer,
    IssueReducer,
    PaymentReducer,
    PricingReducer,
    TowReducer,
    TripReducer,
)
from jack.services import FakePaymentService
from jack.tools import IntakeToolsHandler
from jack.vocabulary import JackCommand, JackEvent


async def build_session(
    *,
    log_path: Path,
    payments_path: Path,
    call_id: str,
    model_handler: Any,
    amount_cents: int | None = None,
    currency: str = "USD",
    payment_service: Any = None,
    instructions_text: str | None = None,
) -> Session:
    service = payment_service or FakePaymentService(payments_path)
    resuming = log_path.exists() and log_path.stat().st_size > 0
    log = JsonlEventLog(log_path, events=JackEvent, commands=JackCommand)
    handlers = {
        "model": model_handler,
        "intake": IntakeToolsHandler(),
        "payment_link": SendPaymentLinkHandler(service, call_id),
        "payment_check": CheckPaymentHandler(service),
        "payment_policy": PaymentPolicyGuard(),
    }
    reducers = [
        *agent_reducers(),
        PricingReducer(),
        IssueReducer(),
        TowReducer(),
        TripReducer(),
        ContactReducer(),
        PaymentReducer(),
        CompletionReducer(),
    ]
    if resuming:
        return await Session.open(
            reducers=reducers,
            handlers=handlers,
            log=log,
            error_result=jack_error_result,
        )
    if amount_cents is None:
        raise ValueError("amount_cents is required for a fresh call")
    connections = [
        Connection(id="model", handler="model"),
        Connection(id="intake", handler="intake"),
        Connection(
            id="payment",
            handler="payment_link",
            guards=(
                GuardBinding(connection_id="payment-policy", direction="outbound"),
            ),
        ),
        Connection(id="payment-check", handler="payment_check"),
        Connection(
            id="payment-policy",
            handler="payment_policy",
            config={"amount_cents": amount_cents},
        ),
    ]
    init = SessionInit(
        instructions=instructions_text or prompts.instructions(amount_cents, currency)
    )
    return await Session.open(
        reducers=reducers,
        handlers=handlers,
        connections=connections,
        log=log,
        init=init,
        error_result=jack_error_result,
    )
```

- [ ] **Step 3: Write the failing happy-path test**

`tests/test_session_happy_path.py`:

```python
from pathlib import Path

from scripted import ScriptedModelHandler, text_response, tool_call

from jack import prompts
from jack.services import FakePaymentService
from jack.session import build_session
from jack.vocabulary import PollTick, PricingConfigured

INTAKE_SCRIPT = [
    tool_call("intake__record_issue", {"summary": "engine died", "vehicle": "Civic"}, "c1"),
    tool_call("intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"),
    tool_call("intake__record_locations", {"pickup": "5th and Main", "dropoff": "Joe's Garage"}, "c3"),
    tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c4"),
    text_response("A payment link is on its way to your phone."),
]
WRAP_UP_SCRIPT = [
    tool_call("intake__complete_intake", {"outcome": "paid"}, "c5"),
    text_response("You're all set. Goodbye!"),
]


async def drain(gen) -> list:
    return [event async for event in gen]


async def open_call(tmp_path: Path, script: list):
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    return session


async def test_full_intake_reaches_paid(tmp_path: Path) -> None:
    session = await open_call(tmp_path, [*INTAKE_SCRIPT, *WRAP_UP_SCRIPT])

    events = await drain(session.send("My car died on 5th and Main"))
    types = [e.type for e in events]
    # facts recorded, link sent within the same turn, guarded first
    assert "issue_recorded" in types
    assert "tow_judged" in types
    assert "locations_recorded" in types
    assert "contact_recorded" in types
    assert "guard_verdict" in types
    assert "payment_link_sent" in types

    payment = session.state.slices["payment"]
    assert payment.status == "pending"
    link_id = payment.link_id

    # first poll: still pending
    await session.run(PollTick())
    assert session.state.slices["payment"].status == "pending"

    # customer pays out of band
    fake = FakePaymentService(tmp_path / "pay.json")
    fake.mark_paid(link_id)
    await session.run(PollTick())
    assert session.state.slices["payment"].status == "paid"

    # notice closes the loop
    await drain(session.send(prompts.PAYMENT_CONFIRMED_NOTICE))
    completion = session.state.slices["completion"]
    assert completion is not None and completion.outcome == "paid"


async def test_write_order_command_check_verdict_result(tmp_path: Path) -> None:
    session = await open_call(tmp_path, list(INTAKE_SCRIPT))
    await drain(session.send("My car died"))
    entries = await session.log.load()
    shape = [
        (e.kind, e.event.type if e.kind == "event" else e.command.type)
        for e in entries
    ]
    i_send = shape.index(("command", "send_payment_link"))
    i_check = shape.index(("command", "guard_check"))
    i_verdict = shape.index(("event", "guard_verdict"))
    i_sent = shape.index(("event", "payment_link_sent"))
    assert i_send < i_check < i_verdict < i_sent
```

- [ ] **Step 4: Run tests, fix wiring until green**

Run: `poetry run pytest tests/test_session_happy_path.py -v`
Expected failures to debug in order: import errors, then script alignment (the number of `call_model` rounds must match the script — one model response per tool call plus the closing text; add or remove `text_response` entries only after reading the logged `call_model` sequence with a quick debug print of `shape`).

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: session wiring; scripted happy path to paid"
```

---

### Task 12: Replay equality and kill/resume

**Files:**
- Test: `tests/test_replay_and_resume.py`

**Interfaces:**
- Consumes: Task 11's `build_session`, scripted support, `rigging.jack_engine`.

- [ ] **Step 1: Write the failing tests**

`tests/test_replay_and_resume.py`:

```python
from pathlib import Path

from rig.core import agent_reducers, create_engine
from scripted import ScriptedModelHandler, text_response, tool_call

from jack import prompts
from jack.reducers import (
    CompletionReducer,
    ContactReducer,
    IssueReducer,
    PaymentReducer,
    PricingReducer,
    TowReducer,
    TripReducer,
)
from jack.services import FakePaymentService
from jack.session import build_session
from jack.vocabulary import JackCommand, JackEvent, PollTick, PricingConfigured
from rig.adapters.jsonl import JsonlEventLog

INTAKE_SCRIPT = [
    tool_call("intake__record_issue", {"summary": "engine died", "vehicle": "Civic"}, "c1"),
    tool_call("intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"),
    tool_call("intake__record_locations", {"pickup": "A", "dropoff": "B"}, "c3"),
    tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c4"),
    text_response("Link sent."),
]


def all_reducers():
    return [
        *agent_reducers(),
        PricingReducer(),
        IssueReducer(),
        TowReducer(),
        TripReducer(),
        ContactReducer(),
        PaymentReducer(),
        CompletionReducer(),
    ]


async def run_to_pending_link(tmp_path: Path):
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(list(INTAKE_SCRIPT)),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    async for _ in session.send("My car died"):
        pass
    await session.run(PollTick())
    return session


async def test_replay_matches_live_state(tmp_path: Path) -> None:
    session = await run_to_pending_link(tmp_path)
    entries = await session.log.load()
    events = [e.event for e in entries if e.kind == "event"]
    replayed = create_engine(reducers=all_reducers()).replay(events)
    assert replayed.slices == session.state.slices
    assert replayed.pending == session.state.pending


async def test_resume_folds_identical_state_and_completes_the_call(
    tmp_path: Path,
) -> None:
    session = await run_to_pending_link(tmp_path)
    live_slices = dict(session.state.slices)
    link_id = session.state.slices["payment"].link_id
    del session  # the process "dies"

    resumed = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(
            [
                tool_call("intake__complete_intake", {"outcome": "paid"}, "c9"),
                text_response("Goodbye!"),
            ]
        ),
        amount_cents=None,  # resume needs no price: the log is the only truth
    )
    assert dict(resumed.state.slices) == live_slices

    FakePaymentService(tmp_path / "pay.json").mark_paid(link_id)
    await resumed.run(PollTick())
    assert resumed.state.slices["payment"].status == "paid"

    async for _ in resumed.send(prompts.PAYMENT_CONFIRMED_NOTICE):
        pass
    assert resumed.state.slices["completion"].outcome == "paid"


async def test_resume_after_kill_between_send_and_result_does_not_double_charge(
    tmp_path: Path,
) -> None:
    """Simulate a crash window: the send_payment_link command is logged
    but its result is not. Resume must re-dispatch and, thanks to the
    (call id, attempt) idempotency key, land on the same link."""
    session = await run_to_pending_link(tmp_path)
    first_link = session.state.slices["payment"].link_id
    entries = await session.log.load()
    # rebuild a truncated log: everything up to and including the
    # send_payment_link command, nothing after
    cut = next(
        i
        for i, e in enumerate(entries)
        if e.kind == "command" and e.command.type == "send_payment_link"
    )
    truncated_path = tmp_path / "truncated.jsonl"
    truncated = JsonlEventLog(
        truncated_path, events=JackEvent, commands=JackCommand
    )
    for entry in entries[: cut + 1]:
        if entry.kind == "event":
            await truncated.append_event(entry.event)
        else:
            await truncated.append_command(
                entry.command, connection_id=entry.connection_id
            )

    resumed = await build_session(
        log_path=truncated_path,
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler([text_response("Link sent.")]),
    )
    # rig resumes in-flight commands; drive one turn so redispatch runs.
    await resumed.run(PollTick())
    assert resumed.state.slices["payment"].link_id == first_link
```

Notes for the implementer:
- If `JsonlEventLog` append method names differ, mirror whatever Task 2 discovered.
- The third test depends on how rig resumes in-flight commands (redispatch mode and when it triggers). Read `rig/runtime/session.py`'s resume docstrings and `docs/tutorials/07-kill-and-resume.md` first; adapt the "drive one turn" line to the real resume API (there may be an explicit resume mode argument). The assertion — same link, no second charge — is the point; keep it.
- If constructing the truncated log via appends re-validates in ways that block command appends, fall back to copying the first N lines of the raw JSONL file with `Path.read_text().splitlines()` — the log is line-delimited JSON.

- [ ] **Step 2: Run, adapt to rig's actual resume API, get green**

Run: `poetry run pytest tests/test_replay_and_resume.py -v`

- [ ] **Step 3: `make ci`, commit**

```bash
git add -A && git commit -m "test: replay equality and kill/resume, including the no-double-charge window"
```

---

### Task 13: Alternate paths — guard rejection and no-tow

**Files:**
- Test: `tests/test_session_alternate_paths.py`

**Interfaces:**
- Consumes: Task 11's wiring and scripted support.

- [ ] **Step 1: Write the failing tests**

`tests/test_session_alternate_paths.py`:

```python
from pathlib import Path

from scripted import ScriptedModelHandler, text_response, tool_call

from jack import prompts
from jack.session import build_session
from jack.vocabulary import PricingConfigured


async def drain(gen) -> list:
    return [event async for event in gen]


async def test_bad_phone_is_rejected_then_corrected(tmp_path: Path) -> None:
    script = [
        tool_call("intake__record_issue", {"summary": "dead", "vehicle": "Civic"}, "c1"),
        tool_call("intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"),
        tool_call("intake__record_locations", {"pickup": "A", "dropoff": "B"}, "c3"),
        tool_call("intake__record_contact", {"phone": "not a number"}, "c4"),
        text_response("Hmm, let me check that number."),
        # second round: the notice arrives, model re-records the contact
        tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c5"),
        text_response("Link sent."),
    ]
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))

    events = await drain(session.send("My car died"))
    types = [e.type for e in events]
    assert "command_rejected" in types
    assert "payment_link_sent" not in types
    payment = session.state.slices["payment"]
    assert payment.halted == "rejected"
    assert "phone" in payment.halt_reason

    events = await drain(
        session.send(prompts.link_rejected_notice(payment.halt_reason))
    )
    types = [e.type for e in events]
    assert "contact_recorded" in types
    assert "payment_link_sent" in types
    assert session.state.slices["payment"].status == "pending"


async def test_no_tow_needed_completes_without_payment(tmp_path: Path) -> None:
    script = [
        tool_call("intake__record_issue", {"summary": "out of fuel", "vehicle": "Civic"}, "c1"),
        tool_call("intake__judge_tow", {"appropriate": False, "reason": "fuel delivery"}, "c2"),
        tool_call("intake__complete_intake", {"outcome": "no_tow_needed"}, "c3"),
        text_response("A fuel truck is a better fit — goodbye!"),
    ]
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    await drain(session.send("I ran out of gas"))

    assert session.state.slices["completion"].outcome == "no_tow_needed"
    payment = session.state.slices["payment"]
    assert payment.link_id is None and payment.attempts == 0


async def test_injected_send_failure_maps_to_send_failed_halt(tmp_path: Path) -> None:
    """Fault injection through the whole stack (spec §10): the fake raises
    on create_link, the session routes the raise through jack_error_result,
    the reducer folds the error result and halts the standing request."""
    from jack.services import FakePaymentService

    script = [
        tool_call("intake__record_issue", {"summary": "dead", "vehicle": "Civic"}, "c1"),
        tool_call("intake__judge_tow", {"appropriate": True, "reason": "undrivable"}, "c2"),
        tool_call("intake__record_locations", {"pickup": "A", "dropoff": "B"}, "c3"),
        tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c4"),
        text_response("One moment."),
        # after the failure notice, the model re-records the contact to retry
        tool_call("intake__record_contact", {"phone": "555-123-4567"}, "c5"),
        text_response("Link sent."),
    ]
    failing = FakePaymentService(tmp_path / "pay.json", fail_creates=1)
    session = await build_session(
        log_path=tmp_path / "call.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="call-t",
        model_handler=ScriptedModelHandler(script),
        amount_cents=15000,
        payment_service=failing,
    )
    await session.run(PricingConfigured(amount_cents=15000))

    events = await drain(session.send("My car died"))
    types = [e.type for e in events]
    assert "payment_link_sent" in types  # the error-status result event
    payment = session.state.slices["payment"]
    assert payment.halted == "send_failed"
    assert "FakePaymentFailure" in payment.halt_reason
    assert payment.attempts == 0

    events = await drain(
        session.send(prompts.link_failed_notice(payment.halt_reason))
    )
    assert session.state.slices["payment"].status == "pending"
```

(Add `from jack.vocabulary import PollTick, PricingConfigured` and adjust imports as needed; `PollTick` is unused here — import only `PricingConfigured`.)

- [ ] **Step 2: Run, align scripts with actual turn structure, get green**

Run: `poetry run pytest tests/test_session_alternate_paths.py -v`

- [ ] **Step 3: `make ci`, commit**

```bash
git add -A && git commit -m "test: guard rejection recovery and no-tow completion paths"
```

---

### Task 14: The CLI

**Files:**
- Create: `src/jack/cli.py`
- Test: `tests/test_cli_helpers.py`

**Interfaces:**
- Consumes: `build_session`, `FakePaymentService`, prompts, `PaymentState`, `MAX_LINK_ATTEMPTS`.
- Produces: console script `jack` with subcommands `new`, `resume <call-id>`, `pay <call-id>`; pure helpers `awaiting_payment(slices) -> bool`, `pending_notice(slices, sent: set[tuple]) -> tuple[tuple, str] | None`, `new_call_id(now: datetime) -> str`.

- [ ] **Step 1: Write the failing helper tests**

`tests/test_cli_helpers.py`:

```python
from datetime import datetime

from jack.cli import awaiting_payment, new_call_id, pending_notice
from jack.reducers import MAX_LINK_ATTEMPTS, PaymentState
from jack.vocabulary import IntakeCompleted


def slices(payment: PaymentState, completion=None) -> dict:
    return {"payment": payment, "completion": completion}


def test_new_call_id_is_a_sortable_slug() -> None:
    assert new_call_id(datetime(2026, 8, 23, 14, 5, 9)) == "20260823-140509"


def test_awaiting_payment_only_while_a_link_is_pending() -> None:
    assert not awaiting_payment(slices(PaymentState()))
    assert awaiting_payment(slices(PaymentState(link_id="l1", status="pending")))
    assert not awaiting_payment(slices(PaymentState(link_id="l1", status="paid")))


def test_paid_notice_fires_once() -> None:
    sent: set = set()
    state = slices(PaymentState(link_id="l1", status="paid", attempts=1))
    first = pending_notice(state, sent)
    assert first is not None
    key, text = first
    assert "complete_intake" in text
    sent.add(key)
    assert pending_notice(state, sent) is None


def test_no_notice_after_completion() -> None:
    state = slices(
        PaymentState(link_id="l1", status="paid", attempts=1),
        completion=IntakeCompleted(outcome="paid"),
    )
    assert pending_notice(state, set()) is None


def test_rejected_and_failed_notices_carry_the_reason() -> None:
    rejected = slices(
        PaymentState(halted="rejected", halt_reason="phone 'x' does not look right")
    )
    key, text = pending_notice(rejected, set())
    assert "phone 'x' does not look right" in text

    failed = slices(PaymentState(halted="send_failed", halt_reason="timeout"))
    key, text = pending_notice(failed, set())
    assert "timeout" in text


def test_expired_notice_is_final_only_at_the_attempt_cap() -> None:
    mid = slices(PaymentState(link_id="l1", status="expired", attempts=1))
    key, text = pending_notice(mid, set())
    assert "new link" in text

    final = slices(
        PaymentState(link_id="l2", status="expired", attempts=MAX_LINK_ATTEMPTS)
    )
    key, text = pending_notice(final, set())
    assert "abandoned" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_cli_helpers.py -v` — Expected: FAIL.

- [ ] **Step 3: Write `src/jack/cli.py`**

```python
"""The terminal call loop: jack new / resume / pay (spec §9).

The CLI owns everything rig core cannot: the clock (poll ticks), stdin,
and the "state to conversation" notices. All notice text comes from
``jack.prompts``. The loop is deliberately thin — every decision it
makes is derived from slices through the pure helpers below, which is
what the tests cover.
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

from rig.adapters.anthropic import anthropic_handler

from jack import prompts
from jack.reducers import MAX_LINK_ATTEMPTS, PaymentState
from jack.services import FakePaymentService
from jack.session import build_session
from jack.vocabulary import PollTick, PricingConfigured

DEFAULT_PRICE_CENTS = 15000
DEFAULT_POLL_INTERVAL = 3.0


def new_call_id(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def awaiting_payment(slices: Any) -> bool:
    payment: PaymentState = slices["payment"]
    return payment.status == "pending" and payment.link_id is not None


def pending_notice(
    slices: Any, sent: set[tuple]
) -> tuple[tuple, str] | None:
    """The next notice the model needs, or None. ``sent`` dedupes: each
    (kind, attempts) pair is injected once per call."""
    if slices.get("completion") is not None:
        return None
    payment: PaymentState = slices["payment"]
    if payment.status == "paid":
        key = ("paid", payment.attempts)
        if key not in sent:
            return key, prompts.PAYMENT_CONFIRMED_NOTICE
    if payment.halted == "rejected":
        key = ("rejected", payment.attempts, payment.halt_reason)
        if key not in sent:
            return key, prompts.link_rejected_notice(payment.halt_reason or "")
    if payment.halted == "send_failed":
        key = ("send_failed", payment.attempts, payment.halt_reason)
        if key not in sent:
            return key, prompts.link_failed_notice(payment.halt_reason or "")
    if payment.status == "expired":
        final = payment.attempts >= MAX_LINK_ATTEMPTS
        key = ("expired", payment.attempts, final)
        if key not in sent:
            return key, prompts.link_expired_notice(final=final)
    return None


def _print_delta(delta: Any) -> None:
    if delta.kind == "text":
        print(delta.text, end="", flush=True)
    elif delta.kind == "tool_call" and delta.name is not None:
        print(f"\n[{delta.name}]", flush=True)


def _show(event: Any) -> None:
    if event.type == "payment_link_sent" and event.status == "ok":
        print(f"\n[payment link sent: {event.url}]")
    elif event.type == "command_rejected":
        print(f"\n[send rejected: {event.reason}]")
    elif event.type == "payment_status_checked":
        print(f"\n[payment status: {event.status}]", flush=True)


async def _drain(session: Any, message: Any) -> None:
    async for event in session.send(message, on_delta=_print_delta):
        _show(event)
    print()


async def _stdin_reader() -> asyncio.StreamReader:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader


async def _run_call(
    session: Any, fake: FakePaymentService, poll_interval: float
) -> None:
    reader = await _stdin_reader()
    sent_notices: set[tuple] = set()
    print("(you are the customer; type your side of the call. 'p' pays the link)")
    while True:
        slices = session.state.slices
        completion = slices["completion"]
        if completion is not None:
            print(f"[call complete: {completion.outcome}]")
            return
        notice = pending_notice(slices, sent_notices)
        if notice is not None:
            key, text = notice
            sent_notices.add(key)
            await _drain(session, text)
            continue
        if awaiting_payment(slices):
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=poll_interval)
            except TimeoutError:
                await session.run(PollTick())
                continue
        else:
            raw = await reader.readline()
        if not raw:
            print("[stdin closed; call parked — resume with: jack resume]")
            return
        line = raw.decode().strip()
        if not line:
            continue
        if line == "p" and awaiting_payment(slices):
            link_id = slices["payment"].link_id
            fake.mark_paid(link_id)
            print(f"[marked {link_id} paid]")
            continue
        await _drain(session, line)


def _paths(calls_dir: Path, call_id: str) -> tuple[Path, Path]:
    return calls_dir / f"{call_id}.jsonl", calls_dir / f"{call_id}.payments.json"


async def _new(args: argparse.Namespace) -> None:
    call_id = new_call_id(datetime.now())
    log_path, payments_path = _paths(args.calls_dir, call_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fake = FakePaymentService(payments_path)
    client = anthropic.AsyncAnthropic()
    session = await build_session(
        log_path=log_path,
        payments_path=payments_path,
        call_id=call_id,
        model_handler=anthropic_handler(client, model=args.model),
        amount_cents=args.price,
        payment_service=fake,
    )
    await session.run(PricingConfigured(amount_cents=args.price))
    print(f"[call {call_id}]")
    await _run_call(session, fake, args.poll_interval)


async def _resume(args: argparse.Namespace) -> None:
    log_path, payments_path = _paths(args.calls_dir, args.call_id)
    if not log_path.exists():
        raise SystemExit(f"no such call: {args.call_id}")
    fake = FakePaymentService(payments_path)
    client = anthropic.AsyncAnthropic()
    session = await build_session(
        log_path=log_path,
        payments_path=payments_path,
        call_id=args.call_id,
        model_handler=anthropic_handler(client, model=args.model),
        payment_service=fake,
    )
    print(f"[resumed call {args.call_id}]")
    await _run_call(session, fake, args.poll_interval)


def _pay(args: argparse.Namespace) -> None:
    _, payments_path = _paths(args.calls_dir, args.call_id)
    link_id = FakePaymentService(payments_path).mark_latest_paid()
    if link_id is None:
        raise SystemExit("no pending link to pay")
    print(f"[marked {link_id} paid]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jack")
    parser.add_argument("--calls-dir", type=Path, default=Path("calls"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="start a new intake call")
    new.add_argument("--price", type=int, default=DEFAULT_PRICE_CENTS)
    new.add_argument("--model", default=prompts.DEFAULT_MODEL)
    new.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)

    resume = sub.add_parser("resume", help="reopen a call after a kill")
    resume.add_argument("call_id")
    resume.add_argument("--model", default=prompts.DEFAULT_MODEL)
    resume.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)

    pay = sub.add_parser("pay", help="fake paying the latest pending link")
    pay.add_argument("call_id")

    args = parser.parse_args()
    if args.cmd == "pay":
        _pay(args)
    elif args.cmd == "new":
        asyncio.run(_new(args))
    else:
        asyncio.run(_resume(args))
```

(Adapt `_print_delta`/`_show` field access to the real delta and event shapes if they differ — check `rig` deltas in `docs/tutorials/02-a-real-agent.md` step 5; on Python < 3.11 `asyncio.TimeoutError` vs `TimeoutError` matters, but we require 3.12.)

- [ ] **Step 4: Run helper tests, then a manual smoke without a network**

Run: `poetry run pytest tests/test_cli_helpers.py -v` — Expected: PASS.
Manual check (no API key needed): `poetry run jack pay nonexistent` → exits with "no pending link to pay" after creating nothing. Do not manually run `jack new` here; the live smoke task covers real-model behavior.

- [ ] **Step 5: `make ci`, commit**

```bash
git add -A && git commit -m "feat: terminal call loop with poll ticks, notices, and resume"
```

---

### Task 15: Live smoke and wrap-up

**Files:**
- Create: `tests/live/__init__.py` (empty), `tests/live/test_smoke.py`
- Modify: `README.md` (add the live smoke instructions), `docs/friction-log.md` (harvest anything discovered during Tasks 11–14 that wasn't logged yet)

**Interfaces:**
- Consumes: everything.

- [ ] **Step 1: Write the gated smoke test**

`tests/live/test_smoke.py`:

```python
"""One live smoke behind an env flag (spec §10): a short scripted
customer reaches payment_link_sent against the real API on the cheapest
served model, with the fake payment service."""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("JACK_LIVE_SMOKE"),
    reason="set JACK_LIVE_SMOKE=1 (and ANTHROPIC_API_KEY) to run",
)

CUSTOMER_LINES = [
    "Hi, my car's engine just died on the highway, it won't restart at all. "
    "It's a 2015 Honda Civic.",
    "I'm at the corner of 5th and Main in Springfield. Please tow it to "
    "Joe's Garage on Elm Street. Yes, I want the tow.",
    "My mobile number is 555-123-4567. Go ahead.",
]


async def test_live_intake_reaches_a_payment_link(tmp_path: Path) -> None:
    import anthropic
    from rig.adapters.anthropic import anthropic_handler

    from jack import prompts
    from jack.session import build_session
    from jack.vocabulary import PricingConfigured

    client = anthropic.AsyncAnthropic()
    session = await build_session(
        log_path=tmp_path / "smoke.jsonl",
        payments_path=tmp_path / "pay.json",
        call_id="smoke",
        model_handler=anthropic_handler(client, model=prompts.SMOKE_MODEL),
        amount_cents=15000,
    )
    await session.run(PricingConfigured(amount_cents=15000))
    for line in CUSTOMER_LINES:
        async for _ in session.send(line):
            pass
        if session.state.slices["payment"].link_id is not None:
            break

    payment = session.state.slices["payment"]
    assert payment.link_id is not None, (
        "no payment link after the scripted dialogue; inspect "
        f"{tmp_path / 'smoke.jsonl'}"
    )
    assert payment.status == "pending"
```

- [ ] **Step 2: Run the ungated suite, then the smoke once**

Run: `make ci` — Expected: green, smoke skipped.
Run: `JACK_LIVE_SMOKE=1 poetry run pytest tests/live -v` — Expected: PASS (needs `ANTHROPIC_API_KEY`). If the model dawdles (asks extra questions instead of recording facts), tune `prompts.py` wording — never the test's tolerance — and note the tuning in the commit.

- [ ] **Step 3: Harvest the friction log**

Reread the diffs of Tasks 11–14. Anything that was awkward — resume API shape, extras ordering, delta field names, notice injection — gets an entry in `docs/friction-log.md` now if it didn't already.

- [ ] **Step 4: Update `README.md`**

Append:

```markdown
## Live smoke

    JACK_LIVE_SMOKE=1 poetry run pytest tests/live -v

Runs one real conversation on `claude-haiku-4-5` against the fake payment
service. Everything else in the suite is offline.
```

- [ ] **Step 5: Final `make ci`, commit**

```bash
git add -A && git commit -m "test: gated live smoke; friction log harvest; README"
```
