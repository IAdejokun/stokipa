"""Low-stock alerting. commit_sale marks which items crossed the threshold
(inside the transaction, race-safe via the alerted flag); this module turns
those into WhatsApp messages after the transaction commits.
"""

import structlog
from sqlalchemy import select

from app.db import SessionLocal
from app.llm.prompts import canned
from app.models import Item
from app.whatsapp.client import wa

log = structlog.get_logger()


async def send_low_stock_alerts(
    wa_id: str, lang: str, crossed_item_ids: list[int]
) -> None:
    if not crossed_item_ids:
        return
    async with SessionLocal() as session:
        items = (await session.execute(
            select(Item).where(Item.id.in_(crossed_item_ids))
        )).scalars().all()
    for item in items:
        try:
            await wa.send_text(wa_id, canned(
                "low_stock", lang,
                name=item.name, qty=item.qty, unit=item.unit,
            ))
        except Exception:
            log.exception("low_stock_alert_failed", item_id=item.id)