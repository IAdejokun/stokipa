"""Storefront API + share-link + owner weekly summary tests."""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.jobs.scheduler import run_owner_summary_tick
from app.main import app
from app.models import ConvoState, Item, Sale, SaleLine, StockMove, User
from app.pipeline.handlers.shop import share_link

LAGOS = ZoneInfo("Africa/Lagos")


@pytest.fixture
def wa_id() -> str:
    return "23485" + uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
async def cleanup(wa_id):
    yield
    async with SessionLocal() as s:
        u = (await s.execute(
            select(User).where(User.wa_id == wa_id))).scalar_one_or_none()
        if u:
            for it in (await s.execute(
                    select(Item).where(Item.user_id == u.id))).scalars():
                await s.execute(delete(StockMove).where(StockMove.item_id == it.id))
                await s.execute(delete(SaleLine).where(SaleLine.item_id == it.id))
            await s.execute(delete(Sale).where(Sale.user_id == u.id))
            await s.execute(delete(Item).where(Item.user_id == u.id))
            await s.execute(delete(User).where(User.id == u.id))
        await s.commit()


async def _mk_shop(wa_id: str) -> int:
    async with SessionLocal() as s:
        u = User(wa_id=wa_id, shop_name="Mama Nkechi Provisions",
                 language="pcm", convo_state=ConvoState.IDLE, checkin_hour=20)
        s.add(u)
        await s.flush()
        s.add(Item(user_id=u.id, name="Rice (50kg bag)", unit="bag", qty=8,
                   price_kobo=8_500_000, cost_kobo=7_800_000, low_stock_at=2))
        s.add(Item(user_id=u.id, name="Milo (tin)", unit="tin", qty=0,
                   price_kobo=250_000, low_stock_at=2))
        await s.commit()
        return u.id


async def test_share_link_mints_slug_and_sends_url(outbox, wa_id):
    uid = await _mk_shop(wa_id)
    async with SessionLocal() as s:
        user = await s.get(User, uid)
    await share_link(user)
    async with SessionLocal() as s:
        user = await s.get(User, uid)
    assert user.slug and user.slug.startswith("mama-nkechi")
    assert user.slug in outbox.sent[-1]["body"]

    # idempotent: second ask reuses the same slug
    await share_link(user)
    async with SessionLocal() as s:
        again = await s.get(User, uid)
    assert again.slug == user.slug


async def test_public_shop_endpoint(outbox, wa_id):
    uid = await _mk_shop(wa_id)
    async with SessionLocal() as s:
        user = await s.get(User, uid)
    await share_link(user)
    async with SessionLocal() as s:
        slug = (await s.get(User, uid)).slug

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        res = await client.get(f"/api/shops/{slug}")
        assert res.status_code == 200
        data = res.json()
        assert data["shop_name"] == "Mama Nkechi Provisions"
        assert data["whatsapp"] == wa_id
        rice = next(i for i in data["items"] if "Rice" in i["name"])
        milo = next(i for i in data["items"] if "Milo" in i["name"])
        assert rice["price_naira"] == 85000.0
        assert rice["in_stock"] is True
        assert milo["in_stock"] is False
        assert "qty" not in rice  # stock depth never exposed

        assert (await client.get("/api/shops/nope-404")).status_code == 404


async def test_owner_weekly_summary_includes_insights(outbox, wa_id):
    uid = await _mk_shop(wa_id)
    # one rice sale this week; Milo has qty 0 so not slow-mover; add slow item
    async with SessionLocal() as s:
        slow = Item(user_id=uid, name="Sardine (carton)", unit="carton", qty=6,
                    price_kobo=1_400_000, cost_kobo=1_350_000, low_stock_at=2)
        s.add(slow)
        rice = (await s.execute(select(Item).where(
            Item.user_id == uid, Item.name.like("Rice%")))).scalar_one()
        sale = Sale(user_id=uid, total_kobo=2 * rice.price_kobo)
        s.add(sale)
        await s.flush()
        s.add(SaleLine(sale_id=sale.id, item_id=rice.id, qty=2,
                       unit_price_kobo=rice.price_kobo))
        await s.commit()

    sunday_6pm = datetime(2026, 7, 19, 18, 0, tzinfo=LAGOS)
    assert await run_owner_summary_tick(sunday_6pm) >= 1
    mine = [m for m in outbox.sent if m["to"] == wa_id]
    body = mine[-1]["body"]
    assert "Your week" in body
    assert "₦170,000" in body                   # revenue
    assert "Sardine" in body and "🐌" in body   # slow mover
    assert "📉" in body                          # thin margin (sardine ~3.5%)

    # once per week
    await run_owner_summary_tick(sunday_6pm + timedelta(minutes=9))
    mine2 = [m for m in outbox.sent if m["to"] == wa_id]
    assert len(mine2) == len(mine)