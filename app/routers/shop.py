"""Public read-only storefront API.

Serves a shop's catalog to the companion web storefront. Deliberately
minimal and privacy-conscious:
- No auth (it's a public shop page), but READ-ONLY and rate-limitable at
  the proxy later.
- Exposes availability (in_stock), never exact stock counts — a shop's
  inventory depth is business-sensitive.
- CORS open: the React storefront is served from a different origin.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Item, User

router = APIRouter(prefix="/api")


class ShopItem(BaseModel):
    name: str
    unit: str
    price_naira: float
    in_stock: bool


class ShopOut(BaseModel):
    shop_name: str
    whatsapp: str            # owner's number for wa.me order deep-links
    items: list[ShopItem]


@router.get("/shops/{slug}", response_model=ShopOut)
async def get_shop(slug: str) -> ShopOut:
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.slug == slug)
        )).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="shop not found")
        items = (await session.execute(
            select(Item).where(Item.user_id == user.id).order_by(Item.name)
        )).scalars().all()
    return ShopOut(
        shop_name=user.shop_name or "Shop",
        whatsapp=user.wa_id,
        items=[ShopItem(name=i.name, unit=i.unit,
                        price_naira=i.price_kobo / 100,
                        in_stock=i.qty > 0) for i in items],
    )