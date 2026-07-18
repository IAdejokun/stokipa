"""Reset + seed a beautiful demo shop for a given WhatsApp number.

Usage:
    python scripts/seed_demo.py 2349062867720

Creates: Mama Nkechi Provisions with 6 items (one low-stock, one slow-mover,
one thin-margin), a week of realistic sales, IDLE state, 8pm check-in.
Run it before every demo — the DB always starts photogenic.
"""

import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ConvoState, GuardianLink, Item, Sale, SaleLine, StockMove,
    StockMoveType, User,
)

ITEMS = [
    # name, unit, qty, price_kobo, cost_kobo, low_stock_at
    ("Rice (50kg bag)", "bag", 8, 8_500_000, 7_800_000, 2),
    ("Indomie (carton)", "carton", 12, 1_100_000, 950_000, 3),
    ("Peak Milk (carton)", "carton", 2, 950_000, 900_000, 2),   # low + thin margin
    ("Garri (paint bucket)", "bucket", 15, 150_000, 100_000, 3),
    ("Milo (tin)", "tin", 20, 250_000, 180_000, 4),
    ("Sardine (carton)", "carton", 6, 1_400_000, 1_300_000, 2),  # slow mover
]


async def main(wa_id: str) -> None:
    async with SessionLocal() as s:
        # ---- wipe any existing data for this number (FK-safe order) ----
        old = (await s.execute(
            select(User).where(User.wa_id == wa_id))).scalar_one_or_none()
        if old:
            for item in (await s.execute(
                    select(Item).where(Item.user_id == old.id))).scalars():
                await s.execute(delete(StockMove).where(
                    StockMove.item_id == item.id))
                await s.execute(delete(SaleLine).where(
                    SaleLine.item_id == item.id))
            await s.execute(delete(Sale).where(Sale.user_id == old.id))
            await s.execute(delete(Item).where(Item.user_id == old.id))
            await s.execute(delete(GuardianLink).where(
                GuardianLink.owner_id == old.id))
            await s.execute(delete(User).where(User.id == old.id))
            await s.commit()

        # ---- fresh shop, already onboarded (IDLE, 8pm check-in) ----
        user = User(wa_id=wa_id, name="Nkechi",
                    shop_name="Mama Nkechi Provisions",
                    language="pcm", convo_state=ConvoState.IDLE,
                    checkin_hour=20)
        s.add(user)
        await s.flush()

        items = []
        for name, unit, qty, price, cost, low in ITEMS:
            it = Item(user_id=user.id, name=name, unit=unit, qty=qty,
                      price_kobo=price, cost_kobo=cost, low_stock_at=low)
            s.add(it)
            items.append(it)
        await s.flush()
        for it in items:
            s.add(StockMove(item_id=it.id, type=StockMoveType.INITIAL,
                            delta=it.qty + 5, reason="seed"))

        # ---- a week of realistic sales (sardine excluded -> slow mover) ----
        now = datetime.now(timezone.utc)
        for day in range(7):
            for _ in range(random.randint(1, 3)):
                it = random.choice(items[:5])
                qty = random.randint(1, 3)
                sale = Sale(user_id=user.id, total_kobo=qty * it.price_kobo,
                            sold_at=now - timedelta(days=day,
                                                    hours=random.randint(1, 9)))
                s.add(sale)
                await s.flush()
                s.add(SaleLine(sale_id=sale.id, item_id=it.id, qty=qty,
                               unit_price_kobo=it.price_kobo))
                s.add(StockMove(item_id=it.id, type=StockMoveType.SALE,
                                delta=-qty, reason=f"seed:{sale.id}"))
        await s.commit()
    print(f"Seeded demo shop for {wa_id} ✅")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/seed_demo.py <wa_id e.g. 2349062867720>")
    asyncio.run(main(sys.argv[1]))