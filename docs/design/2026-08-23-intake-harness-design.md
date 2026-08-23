# jack: a roadside assistance intake harness on rig

**Status:** approved design, pre-implementation
**Date:** 2026-08-23
**Depends on:** rig (path dependency on `../rig-py`, MVP + connections layer)

## 1. Purpose

jack is the first real harness built on rig. It handles intake of a new
roadside assistance issue: a customer describes their problem in a
conversation, the agent collects the facts, judges whether a tow is
appropriate, collects pickup and destination locations and a phone
number, sends a payment link to the customer's phone, polls for payment
success, and completes the call.

jack has two deliverables of equal weight:

1. A working intake agent that is the seed of a real product. Decisions
   here should hold up in production; this is not a throwaway exercise.
2. `docs/friction-log.md` — a running record of every place rig fought
   back: missing capability, awkward API, unclear docs, or a pattern
   that had to be invented (like the poll tick pattern in §6). The
   friction log is the input to rig's next roadmap and is updated in
   the same commit as the work that surfaced the entry.

jack is built strictly against rig's public API, from a separate
repository, using only the documentation a product team would have.
When the public surface is insufficient, that is a friction log entry
and possibly a rig change — never a reach into rig internals.

## 2. Decisions already made

- **Terminal chat stands in for the call.** A CLI conversation loop is
  the interaction surface. Telephony and voice can be layered on later
  without changing the harness, because the harness only consumes and
  produces events.
- **Fakes now, sandboxes later.** SMS delivery and payments sit behind
  small protocols (§7). Version one ships fakes with injectable latency
  and failures. Twilio and Stripe test integrations are later
  implementations of the same protocols.
- **Flat tow price; the model judges tow appropriateness.** No quoting
  logic in version one. The price is configuration, folded onto the log
  at session open (§5), and the model decides from the conversation
  whether a tow is warranted.
- **The model converses and extracts; state gates effects.** Typed
  domain events are the source of truth. The payment link cannot be
  sent by model whim — the reducer emits the command only when state
  holds the required facts, and a guard independently checks it.

## 3. Repository shape

Python 3.12, poetry, ruff, ty, pytest + pytest-asyncio (automatic
mode), mirroring rig's conventions. The Makefile is the universal
entrypoint with the same targets as rig: `test`, `lint`, `format`,
`typecheck`, `ci`.

```
pyproject.toml        # rig = {path = "../rig-py", develop = true, extras = ["anthropic"]}
Makefile
docs/
  design/             # this document
  friction-log.md     # deliverable two
src/jack/
  vocabulary.py       # domain events and commands + log unions
  reducers.py         # pure reducers, one per concern
  tools.py            # the intake tool handler the model calls
  payments.py         # SendPaymentLink / CheckPayment handlers
  services.py         # service protocols + fakes
  guards.py           # PaymentPolicyGuard
  prompts.py          # every string the model can see, as named parameters
  session.py          # build_session() wiring
  cli.py              # jack new / jack resume terminal loop
tests/
calls/                # JSONL logs, one per call (gitignored)
```

## 4. Vocabulary

All events and commands are `FrozenModel` subclasses with a literal
`type` field, exactly as rig's tutorials author them.

### Domain events

| Event | Fields | Raised by |
|---|---|---|
| `pricing_configured` | `amount_cents`, `currency` | CLI, as an input event at session open |
| `issue_recorded` | `summary`, `vehicle` | intake tool handler (extra event) |
| `tow_judged` | `appropriate: bool`, `reason` | intake tool handler (extra event) |
| `locations_recorded` | `pickup`, `dropoff` | intake tool handler (extra event) |
| `contact_recorded` | `phone` | intake tool handler (extra event) |
| `payment_link_sent` | `link_id`, `url`, `amount_cents` | payment link handler (result) |
| `payment_status_checked` | `link_id`, `status: pending\|paid\|expired` | payment check handler (result) |
| `poll_tick` | — | CLI, on an interval while a payment is awaited |
| `intake_completed` | `outcome: paid\|no_tow_needed\|abandoned` | intake tool handler (extra event) |

### Commands

| Command | Fields | Tracking | Result event |
|---|---|---|---|
| `send_payment_link` | `phone`, `amount_cents`, `attempt` | single (one link in flight per call) | `payment_link_sent` |
| `check_payment` | `link_id` | keyed by `link_id` (key mirror: copied verbatim onto `payment_status_checked`) | `payment_status_checked` |

