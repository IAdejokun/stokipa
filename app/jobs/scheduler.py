"""Daily check-in scheduler.

Design: ONE minute-tick cron job; all schedule state lives in Postgres
(users.checkin_hour + users.last_checkin_sent), so the system is
restart-safe with zero external infrastructure (no Redis, no job store).

Rules enforced by the tick:
- Fire only at the user's chosen hour, in Africa/Lagos.
- Never interrupt a flow: only users sitting in IDLE get the nudge.
- At most once per Lagos calendar day (last_checkin_sent guard), so
  restarts and the 60x/hour tick can't double-send.

Deployment note: run exactly ONE process (no uvicorn --workers); the
scheduler lives in-process and must not be duplicated.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db import SessionLocal
from app.llm.prompts import canned
from app.models import ConvoState, User
from app.whatsapp.client import wa

log = structlog.get_logger()

LAGOS = ZoneInfo("Africa/Lagos")

scheduler = AsyncIOScheduler(timezone="UTC")


def _sent_today(last_sent: datetime | None, now_lagos: datetime) -> bool:
    if last_sent is None:
        return False
    # stored as naive UTC -> make aware -> compare Lagos calendar dates
    aware = last_sent.replace(tzinfo=timezone.utc)
    return aware.astimezone(LAGOS).date() == now_lagos.date()


async def run_checkin_tick(now: datetime | None = None) -> int:
    """One pass. Returns how many check-ins were sent (for tests/logs).
    `now` is injectable for tests; defaults to real Lagos now."""
    now_lagos = (now or datetime.now(LAGOS)).astimezone(LAGOS)
    sent = 0
    async with SessionLocal() as session:
        users = (await session.execute(
            select(User).where(
                User.checkin_hour == now_lagos.hour,
                User.convo_state == ConvoState.IDLE,
            )
        )).scalars().all()

        for user in users:
            if _sent_today(user.last_checkin_sent, now_lagos):
                continue
            try:
                await wa.send_text(
                    user.wa_id, canned("daily_checkin", user.language)
                )
            except Exception:
                # Don't mark as sent — the next tick retries this hour.
                log.exception("checkin_send_failed", wa_id=user.wa_id)
                continue
            user.last_checkin_sent = now_lagos.astimezone(
                timezone.utc).replace(tzinfo=None)
            sent += 1
        await session.commit()

    if sent:
        log.info("checkins_sent", count=sent, hour=now_lagos.hour)
    return sent


def start() -> None:
    scheduler.add_job(run_checkin_tick, "cron", minute="*",
                      id="checkin-tick", replace_existing=True)
    scheduler.start()
    log.info("scheduler_started")


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")