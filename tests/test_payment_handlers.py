from pathlib import Path

from rig.runtime import DispatchContext

from jack.payments import (
    CheckPaymentHandler,
    SendPaymentLinkHandler,
    jack_error_result,
)
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


async def test_send_is_idempotent_per_attempt_but_fresh_per_retry(
    tmp_path: Path,
) -> None:
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
