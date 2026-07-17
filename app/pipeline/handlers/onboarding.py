"""Onboarding conversation flow.

State machine (flow control is code, never the LLM — the LLM only extracts):

NEW ──welcome──► ONBOARDING_NAME ──save name──► ONBOARDING_ITEMS
ONBOARDING_ITEMS ──item message──► AWAITING_ITEM_CONFIRM (buttons)
AWAITING_ITEM_CONFIRM ──yes──► save item ──► ONBOARDING_ITEMS
AWAITING_ITEM_CONFIRM ──no───► ONBOARDING_ITEMS (retry)
AWAITING_ITEM_CONFIRM ──other──► treated as a fresh item message
ONBOARDING_ITEMS ──"done" (≥1 item)──► ONBOARDING_CHECKIN_TIME
ONBOARDING_CHECKIN_TIME ──hour parsed──► IDLE (setup complete)

Items can also be added from IDLE (handle_new_item_from_idle): same flow,
but pending_action carries a "return" state so the confirmation lands the
user back in IDLE instead of the onboarding loop.
"""

import structlog
from sqlalchemy import func, select

from app.db import SessionLocal
from app.domain.inventory import DuplicateItemError, create_item
from app.lib.money import fmt_naira, naira_to_kobo
from app.llm import service as llm
from app.llm.prompts import canned, fmt_hour
from app.llm.tools import ParsedItem
from app.models import ConvoState, Item, User
from app.whatsapp.client import wa

log = structlog.get_logger()


async def _save(user_id: int, **fields) -> None:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        for k, v in fields.items():
            setattr(user, k, v)
        await session.commit()


async def _item_count(user_id: int) -> int:
    async with SessionLocal() as session:
        return (await session.execute(
            select(func.count()).select_from(Item).where(Item.user_id == user_id)
        )).scalar_one()


async def handle(user: User, wamid: str, text: str, button_id: str | None) -> None:
    state = user.convo_state
    if state == ConvoState.NEW:
        await _save(user.id, convo_state=ConvoState.ONBOARDING_NAME)
        await wa.send_text(user.wa_id, canned("welcome", user.language))
    elif state == ConvoState.ONBOARDING_NAME:
        shop = text.strip()[:80]
        await _save(user.id, shop_name=shop,
                    convo_state=ConvoState.ONBOARDING_ITEMS)
        await wa.send_text(user.wa_id, canned("ask_items", user.language, shop=shop))
    elif state == ConvoState.ONBOARDING_ITEMS:
        await _handle_item_message(user, text)
    elif state == ConvoState.AWAITING_ITEM_CONFIRM:
        await _handle_item_confirmation(user, text, button_id)
    elif state == ConvoState.ONBOARDING_CHECKIN_TIME:
        await _handle_checkin_time(user, text)


async def handle_new_item_from_idle(user: User, text: str) -> None:
    """An already-set-up owner introduces a new product. Same extraction and
    confirm flow as onboarding, but returns to IDLE afterwards."""
    await _handle_item_message(user, text, return_state=ConvoState.IDLE.value)


async def _handle_item_message(
    user: User, text: str, return_state: str = ConvoState.ONBOARDING_ITEMS.value
) -> None:
    result = await llm.extract_item_or_done(text)
    lang = result.language
    await _save(user.id, language=lang)

    if result.action == "done":
        if await _item_count(user.id) == 0:
            await wa.send_text(user.wa_id, canned("need_one_item", lang))
            return
        await _save(user.id, convo_state=ConvoState.ONBOARDING_CHECKIN_TIME)
        await wa.send_text(user.wa_id, canned("ask_checkin", lang))
        return

    if result.action != "add_item" or result.item is None:
        await wa.send_text(user.wa_id, canned("item_unclear", lang))
        return

    item = result.item
    await _save(
        user.id,
        convo_state=ConvoState.AWAITING_ITEM_CONFIRM,
        pending_action={"kind": "item", "return": return_state,
                        **item.model_dump()},
    )
    await wa.send_confirm_buttons(
        user.wa_id,
        canned("confirm_item", lang, name=item.name, qty=item.qty,
               unit=item.unit, price=fmt_naira(naira_to_kobo(item.price_naira))),
        canned("yes_label", lang),
        canned("no_label", lang),
    )


async def _handle_item_confirmation(
    user: User, text: str, button_id: str | None
) -> None:
    lang = user.language
    if button_id == "confirm_yes":
        verdict = "yes"
    elif button_id == "confirm_no":
        verdict = "no"
    else:
        verdict = (await llm.interpret_confirmation(text)).verdict

    if verdict == "yes":
        pending = user.pending_action or {}
        back = ConvoState(pending.get("return", ConvoState.ONBOARDING_ITEMS.value))
        parsed = ParsedItem.model_validate(
            {k: v for k, v in pending.items() if k not in ("kind", "return")}
        )
        try:
            await create_item(user.id, parsed)
        except DuplicateItemError:
            await _save(user.id, pending_action=None, convo_state=back)
            await wa.send_text(
                user.wa_id, canned("duplicate_item", lang, name=parsed.name)
            )
            return
        await _save(user.id, pending_action=None, convo_state=back)
        key = "item_saved_idle" if back == ConvoState.IDLE else "item_saved"
        await wa.send_text(user.wa_id, canned(key, lang))
        return

    if verdict == "no":
        back = ConvoState((user.pending_action or {}).get(
            "return", ConvoState.ONBOARDING_ITEMS.value))
        await _save(user.id, pending_action=None, convo_state=back)
        await wa.send_text(user.wa_id, canned("item_retry", lang))
        return

    # "other": the owner ignored the buttons and sent something new —
    # treat it as a fresh item message (abandon the old pending).
    await _save(user.id, pending_action=None,
                convo_state=ConvoState.ONBOARDING_ITEMS)
    await _handle_item_message(user, text)


async def _handle_checkin_time(user: User, text: str) -> None:
    result = await llm.parse_checkin_hour(text)
    lang = result.language
    if result.hour is None:
        await wa.send_text(user.wa_id, canned("checkin_unclear", lang))
        return
    await _save(user.id, language=lang, checkin_hour=result.hour,
                convo_state=ConvoState.IDLE)
    count = await _item_count(user.id)
    async with SessionLocal() as session:
        fresh = await session.get(User, user.id)
        shop = fresh.shop_name or "Your shop"
    await wa.send_text(
        user.wa_id,
        canned("setup_done", lang, shop=shop, count=count,
               hour=fmt_hour(result.hour)),
    )