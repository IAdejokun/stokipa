"""Inventory domain operations. All writes transactional."""

import math

from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.lib.money import naira_to_kobo
from app.llm.tools import ParsedItem
from app.models import Item, StockMove, StockMoveType


class DuplicateItemError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"item already exists: {name}")


def default_low_stock(qty: int) -> int:
    return max(2, math.ceil(qty * 0.2))


async def create_item(user_id: int, parsed: ParsedItem) -> Item:
    """Create an item with its INITIAL stock move in one transaction."""
    async with SessionLocal() as session:
        try:
            async with session.begin():
                item = Item(
                    user_id=user_id,
                    name=parsed.name,
                    unit=parsed.unit,
                    qty=parsed.qty,
                    price_kobo=naira_to_kobo(parsed.price_naira),
                    cost_kobo=(
                        naira_to_kobo(parsed.cost_naira)
                        if parsed.cost_naira is not None else None
                    ),
                    low_stock_at=default_low_stock(parsed.qty),
                )
                session.add(item)
                await session.flush()
                session.add(StockMove(
                    item_id=item.id,
                    type=StockMoveType.INITIAL,
                    delta=parsed.qty,
                    reason="onboarding",
                ))
            return item
        except IntegrityError as exc:
            raise DuplicateItemError(parsed.name) from exc