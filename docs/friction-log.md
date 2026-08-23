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
