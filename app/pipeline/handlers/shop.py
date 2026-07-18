"""'Share my shop' — mint a slug and hand the owner their storefront link."""

import re
import secrets

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.llm.prompts import canned
from app.models import User
from app.whatsapp.client import wa


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "shop").lower()).strip("-")
    return s[:40] or "shop"


async def share_link(user: User) -> None:
    async with SessionLocal() as session:
        row = await session.get(User, user.id)
        if not row.slug:
            base = _slugify(row.shop_name or "shop")
            slug = base
            exists = (await session.execute(
                select(User.id).where(User.slug == slug)
            )).scalar_one_or_none()
            if exists:
                slug = f"{base}-{secrets.token_hex(2)}"
            row.slug = slug
            await session.commit()
        slug = row.slug
        lang = row.language

    url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/shop/{slug}"
    await wa.send_text(user.wa_id, canned("share_shop", lang, url=url))