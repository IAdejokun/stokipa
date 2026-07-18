"""Daily check-in scheduler + quiet-shop alerts + weekly guardian digest.

Design: ONE minute-tick cron job; all schedule state lives in Postgres,
so the system is restart-safe with zero external infrastructure.

Deployment note: run exactly ONE process (no uvicorn --workers); the
scheduler lives in-process and must not be duplicated.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db import SessionLocal
from app.domain import reports
from app.lib.money import fmt_naira
from app.llm.prompts import canned
from app.models import ConvoState, GuardianLink, User
from app.whatsapp.client import wa

log = structlog.get_logger()

LAGOS = ZoneInfo("Africa/Lagos")

scheduler = AsyncIOScheduler(timezone="UTC")


def _sent_today(last_sent: datetime | None, now_lagos: datetime) -> bool:
    if last_sent is None:
        return False
    aware = last_sent if last_sent.tzinfo else last_sent.replace(tzinfo=timezone.utc)
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


QUIET_AFTER = timedelta(hours=48)
DIGEST_WEEKDAY = 6   # Sunday
DIGEST_HOUR = 18     # 6pm Lagos


async def run_quiet_tick(now: datetime | None = None) -> int:
    """Alert ACTIVE guardians when their shop has been silent > 48h.
    One alert per silence episode (quiet_alerted flag; reset on activity)."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now_utc - QUIET_AFTER
    sent = 0
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(User, GuardianLink)
            .join(GuardianLink, GuardianLink.owner_id == User.id)
            .where(
                GuardianLink.status == "ACTIVE",
                User.last_seen_at < cutoff,
                User.quiet_alerted.is_(False),
            )
        )).all()
        alerted_owner_ids: set[int] = set()
        for owner, link in rows:
            seen = owner.last_seen_at
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            days = max(2, (now_utc - seen).days)
            try:
                await wa.send_text(link.guardian_wa_id, canned(
                    "quiet_alert", "pcm",
                    shop=owner.shop_name or "The shop", days=days))
                sent += 1
                alerted_owner_ids.add(owner.id)
            except Exception:
                log.exception("quiet_alert_failed", owner_id=owner.id)
        for owner, _ in rows:
            if owner.id in alerted_owner_ids:
                owner.quiet_alerted = True
        await session.commit()
    if sent:
        log.info("quiet_alerts_sent", count=sent)
    return sent


def _same_iso_week(a: datetime | None, b: datetime) -> bool:
    return a is not None and a.isocalendar()[:2] == b.isocalendar()[:2]


async def run_digest_tick(now: datetime | None = None) -> int:
    """Sunday 6pm Lagos: weekly summary to every ACTIVE guardian.
    Guarded per-link by digest_sent_at (ISO-week compare), so the minute
    tick and restarts can't double-send."""
    now_lagos = (now or datetime.now(LAGOS)).astimezone(LAGOS)
    if now_lagos.weekday() != DIGEST_WEEKDAY or now_lagos.hour != DIGEST_HOUR:
        return 0
    sent = 0
    async with SessionLocal() as session:
        links = (await session.execute(
            select(GuardianLink).where(GuardianLink.status == "ACTIVE")
        )).scalars().all()
        for link in links:
            if _same_iso_week(link.digest_sent_at, now_lagos.replace(tzinfo=None)):
                continue
            owner = await session.get(User, link.owner_id)
            body = await _build_digest(owner)
            try:
                await wa.send_text(link.guardian_wa_id, body)
            except Exception:
                log.exception("digest_send_failed", link_id=link.id)
                continue
            link.digest_sent_at = now_lagos.astimezone(
                timezone.utc).replace(tzinfo=None)
            sent += 1
        await session.commit()
    if sent:
        log.info("digests_sent", count=sent)
    return sent


async def _build_digest(owner: User) -> str:
    total, count = await reports.revenue(owner.id, "week")
    top = await reports.top_sellers(owner.id, "week")
    items = await reports.stock_levels(owner.id)
    low = [i for i in items if i.qty <= i.low_stock_at]
    lines = [canned("weekly_digest_header", "pcm",
                    shop=owner.shop_name or "Shop")]
    lines.append(f"💰 Sales this week: {fmt_naira(total)} ({count} sale(s))")
    if top:
        lines.append("🏆 Top sellers: " + ", ".join(f"{n} ({u})" for n, u in top))
    if low:
        lines.append("⚠️ Low stock: " + ", ".join(
            f"{i.name} ({i.qty} {i.unit})" for i in low))
    else:
        lines.append("📦 Stock levels dey okay.")
    return "\n".join(lines)

async def run_owner_summary_tick(now: datetime | None = None) -> int:
    """Sunday 6pm Lagos: insights summary straight to each owner — revenue,
    top sellers, slow movers, thin margins. The doc's 'honest observations'."""
    now_lagos = (now or datetime.now(LAGOS)).astimezone(LAGOS)
    if now_lagos.weekday() != DIGEST_WEEKDAY or now_lagos.hour != DIGEST_HOUR:
        return 0
    sent = 0
    async with SessionLocal() as session:
        users = (await session.execute(
            select(User).where(User.convo_state == ConvoState.IDLE)
        )).scalars().all()
        for user in users:
            last = user.weekly_summary_sent_at
            if last is not None and last.isocalendar()[:2] == \
                    now_lagos.isocalendar()[:2]:
                continue
            body = await _build_owner_summary(user)
            if body is None:
                continue
            try:
                await wa.send_text(user.wa_id, body)
            except Exception:
                log.exception("owner_summary_failed", user_id=user.id)
                continue
            user.weekly_summary_sent_at = now_lagos
            sent += 1
        await session.commit()
    if sent:
        log.info("owner_summaries_sent", count=sent)
    return sent


async def _build_owner_summary(user: User) -> str | None:
    total, count = await reports.revenue(user.id, "week")
    items = await reports.stock_levels(user.id)
    if not items:
        return None  # never onboarded properly; nothing to say
    top = await reports.top_sellers(user.id, "week")
    slow = await reports.slow_movers(user.id)
    thin = [i for i in items
            if i.cost_kobo and i.price_kobo
            and (i.price_kobo - i.cost_kobo) / i.price_kobo < 0.10]
    lines = [f"📊 *Your week — {user.shop_name or 'your shop'}*",
             f"💰 You make {fmt_naira(total)} from {count} sale(s)."]
    if top:
        lines.append("🏆 Best sellers: " + ", ".join(
            f"{n} ({u})" for n, u in top))
    if slow:
        names = ", ".join(i.name for i in slow[:3])
        lines.append(f"🐌 {names} never sell for 2 weeks — "
                     "you fit push am or reduce next order.")
    if thin:
        names = ", ".join(i.name for i in thin[:3])
        lines.append(f"📉 Profit thin on {names} — "
                     "check the price wey you dey sell am.")
    low = [i for i in items if i.qty <= i.low_stock_at]
    if low:
        lines.append("⚠️ Low stock: " + ", ".join(
            f"{i.name} ({i.qty} {i.unit})" for i in low))
    return "\n".join(lines)


async def _tick() -> None:
    await run_checkin_tick()
    await run_quiet_tick()
    await run_digest_tick()
    await run_owner_summary_tick()


def start() -> None:
    scheduler.add_job(_tick, "cron", minute="*",
                      id="stokipa-tick", replace_existing=True)
    scheduler.start()
    log.info("scheduler_started")


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")