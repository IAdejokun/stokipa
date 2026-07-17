"""Milestone 3 tests: WhatsApp client behavior via httpx.MockTransport.

No real network. Verifies: success + audit row, retry on 5xx, no retry on
4xx, OutsideServiceWindowError on code 131047, echo routing end-to-end.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Message, User
from app.pipeline.ingest import ingest_webhook
from app.whatsapp.client import (
    OutsideServiceWindowError,
    WhatsAppClient,
    WhatsAppError,
)


def _ok_send_response(wamid: str) -> httpx.Response:
    return httpx.Response(200, json={
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": "2348012345678"}],
        "messages": [{"id": wamid}],
    })


async def _cleanup(wamid: str) -> None:
    async with SessionLocal() as s:
        await s.execute(delete(Message).where(Message.wamid == wamid))
        await s.execute(delete(User).where(User.wa_id == "2348011122233"))
        await s.commit()


async def test_send_text_success_and_audit_row():
    wamid = f"wamid.client.{uuid.uuid4()}"
    transport = httpx.MockTransport(lambda req: _ok_send_response(wamid))
    client = WhatsAppClient(transport=transport)
    try:
        out = await client.send_text("2348012345678", "hello")
        assert out == wamid
        async with SessionLocal() as s:
            m = (await s.execute(
                select(Message).where(Message.wamid == wamid)
            )).scalar_one()
        assert m.direction == "OUT"
        assert m.body == "hello"
    finally:
        await _cleanup(wamid)
        await client.aclose()


async def test_retries_on_500_then_succeeds():
    wamid = f"wamid.client.{uuid.uuid4()}"
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="server error")
        return _ok_send_response(wamid)

    client = WhatsAppClient(transport=httpx.MockTransport(handler))
    try:
        out = await client.send_text("234", "retry me")
        assert out == wamid
        assert calls["n"] == 2
    finally:
        await _cleanup(wamid)
        await client.aclose()


async def test_no_retry_on_400():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text='{"error":{"message":"bad request"}}')

    client = WhatsAppClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(WhatsAppError):
            await client.send_text("234", "x")
        assert calls["n"] == 1  # 4xx must not be retried
    finally:
        await client.aclose()


async def test_outside_service_window_raises_distinct_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=json.dumps(
            {"error": {"code": 131047, "message": "Re-engagement message"}}
        ))

    client = WhatsAppClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(OutsideServiceWindowError):
            await client.send_text("234", "x")
    finally:
        await client.aclose()


async def test_button_titles_truncated_to_20_chars():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(json.loads(req.content))
        return _ok_send_response(f"wamid.client.{uuid.uuid4()}")

    client = WhatsAppClient(transport=httpx.MockTransport(handler))
    try:
        await client.send_confirm_buttons(
            "234", "confirm?", "Y" * 30, "N" * 30
        )
        buttons = captured["interactive"]["action"]["buttons"]
        assert len(buttons[0]["reply"]["title"]) == 20
        assert len(buttons[1]["reply"]["title"]) == 20
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Message).where(Message.direction == "OUT",
                                                  Message.body == "confirm?"))
            await s.commit()
        await client.aclose()


# ------------- full loop (ingest -> router -> wa stub) -------------
# Since milestone 4 the router runs the onboarding state machine: a brand-new
# user's first message triggers the welcome reply.

async def test_reply_sent_for_inbound_text(outbox):
    wamid = f"wamid.echo.{uuid.uuid4()}"
    payload = {
        "entry": [{"changes": [{"value": {
            "contacts": [{"profile": {"name": "Mama Nkechi"}}],
            "messages": [{
                "id": wamid, "from": "2348011122233",
                "type": "text", "text": {"body": "good morning"},
            }],
        }}]}]
    }
    try:
        await ingest_webhook(payload)
        assert len(outbox.sent) == 1
        assert outbox.sent[0]["to"] == "2348011122233"
        assert "Welcome to Stokipa" in outbox.sent[0]["body"]
    finally:
        await _cleanup(wamid)


async def test_duplicate_inbound_produces_single_reply(outbox):
    wamid = f"wamid.echo.{uuid.uuid4()}"
    payload = {
        "entry": [{"changes": [{"value": {
            "messages": [{
                "id": wamid, "from": "2348011122233",
                "type": "text", "text": {"body": "hi"},
            }],
        }}]}]
    }
    try:
        await ingest_webhook(payload)
        await ingest_webhook(payload)  # Meta retry
        assert len(outbox.sent) == 1   # exactly one reply, not two
    finally:
        await _cleanup(wamid)