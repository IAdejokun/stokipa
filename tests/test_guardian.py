"""Guardian layer tests: invite/consent flow, quiet-shop alerts, weekly digest.

LLM untouched except confirmation (buttons used); Meta stubbed via conftest.
"""

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select
from unittest.mock import patch

from app.db import SessionLocal
from app.jobs.scheduler import run_digest_tick, run_quiet_tick
from app.llm import service
from app.llm.tools import Intent
from app.models import ConvoState, GuardianLink, Item, Message, User
from app.pipeline.ingest import ingest_webhook

LAGOS = ZoneInfo("Africa/Lagos")


@pytest.fixture
def owner_wa() -> str:
    return "23483" + uuid.uuid4().hex[:8]


@pytest.fixture
def guardian_wa() -> str:
    return "23484" + uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
async def cleanup(owner_wa, guardian_wa):
    yield
    async with SessionLocal() as s:
        for wid in (owner_wa, guardian_wa):
            u = (await s.execute(
                select(User).where(User.wa_id == wid))).scalar_one_or_none()
            if u:
                await s.execute(delete(GuardianLink).where(
                    GuardianLink.owner_id == u.id))
                await s.execute(delete(Item).where(Item.user_id == u.id))
                await s.execute(delete(User).where(User.id == u.id))
            await s.execute(delete(Message).where(
                Message.raw["from"].astext == wid))
        await s.commit()


async def _mk_owner(owner_wa: str, **kw) -> int:
    defaults = dict(wa_id=owner_wa, shop_name="Mama Nkechi Provisions",
                    language="pcm", convo_state=ConvoState.IDLE,
                    checkin_hour=20)
    defaults.update(kw)
    async with SessionLocal() as s:
        u = User(**defaults)
        s.add(u)
        await s.commit()
        return u.id


def _inbound(wa_id: str, text: str, name: str = "Someone") -> dict:
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": name}}],
        "messages": [{
            "id": f"wamid.g.{uuid.uuid4()}", "from": wa_id,
            "type": "text", "text": {"body": text},
        }],
    }}]}]}


def _button(wa_id: str, button_id: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "messages": [{
            "id": f"wamid.g.{uuid.uuid4()}", "from": wa_id,
            "type": "interactive",
            "interactive": {"button_reply": {"id": button_id, "title": "x"}},
        }],
    }}]}]}


async def _link(owner_id: int) -> GuardianLink:
    async with SessionLocal() as s:
        return (await s.execute(select(GuardianLink).where(
            GuardianLink.owner_id == owner_id))).scalar_one()


async def test_full_link_flow(monkeypatch, outbox, owner_wa, guardian_wa):
    owner_id = await _mk_owner(owner_wa)

    async def intent(text):
        return Intent(type="add_guardian", language="pcm")
    monkeypatch.setattr(service, "classify_intent", intent)

    # 1. Owner asks -> code delivered
    await ingest_webhook(_inbound(owner_wa, "I wan add my daughter as guardian"))
    body = outbox.sent[-1]["body"]
    assert "GUARD-" in body
    code = "GUARD-" + body.split("GUARD-")[1][:6].strip("*").strip()

    # 2. Guardian (new number) sends the code -> owner gets consent buttons
    await ingest_webhook(_inbound(guardian_wa, f"my code is {code}", "Ada"))
    kinds = [m["kind"] for m in outbox.sent[-2:]]
    assert "buttons" in kinds
    buttons_msg = [m for m in outbox.sent if m["kind"] == "buttons"][-1]
    assert buttons_msg["to"] == owner_wa
    assert "Ada" in buttons_msg["body"]
    # guardian did NOT fall into shop onboarding
    assert not any("Welcome to Stokipa" in m["body"]
                   for m in outbox.sent if m["to"] == guardian_wa)

    # 3. Owner approves -> ACTIVE, both notified
    await ingest_webhook(_button(owner_wa, "confirm_yes"))
    link = await _link(owner_id)
    assert link.status == "ACTIVE"
    assert link.guardian_wa_id == guardian_wa
    tos = [m["to"] for m in outbox.sent[-2:]]
    assert set(tos) == {owner_wa, guardian_wa}
    async with SessionLocal() as s:
        owner = await s.get(User, owner_id)
    assert owner.convo_state == ConvoState.IDLE


async def test_owner_can_decline(monkeypatch, outbox, owner_wa, guardian_wa):
    owner_id = await _mk_owner(owner_wa)

    async def intent(text):
        return Intent(type="add_guardian", language="pcm")
    monkeypatch.setattr(service, "classify_intent", intent)

    await ingest_webhook(_inbound(owner_wa, "add guardian"))
    code = "GUARD-" + outbox.sent[-1]["body"].split("GUARD-")[1][:6].strip("*").strip()
    await ingest_webhook(_inbound(guardian_wa, code, "Ada"))
    await ingest_webhook(_button(owner_wa, "confirm_no"))
    link = await _link(owner_id)
    assert link.status == "REVOKED"


async def test_invalid_code_rejected(outbox, guardian_wa):
    await ingest_webhook(_inbound(guardian_wa, "GUARD-ZZZZZZ"))
    assert "no valid" in outbox.sent[-1]["body"] or "no correct" in outbox.sent[-1]["body"]


async def test_quiet_alert_fires_once_and_resets(outbox, owner_wa, guardian_wa):
    three_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
    owner_id = await _mk_owner(owner_wa, last_seen_at=three_days_ago)
    async with SessionLocal() as s:
        s.add(GuardianLink(owner_id=owner_id, guardian_wa_id=guardian_wa,
                           status="ACTIVE", invite_code=f"GUARD-{uuid.uuid4().hex[:6].upper()}"))
        await s.commit()

    assert await run_quiet_tick() == 1
    assert outbox.sent[-1]["to"] == guardian_wa
    assert "🔕" in outbox.sent[-1]["body"]

    # second tick: no repeat
    assert await run_quiet_tick() == 0

    # owner sends any message -> flag resets (router side)
    async def intent(text):
        return Intent(type="smalltalk", language="pcm")
    with patch.object(service, "classify_intent", intent):
        await ingest_webhook(_inbound(owner_wa, "how far"))
    async with SessionLocal() as s:
        owner = await s.get(User, owner_id)
    assert owner.quiet_alerted is False


async def test_weekly_digest_sunday_once(outbox, owner_wa, guardian_wa):
    owner_id = await _mk_owner(owner_wa)
    async with SessionLocal() as s:
        s.add(GuardianLink(owner_id=owner_id, guardian_wa_id=guardian_wa,
                           status="ACTIVE", invite_code=f"GUARD-{uuid.uuid4().hex[:6].upper()}"))
        s.add(Item(user_id=owner_id, name="Rice (50kg bag)", unit="bag",
                   qty=1, price_kobo=8_500_000, low_stock_at=2))
        await s.commit()

    sunday_6pm = datetime(2026, 7, 19, 18, 0, tzinfo=LAGOS)  # a Sunday
    assert await run_digest_tick(sunday_6pm) == 1
    body = outbox.sent[-1]["body"]
    assert "Weekly summary" in body and "Mama Nkechi" in body
    assert "Low stock" in body  # rice qty 1 <= threshold 2

    # same Sunday, later minute -> no repeat
    assert await run_digest_tick(sunday_6pm + timedelta(minutes=7)) == 0

    # Monday -> not digest day
    assert await run_digest_tick(sunday_6pm + timedelta(days=1)) == 0