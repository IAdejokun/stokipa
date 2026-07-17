"""Sales + restock domain operations. The money paths — everything here is
one transaction with row locks (SELECT ... FOR UPDATE) on the items touched.
"""

import structlog
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item, Sale, SaleLine, StockMove, StockMoveType

log = structlog.get_logger()


async def commit_sale(
    user_id: int, lines: list[dict], source_wamid: str | None
) -> tuple[Sale, list[int]]:
    """lines: [{"item_id": int, "qty": int, "unit_price_kobo": int}, ...]

    Returns (sale, item_ids_that_crossed_low_stock).

    Ledger-drift policy: real shops sell things the ledger missed. We NEVER
    block a sale because recorded stock is too low — we clamp stock at 0 and
    record the discrepancy as an ADJUSTMENT move. The audit trail stays
    honest; the owner's flow is never interrupted.
    """
    crossed: list[int] = []
    async with SessionLocal() as session:
        async with session.begin():
            total = sum(l["qty"] * l["unit_price_kobo"] for l in lines)
            sale = Sale(user_id=user_id, total_kobo=total,
                        source_wamid=source_wamid)
            session.add(sale)
            await session.flush()

            for l in lines:
                item = (await session.execute(
                    select(Item).where(Item.id == l["item_id"]).with_for_update()
                )).scalar_one()

                session.add(SaleLine(
                    sale_id=sale.id, item_id=item.id,
                    qty=l["qty"], unit_price_kobo=l["unit_price_kobo"],
                ))
                session.add(StockMove(
                    item_id=item.id, type=StockMoveType.SALE,
                    delta=-l["qty"], reason=f"sale:{sale.id}",
                ))

                new_qty = item.qty - l["qty"]
                if new_qty < 0:
                    session.add(StockMove(
                        item_id=item.id, type=StockMoveType.ADJUSTMENT,
                        delta=-new_qty, reason="ledger drift clamp",
                    ))
                    log.warning("ledger_drift", item_id=item.id,
                                recorded=item.qty, sold=l["qty"])
                    new_qty = 0

                item.qty = new_qty
                if new_qty <= item.low_stock_at and not item.low_stock_alerted:
                    item.low_stock_alerted = True
                    crossed.append(item.id)

        await session.refresh(sale)
    return sale, crossed


async def commit_restock(
    item_id: int, qty: int, unit_cost_kobo: int | None
) -> Item:
    """Increment stock in one transaction; reset the low-stock alert flag so
    the next crossing alerts again; update last-known cost if given."""
    async with SessionLocal() as session:
        async with session.begin():
            item = (await session.execute(
                select(Item).where(Item.id == item_id).with_for_update()
            )).scalar_one()
            item.qty += qty
            item.low_stock_alerted = False
            if unit_cost_kobo is not None:
                item.cost_kobo = unit_cost_kobo
            session.add(StockMove(
                item_id=item.id, type=StockMoveType.RESTOCK,
                delta=qty, reason="restock",
            ))
        await session.refresh(item)
    return item