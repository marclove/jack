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

## 2026-08-23 — Reducer protocol requires methods the docs call optional

Tutorial 03 says a reducer "may define two methods, and both are
optional", and the runtime tolerates a missing `emit`. But the
`Reducer` protocol in `rig.core.protocols` requires both members, so a
fold-only reducer fails `ty` when passed to `create_engine`. jack adds
an empty `emit` to every fact reducer to satisfy the checker. Either
the protocol should split (or default) the optional members, or the
docs should stop calling them optional.

## 2026-08-23 — resume() is a separate, easy-to-forget step

`Session.open` on an existing log rebuilds state, including the pending
marks for in-flight commands — but nothing re-dispatches them until you
drain `session.resume(mode=...)`, an async generator like `send`. jack's
CLI initially reopened the log and went straight to the input loop, which
would strand any command caught by a crash window forever. The tutorials
cover this, but the API makes the wrong thing quiet: a session with
non-empty `pending` and no `resume()` call just sits there. Consider
having `Session.open` fail loudly (or warn) when a session with in-flight
commands starts taking new turns before resume.

## 2026-08-23 — domain state cannot prompt the model

When payment is confirmed (a domain event), the model needs to be told so it
can wrap up the call. Nothing in rig turns state into a model-visible input;
jack's CLI watches slices and injects named notice messages. Fine for one
harness, but "state change → tell the model" seems universal enough that rig
may want a first-class hook.
