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

    async def status(self, link_id: str) -> LinkStatus: ...


class FakePaymentFailure(RuntimeError):
    """Injected failure from the fake, for exercising error paths."""


class FakePaymentService:
    """File-backed fake implementing both protocols.

    Knobs: ``latency`` (seconds added to every call), ``fail_creates``
    (the next N create_link calls raise), ``expire_after_checks`` (a
    pending link flips to expired on the Nth status call). Knobs are
    process local; the link table persists at ``state_path``."""

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

    async def status(self, link_id: str) -> LinkStatus:
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
