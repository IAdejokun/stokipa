"""Shared test fixtures.

`stub_wa` (autouse) replaces the send methods on the `wa` singleton so no
test ever performs real network I/O to Meta. Both ingest.py and router.py
imported the same object, so patching its bound methods covers every caller.
Tests that want to assert on outbound messages use the `outbox` fixture.
"""

import pytest

from app.whatsapp.client import wa


class Outbox:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, to: str, body: str) -> str:
        self.sent.append({"kind": "text", "to": to, "body": body})
        return f"wamid.out.{len(self.sent)}"

    async def send_confirm_buttons(
        self, to: str, body: str, yes_label: str, no_label: str
    ) -> str:
        self.sent.append({
            "kind": "buttons", "to": to, "body": body,
            "yes": yes_label, "no": no_label,
        })
        return f"wamid.out.{len(self.sent)}"


@pytest.fixture(autouse=True)
def stub_wa(monkeypatch) -> Outbox:
    box = Outbox()
    monkeypatch.setattr(wa, "send_text", box.send_text)
    monkeypatch.setattr(wa, "send_confirm_buttons", box.send_confirm_buttons)
    return box


@pytest.fixture
def outbox(stub_wa: Outbox) -> Outbox:
    return stub_wa