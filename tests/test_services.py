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


async def test_mark_latest_paid_targets_the_newest_pending_link(
    tmp_path: Path,
) -> None:
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
    assert await fake.status(link_id) == "expired"


async def test_unknown_link_raises(tmp_path: Path) -> None:
    fake = FakePaymentService(tmp_path / "pay.json")
    with pytest.raises(KeyError):
        await fake.status("nope")
