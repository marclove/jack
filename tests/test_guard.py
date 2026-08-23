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
    assert verdict.reason is not None
    assert "amount" in verdict.reason


async def test_implausible_phone_is_rejected() -> None:
    for phone in ("", "n/a", "call me maybe", "12"):
        verdict = await PaymentPolicyGuard().dispatch(
            check({"phone": phone, "amount_cents": 15000, "attempt": 1}), CONFIG_CTX
        )
        assert verdict.verdict == "reject", f"accepted {phone!r}"
        assert verdict.reason is not None
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
