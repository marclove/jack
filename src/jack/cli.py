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


def pending_notice(slices: Any, sent: set[tuple]) -> tuple[tuple, str] | None:
    """The next notice the model needs, or None. ``sent`` dedupes: each
    key is injected once per call."""
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
    await session.resume(mode="redispatch")
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
