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