### Log vocabulary

`vocabulary.py` exports `JACK_VOCABULARY`, built with rig's
`vocabulary()` from the types above — the built-in unions are always
included, and tag collisions or untagged models fail at build time.
Every model's `type` tag is derived from its class name. The JSONL log
is always constructed with `vocabulary=JACK_VOCABULARY`; a log built
with the defaults cannot reopen a jack call.

## 5. Reducers and state

Small pure reducers, one slice per concern, each testable by feeding
events to the engine:

- **pricing** — holds `(amount_cents, currency)` from
  `pricing_configured`. Reducers cannot read configuration (core is
  pure), so the flat price enters as data on the log: the CLI sends
  `PricingConfigured(...)` as the first input event of a new call. On
  resume it folds back from the log like everything else.
- **issue / tow / trip / contact** — each folds its one recording event
  into a small typed value. No `emit`.
- **payment** — the interesting one; see §6.
- **completion** — folds `intake_completed`; the CLI reads this slice
  to know the call is over.

There is no stored "phase" field. Anything phase-like is derived from
the slices (e.g. "awaiting payment" means a link is sent and status is
still pending).

## 6. The payment flow

### Sending the link

The payment reducer's `emit` asks for `send_payment_link` — a standing
request per rig's contract — while **all** of the following hold in
state: `tow_judged.appropriate` is true, locations are recorded, a
phone is recorded, pricing is configured, and no link has been sent,
rejected, or is in flight. The amount comes from the pricing slice.
The model cannot cause an early or malformed send; the command is not
emittable until the facts exist.

### The guard

`PaymentPolicyGuard` is an ordinary handler behind its own connection,
bound outbound on the payment connection via `GuardBinding`. It checks
the command against its **own** connection config (config rides
`DispatchContext`): the amount must equal the configured price — two
independent sources must agree, reducer state and guard config — and
the phone must plausibly be a mobile number. Any failure rejects with a
reason.

Per the `command_rejected` contract, the payment reducer's `reduce`
retires the standing request on a rejection naming `send_payment_link`
and keeps the reason in the payment slice. The CLI then injects the
`LINK_REJECTED_NOTICE` (§8) so the model can re-collect the phone
number; correcting the phone (a new `contact_recorded`) clears the
rejected state and re-arms the standing request.

### Polling without a clock in core

A naive standing request for `check_payment` would spin: a `pending`
status clears the pending mark, state still wants a check, and the turn
never settles. Core has no clock, so cadence enters as data:

- While the payment slice says "link sent, status pending", the CLI
  sends a `poll_tick` event on an interval (default 3 seconds,
  injectable).
- The payment reducer counts `ticks_seen` (from `poll_tick`) and
  `checks_done` (from `payment_status_checked`). `emit` asks for
  `check_payment(link_id)` only while `ticks_seen > checks_done` and
  status is pending. Each tick entitles exactly one check; each result
  consumes the entitlement. Deterministic, pure, and replayable.

**Friction log, day one:** rig has no first-class timer or scheduling
story; "the shell owns the clock, ticks are events" is a pattern jack
had to invent. Recorded so rig can decide whether to bless or absorb
it.

### Terminal states

`payment_status_checked(paid)` marks the payment done; the CLI injects
`PAYMENT_CONFIRMED_NOTICE` so the model confirms with the customer and
calls `complete_intake(outcome="paid")`. An `expired` status clears the
link from state, which re-arms the send (a fresh link) — the reducer
allows at most two links per call before folding a terminal
`abandoned`-eligible condition the model is told about via
`LINK_EXPIRED_NOTICE`.

### Idempotency

Delivery is at least once. The command carries `attempt` — the payment
slice's count of links created so far plus one — and the handler passes
(call id, attempt) as the idempotency key for link creation, so a
resumed session that re-dispatches `send_payment_link` gets the same
link rather than a second charge, while a genuine retry after expiry
(a higher attempt number) gets a fresh one. `check_payment` is
naturally idempotent.

## 7. Tools and services

### Intake tools

One custom handler (not the generic `toolkit`) claims
`execute_tool`/`tool_result` keyed by `call_id` and declares five
tools, descriptions living in `prompts.py` as overridable named
parameters:

- `record_issue(summary, vehicle)`
- `judge_tow(appropriate, reason)`
- `record_locations(pickup, dropoff)`
- `record_contact(phone)`
- `complete_intake(outcome)`

