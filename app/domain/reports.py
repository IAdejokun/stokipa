"""Read-side queries for owner questions: stock levels, revenue, top sellers.
All times computed in Africa/Lagos — the owner's 'today' is Lagos today, not
UTC today.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Item, Sale, SaleLine

LAGOS = ZoneInfo("Africa/Lagos")


def _period_start(period: str) -> datetime:
    """Boundary in Lagos local time, returned as NAIVE UTC — because
    Sale.sold_at is a naive (timestamp-without-tz) column populated by
    Postgres now() in UTC. Mixing aware/naive here breaks at the driver."""
    now = datetime.now(LAGOS)
    start = datetime.combine(now.date(), time.min, tzinfo=LAGOS)
    if period == "week":
        start -= timedelta(days=now.weekday())  # Monday
    elif period == "month":
        start = start.replace(day=1)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


async def stock_levels(user_id: int, item_ids: list[int] | None = None) -> list[Item]:
    async with SessionLocal() as session:
        q = select(Item).where(Item.user_id == user_id).order_by(Item.name)
        if item_ids:
            q = q.where(Item.id.in_(item_ids))
        return list((await session.execute(q)).scalars().all())


async def revenue(user_id: int, period: str) -> tuple[int, int]:
    """Returns (total_kobo, sale_count) for the period."""
    start = _period_start(period)
    async with SessionLocal() as session:
        row = (await session.execute(
            select(func.coalesce(func.sum(Sale.total_kobo), 0), func.count())
            .where(Sale.user_id == user_id, Sale.sold_at >= start)
        )).one()
    return int(row[0]), int(row[1])


async def top_sellers(user_id: int, period: str, limit: int = 3) -> list[tuple[str, int]]:
    """[(item_name, units_sold)] for the period, best first."""
    start = _period_start(period)
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Item.name, func.sum(SaleLine.qty).label("units"))
            .join(SaleLine, SaleLine.item_id == Item.id)
            .join(Sale, Sale.id == SaleLine.sale_id)
            .where(Sale.user_id == user_id, Sale.sold_at >= start)
            .group_by(Item.name)
            .order_by(func.sum(SaleLine.qty).desc())
            .limit(limit)
        )).all()
    return [(r[0], int(r[1])) for r in rows]