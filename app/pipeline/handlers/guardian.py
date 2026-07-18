"""Guardian (family/diaspora oversight) flow.

WhatsApp forbids cold-messaging someone who has never messaged the bot, so
linking is GUARDIAN-INITIATED via a code — which also keeps consent clean:

1. Owner: "I wan add my daughter as guardian"
   -> bot generates GUARD-XXXX, tells owner to share it.
2. Guardian sends GUARD-XXXX to the bot (from their own phone)
   -> bot records their number/name on the link (still PENDING)
   -> bot asks the OWNER to approve with yes/no buttons.
3. Owner taps yes -> link ACTIVE; both sides notified.
   Owner taps no  -> link REVOKED; guardian politely told.

ACTIVE guardians receive: weekly digest + quiet-shop alerts (scheduler).
"""

import re
import secrets

import structlog
from sqlalchemy import select

from app.db import SessionLocal
from app.llm import service as llm
from app.llm.prompts import canned
from app.models import ConvoState, GuardianLink, User
from app.whatsapp.client import wa

log = structlog.get_logger()

CODE_RE = re.compile(r"\bGUARD-([A-Z0-9]{4,8})\b", re.IGNORECASE)


def extract_code(text: str) -> str | None:
    m = CODE_RE.search(text or "")
    return f"GUARD-{m.group(1).upper()}" if m else None


async def request_invite(owner: User) -> None:
    """Owner asked to add a guardian: mint a code on a PENDING link."""
    code = f"GUARD-{secrets.token_hex(3).upper()[:6]}"
    async with SessionLocal() as session:
        session.add(GuardianLink(owner_id=owner.id, invite_code=code,
                                 status="PENDING"))
        await session.commit()
    await wa.send_text(owner.wa_id,
                       canned("guardian_invite", owner.language, code=code))


async def handle_code(guardian_wa_id: str, profile_name: str | None,
                      code: str) -> None:
    """Someone sent a GUARD-XXXX code. Attach them to the link and ask the
    owner for consent."""
    async with SessionLocal() as session:
        link = (await session.execute(
            select(GuardianLink).where(GuardianLink.invite_code == code)
        )).scalar_one_or_none()
        if link is None or link.status != "PENDING":
            await wa.send_text(guardian_wa_id, canned("guardian_invalid_code", "pcm"))
            return
        owner = await session.get(User, link.owner_id)
        if owner.wa_id == guardian_wa_id:
            await wa.send_text(guardian_wa_id, canned("guardian_self_link", owner.language))
            return
        link.guardian_wa_id = guardian_wa_id
        link.guardian_name = profile_name
        owner.convo_state = ConvoState.AWAITING_GUARDIAN_CONSENT
        owner.pending_action = {"kind": "guardian", "link_id": link.id}
        await session.commit()
        owner_lang, shop = owner.language, owner.shop_name or "your shop"

    await wa.send_text(guardian_wa_id, canned("guardian_code_received", "pcm"))
    await wa.send_confirm_buttons(
        (await _owner_wa_id(code)),
        canned("guardian_confirm_ask", owner_lang,
               name=profile_name or guardian_wa_id, number=guardian_wa_id,
               shop=shop),
        canned("yes_label", owner_lang), canned("no_label", owner_lang),
    )


async def _owner_wa_id(code: str) -> str:
    async with SessionLocal() as session:
        link = (await session.execute(
            select(GuardianLink).where(GuardianLink.invite_code == code)
        )).scalar_one()
        owner = await session.get(User, link.owner_id)
        return owner.wa_id


async def handle_owner_consent(owner: User, text: str,
                               button_id: str | None) -> None:
    lang = owner.language
    if button_id == "confirm_yes":
        verdict = "yes"
    elif button_id == "confirm_no":
        verdict = "no"
    else:
        verdict = (await llm.interpret_confirmation(text)).verdict

    pending = owner.pending_action or {}
    link_id = pending.get("link_id")

    async with SessionLocal() as session:
        link = await session.get(GuardianLink, link_id) if link_id else None
        if link is None:
            owner_row = await session.get(User, owner.id)
            owner_row.convo_state = ConvoState.IDLE
            owner_row.pending_action = None
            await session.commit()
            return
        if verdict == "yes":
            link.status = "ACTIVE"
        elif verdict == "no":
            link.status = "REVOKED"
        else:
            # unclear -> re-ask once via text
            await session.commit()
            await wa.send_text(owner.wa_id, canned("guardian_reask", lang))
            return
        guardian_wa, guardian_name = link.guardian_wa_id, link.guardian_name
        owner_row = await session.get(User, owner.id)
        owner_row.convo_state = ConvoState.IDLE
        owner_row.pending_action = None
        shop = owner_row.shop_name or "the shop"
        await session.commit()

    if verdict == "yes":
        await wa.send_text(owner.wa_id, canned(
            "guardian_active_owner", lang, name=guardian_name or guardian_wa))
        await wa.send_text(guardian_wa, canned(
            "guardian_active_guardian", "pcm", shop=shop))
    else:
        await wa.send_text(owner.wa_id, canned("guardian_declined_owner", lang))
        await wa.send_text(guardian_wa, canned("guardian_declined_guardian", "pcm"))