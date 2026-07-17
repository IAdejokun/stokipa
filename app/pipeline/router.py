"""Conversation dispatch: load-or-create the user, dispatch on state.

Flow control lives HERE and in the handlers — the LLM only ever extracts
structure; it never decides what state comes next.
"""

import structlog
from sqlalchemy import func, select

from app.db import SessionLocal
from app.llm.prompts import canned
from app.models import ConvoState, User
from app.pipeline.handlers import onboarding
from app.whatsapp.client import wa

log = structlog.get_logger()

_ONBOARDING_STATES = {
    ConvoState.NEW,
    ConvoState.ONBOARDING_NAME,
    ConvoState.ONBOARDING_ITEMS,
    ConvoState.ONBOARDING_CHECKIN_TIME,
    ConvoState.AWAITING_ITEM_CONFIRM,
}


async def route_message(
    wa_id: str,
    wamid: str,
    text: str,
    profile_name: str | None,
    button_id: str | None = None,
) -> None:
    async with SessionLocal() as session:
        user = (await session.execute(
            select(User).where(User.wa_id == wa_id)
        )).scalar_one_or_none()
        if user is None:
            user = User(wa_id=wa_id, name=profile_name,
                        convo_state=ConvoState.NEW)
            session.add(user)
        user.last_seen_at = func.now()
        await session.commit()
        await session.refresh(user)

    log.info("route", wa_id=wa_id, state=user.convo_state.value)

    if user.convo_state in _ONBOARDING_STATES:
        await onboarding.handle(user, wamid, text, button_id)
        return

    # IDLE — milestone 5 adds intent classification (sales/restock/queries).
    await wa.send_text(wa_id, canned("help_idle", user.language))