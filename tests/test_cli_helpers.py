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
    notice = pending_notice(rejected, set())
    assert notice is not None
    assert "phone 'x' does not look right" in notice[1]

    failed = slices(PaymentState(halted="send_failed", halt_reason="timeout"))
    notice = pending_notice(failed, set())
    assert notice is not None
    assert "timeout" in notice[1]


def test_expired_notice_is_final_only_at_the_attempt_cap() -> None:
    mid = slices(PaymentState(link_id="l1", status="expired", attempts=1))
    notice = pending_notice(mid, set())
    assert notice is not None
    assert "new link" in notice[1]

    final = slices(
        PaymentState(link_id="l2", status="expired", attempts=MAX_LINK_ATTEMPTS)
    )
    notice = pending_notice(final, set())
    assert notice is not None
    assert "abandoned" in notice[1]
