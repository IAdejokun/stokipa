"""Milestone 2 tests: signature verification + idempotent ingestion.

Run against the real dev Postgres (DATABASE_URL from .env) so the unique
constraint and UPDATE...RETURNING claim behave exactly as in production.
"""

import asyncio
import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models import Message
from app.pipeline.ingest import ingest_webhook
from app.whatsapp.signature import verify_signature


def _sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.WA_APP_SECRET.encode(), raw, hashlib.sha256
    ).hexdigest()


def _payload(wamid: str, text: str = "hello") -> dict:
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"profile": {"name": "Test Shop"}}],
                    "messages": [{
                        "id": wamid,
                        "from": "2348012345678",
                        "type": "text",
                        "text": {"body": text},
                    }],
                }
            }]
        }]
    }


# ---------------- signature ----------------

def test_signature_valid():
    raw = b'{"a": 1}'
    assert verify_signature(raw, _sign(raw), settings.WA_APP_SECRET)


def test_signature_invalid_secret():
    raw = b'{"a": 1}'
    bad = "sha256=" + hmac.new(b"wrong", raw, hashlib.sha256).hexdigest()
    assert not verify_signature(raw, bad, settings.WA_APP_SECRET)


def test_signature_missing_or_malformed():
    assert not verify_signature(b"x", None, settings.WA_APP_SECRET)
    assert not verify_signature(b"x", "md5=abc", settings.WA_APP_SECRET)


def test_signature_is_over_raw_bytes_not_reserialized_json():
    raw = b'{"a": 1}'
    reserialized = json.dumps(json.loads(raw)).encode()
    sig = _sign(raw)
    if raw != reserialized:
        assert not verify_signature(reserialized, sig, settings.WA_APP_SECRET)
    assert verify_signature(raw, sig, settings.WA_APP_SECRET)


# ---------------- dedupe ----------------

@pytest.fixture
def wamid() -> str:
    return f"wamid.test.{uuid.uuid4()}"


async def _count(wamid: str) -> int:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Message).where(Message.wamid == wamid))).all()
        return len(rows)


async def _cleanup(wamid: str) -> None:
    async with SessionLocal() as s:
        await s.execute(delete(Message).where(Message.wamid == wamid))
        await s.commit()


async def test_single_message_persisted(wamid):
    try:
        await ingest_webhook(_payload(wamid, "I sold 3 bags of rice"))
        async with SessionLocal() as s:
            m = (await s.execute(
                select(Message).where(Message.wamid == wamid)
            )).scalar_one()
        assert m.direction == "IN"
        assert m.msg_type == "text"
        assert m.body == "I sold 3 bags of rice"
        assert m.processed_at is not None
    finally:
        await _cleanup(wamid)


async def test_duplicate_delivery_ignored(wamid):
    try:
        p = _payload(wamid)
        await ingest_webhook(p)
        await ingest_webhook(p)  # Meta retry — must be a no-op
        assert await _count(wamid) == 1
    finally:
        await _cleanup(wamid)


async def test_concurrent_duplicates_processed_once(wamid):
    """Two deliveries of the same wamid racing: exactly one row, one claim."""
    try:
        p = _payload(wamid)
        await asyncio.gather(ingest_webhook(p), ingest_webhook(p))
        assert await _count(wamid) == 1
    finally:
        await _cleanup(wamid)


async def test_crashed_message_is_reclaimable(wamid):
    """A row persisted but never claimed (processed_at NULL) gets reprocessed."""
    try:
        async with SessionLocal() as s:
            s.add(Message(wamid=wamid, direction="IN", msg_type="text",
                          raw={}, processed_at=None))
            await s.commit()

        await ingest_webhook(_payload(wamid, "retry after crash"))

        async with SessionLocal() as s:
            m = (await s.execute(
                select(Message).where(Message.wamid == wamid)
            )).scalar_one()
        assert m.processed_at is not None
        assert m.body == "retry after crash"
    finally:
        await _cleanup(wamid)