Each dispatch returns the `tool_result` **plus the corresponding domain
event as an extra event** — the documented handler surface ("the paired
result plus extras", `rig.runtime.handler`). The domain event, not the
tool result text, is what reducers fold. If this surface proves awkward
in practice, that is a friction entry, not a workaround.

### Services

`services.py` defines two protocols:

- `PaymentLinkService.create_link(phone, amount_cents, idempotency_key) -> (link_id, url)`
  (creation and SMS delivery in one call for version one)
- `PaymentStatusService.status(link_id) -> pending|paid|expired`

`FakePaymentService` implements both: in-memory links, a
`mark_paid(link_id)` back door the CLI exposes (press `p` in the poll
loop, or `jack pay <call-id>` from a second terminal), plus injectable
latency, failure rate, and expiry. The payment handlers in
`payments.py` depend only on the protocols; Twilio/Stripe sandbox
implementations later are new classes and one line in `session.py`.

### Model connection

The standard `agent_reducers()` and `anthropic_handler` carry the
conversation. Model id is an injectable parameter of `build_session`;
the default is `claude-opus-5`. The gated live smoke uses
`claude-haiku-4-5`, matching rig's convention.

## 8. Prompts — §9.1 compliance

Every string the model can see is a named parameter in `prompts.py`:

- `INSTRUCTIONS` — the system prompt, a template taking the formatted
  flat price. Describes the intake flow, the tow judgment policy, and
  when to call each tool.
- `PAYMENT_CONFIRMED_NOTICE`, `LINK_REJECTED_NOTICE` (takes the guard's
  reason), `LINK_EXPIRED_NOTICE` — templates the CLI injects as inputs
  when it observes the corresponding state. This "state to conversation
  notice" pattern is itself a friction log candidate: domain events
  have no built-in way to prompt the model.
- Tool descriptions, as a mapping usable with description overrides.

No literal model-facing string appears in a reducer, handler, or the
CLI.

## 9. The CLI

- `jack new` — creates `calls/<call-id>.jsonl` (id is a short
  timestamp slug), opens the session with the full wiring, sends
  `PricingConfigured`, then loops: read stdin, `session.send(...)`
  streaming deltas via `on_delta`, print events of interest.
- While the payment slice says a payment is awaited, the loop
  multiplexes stdin against the poll interval (asyncio): on interval it
  sends `poll_tick`; on input it sends the customer's message. The
  customer can keep talking while polling continues; turns remain
  serial, which is fine at this cadence.
- On observing payment confirmed / link rejected / link expired in
  state, the loop injects the matching notice from `prompts.py` as the
  next input.
- Exits when the completion slice is set, printing the outcome.
- `jack resume <call-id>` — reopens the log with no `connections` and
  no `init`; the log is the only truth. Kill and resume is the daily
  workflow, not just a test.
- `jack pay <call-id>` — the fake's back door, for a second terminal.

## 10. Testing

Same layered style as rig, no mocks where purity allows:

- **Engine tests per reducer** — feed events, assert slices and
  emitted commands. Cover: the send preconditions (each missing fact
  suppresses the emit), tick entitlement (no tick, no check; one tick,
  one check; no spin on pending), rejection retiring the standing
  request and re-arming on corrected contact, expiry re-arming the
  send, the two-link cap.
- **Guard tests** — approve, reject on amount mismatch, reject on bad
  phone; write order (command, check, verdict, then result or
  rejection) asserted from the log.
- **Replay equality** — run a scripted call over a real JSONL log,
  fold the events through a fresh engine, assert slices and pending
  match live state. Also reopen the log and assert the resumed session
  folds identical state.
- **Full-turn tests** — `ScriptedModelHandler` (rig tutorial pattern)
  drives a complete intake without the network: happy path to `paid`,
  the no-tow path, the guard rejection path, kill between link send
  and payment then resume and complete.
- **Fault injection** — fake service latency/failure knobs exercise
  handler error results and at-least-once behavior (re-dispatch after
  resume creates no second link).
- **Live smoke** — one test behind an environment flag on
  `claude-haiku-4-5`: a short scripted customer dialogue reaches
  `payment_link_sent` against the real API with the fake services.

## 11. Out of scope for version one

Telephony/voice, real SMS and payment providers (the protocol boundary
is the extent of the preparation), quoting and pricing logic beyond the
flat price, multi-call concurrency in one process, and any persistence
beyond JSONL. Each becomes work only after the intake loop is real and
the friction log has had its first harvest.
