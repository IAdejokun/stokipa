"""Conversation dispatch.

MILESTONE 3: temporary echo implementation to prove the full loop
(webhook -> ingest -> reply) against a real WhatsApp number.

MILESTONE 4 replaces the body of `route_message` with the conversation
state machine. The signature is final — ingest.py never changes again.
"""

import structlog

from app.whatsapp.client import wa

log = structlog.get_logger()


async def route_message(
    wa_id: str, wamid: str, text: str, profile_name: str | None
) -> None:
    name = profile_name or "there"
    await wa.send_text(wa_id, f"Echo, {name}: {text}")