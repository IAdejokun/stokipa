"""Scheduler tests: the minute tick's rules, with injectable 'now'.

- fires at the user's hour, in IDLE, once per Lagos day
- skips: wrong hour, non-IDLE states, already-sent-today
- a failed send is NOT marked sent (retried next tick)
"""

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.jobs.scheduler import run_checkin_tick
from app.models import ConvoState, User
from app.whatsapp.client import wa

LAGOS = ZoneInfo("Africa/Lagos")
NOW = datetime(2026, 7, 17, 20, 3, tzinfo=LAGOS)  # 8:03pm Lagos


@pytest.fixture
def wa_id() -> str:
    return "23482" + uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
async def cleanup(wa_id):
    yield
    async with SessionLocal() as s:
        await s.execute(delete(User).where(User.wa_id == wa_id))
        await s.commit()


async def _mk_user(wa_id: str, **kw) -> int:
    defaults = dict(wa_id=wa_id, convo_state=ConvoState.IDLE,
                    checkin_hour=20, language="pcm")
    defaults.update(kw)
    async with SessionLocal() as s:
        u = User(**defaults)
        s.add(u)
        await s.commit()
        return u.id


async def _last_sent(user_id: int):
    async with SessionLocal() as s:
        return (await s.execute(
            select(User).where(User.id == user_id))).scalar_one().last_checkin_sent


async def test_fires_at_users_hour_once(outbox, wa_id):
    uid = await _mk_user(wa_id)
    assert await run_checkin_tick(NOW) == 1
    assert len(outbox.sent) == 1
    assert "Wetin you sell today" in outbox.sent[0]["body"]
    assert await _last_sent(uid) is not None

    # same hour, later minute -> no repeat
    assert await run_checkin_tick(NOW + timedelta(minutes=5)) == 0
    assert len(outbox.sent) == 1


async def test_skips_wrong_hour(outbox, wa_id):
    await _mk_user(wa_id, checkin_hour=9)
    assert await run_checkin_tick(NOW) == 0
    assert outbox.sent == []


async def test_skips_mid_flow_user(outbox, wa_id):
    await _mk_user(wa_id, convo_state=ConvoState.AWAITING_SALE_CONFIRM)
    assert await run_checkin_tick(NOW) == 0
    assert outbox.sent == []


async def test_fires_again_next_day(outbox, wa_id):
    yesterday_send = (NOW - timedelta(days=1)).astimezone(
        timezone.utc).replace(tzinfo=None)
    await _mk_user(wa_id, last_checkin_sent=yesterday_send)
    assert await run_checkin_tick(NOW) == 1


async def test_failed_send_not_marked_sent(outbox, wa_id, monkeypatch):
    uid = await _mk_user(wa_id)

    async def boom(to, body):
        raise RuntimeError("network down")

    monkeypatch.setattr(wa, "send_text", boom)
    assert await run_checkin_tick(NOW) == 0
    assert await _last_sent(uid) is None  # retried next tick