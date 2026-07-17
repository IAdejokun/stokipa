"""Conversation dispatch: load-or-create the user, dispatch on state.

Flow control lives HERE and in the handlers — the LLM only ever extracts
structure; it never decides what state comes next.
"""

import structlog
from sqlalchemy import func, select

from app.db import SessionLocal
from app.llm import service as llm
from app.llm.prompts import canned
from app.models import ConvoState, User
from app.pipeline.handlers import onboarding, query, sales
from app.whatsapp.client import wa

log = structlog.get_logger()

_ONBOARDING_STATES = {
    ConvoState.NEW,
    ConvoState.ONBOARDING_NAME,
    ConvoState.ONBOARDING_ITEMS,
    ConvoState.ONBOARDING_CHECKIN_TIME,
    ConvoState.AWAITING_ITEM_CONFIRM,
}

_SALES_CONFIRM_STATES = {
    ConvoState.AWAITING_SALE_CONFIRM,
    ConvoState.AWAITING_RESTOCK_CONFIRM,
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

    if user.convo_state in _SALES_CONFIRM_STATES:
        await sales.handle_confirmation(user, wamid, text, button_id)
        return

    await route_idle(user, wamid, text)


async def route_idle(user: User, wamid: str, text: str) -> None:
    """Free-form message from a set-up user: classify, then dispatch."""
    intent = await llm.classify_intent(text)
    log.info("intent", wa_id=user.wa_id, type=intent.type)

    if intent.type == "log_sale":
        await sales.start_sale(user, wamid, text)
    elif intent.type == "restock":
        await sales.start_restock(user, wamid, text)
    elif intent.type == "query":
        await query.handle(user, intent)
    elif intent.type == "add_item":
        await onboarding.handle_new_item_from_idle(user, text)
    else:
        await wa.send_text(user.wa_id, canned("help_full", intent.language))