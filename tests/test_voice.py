"""Milestone 6 tests: voice notes -> transcription -> normal pipeline.

Both external hops are stubbed: wa.download_media (Meta) and the transcriber
(Groq). What we verify is OUR wiring: audio messages produce a transcript,
the transcript is persisted on the Message row, and it routes exactly like
typed text.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Message, User
from app.pipeline import ingest as ingest_mod
from app.pipeline.ingest import ingest_webhook
from app.whatsapp.client import wa

WA_ID = "2348077700011"


@pytest.fixture(autouse=True)
def stub_media_and_stt(monkeypatch):
    async def fake_download(media_id: str):
        return b"OGG_BYTES", "audio/ogg; codecs=opus"

    class FakeTranscriber:
        def __init__(self):
            self.calls: list[tuple[bytes, str]] = []

        async def transcribe(self, audio: bytes, mime_type: str) -> str:
            self.calls.append((audio, mime_type))
            return "I sell 3 bags of rice"

    fake = FakeTranscriber()
    monkeypatch.setattr(wa, "download_media", fake_download)
    monkeypatch.setattr(ingest_mod, "transcriber", fake)
    return fake


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with SessionLocal() as s:
        await s.execute(delete(Message).where(
            Message.raw["from"].astext == WA_ID))
        await s.execute(delete(User).where(User.wa_id == WA_ID))
        await s.commit()


def _audio_payload(wamid: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Voice Tester"}}],
        "messages": [{
            "id": wamid, "from": WA_ID, "type": "audio",
            "audio": {"id": "media123", "mime_type": "audio/ogg; codecs=opus",
                      "voice": True},
        }],
    }}]}]}


async def test_voice_note_transcribed_and_routed(stub_media_and_stt, outbox):
    wamid = f"wamid.voice.{uuid.uuid4()}"
    await ingest_webhook(_audio_payload(wamid))

    # transcriber got the downloaded bytes
    assert stub_media_and_stt.calls == [(b"OGG_BYTES", "audio/ogg; codecs=opus")]

    # transcript persisted on the message row
    async with SessionLocal() as s:
        m = (await s.execute(
            select(Message).where(Message.wamid == wamid))).scalar_one()
    assert m.msg_type == "audio"
    assert m.body == "I sell 3 bags of rice"

    # routed like text: brand-new user -> welcome reply
    assert len(outbox.sent) == 1
    assert "Welcome to Stokipa" in outbox.sent[0]["body"]


async def test_duplicate_voice_note_transcribed_once(stub_media_and_stt, outbox):
    wamid = f"wamid.voice.{uuid.uuid4()}"
    p = _audio_payload(wamid)
    await ingest_webhook(p)
    await ingest_webhook(p)  # Meta retry
    assert len(stub_media_and_stt.calls) == 1  # no double download/transcribe
    assert len(outbox.sent) == 1