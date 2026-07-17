"""Inbound message ingestion.

Idempotency contract (crash-safe "claim" pattern):

1. INSERT the Message row (unique on wamid).
   - Success  -> we own this message; process it, then set processed_at.
   - Conflict -> someone saw it before. Try to CLAIM it with
     `UPDATE ... SET processed_at = now() WHERE wamid = :w AND processed_at
     IS NULL RETURNING id`.
       - Row returned  -> the earlier attempt crashed mid-processing; we own
         the retry.
       - No row        -> already fully processed; drop the duplicate.

   The atomic UPDATE ... RETURNING makes the claim race-safe: two concurrent
   deliveries of the same wamid can never both process it.

Note: we set processed_at when we START processing (claim), not when we
finish. A crash after claim means that one message is lost rather than
double-processed — for a ledger, double-counting a sale is the worse failure.
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.pipeline.router import route_message
from app.whatsapp.client import wa

from app.db import SessionLocal
from app.models import Message

log = structlog.get_logger()


async def ingest_webhook(payload: dict) -> None:
    """Entry point for a verified webhook payload. Never raises."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            # Delivery/read receipts arrive as "statuses" — ignored for MVP.
            for msg in value.get("messages", []):
                try:
                    await _ingest_one(msg, value)
                except Exception:
                    log.exception(
                        "ingest_failed",
                        wamid=msg.get("id"),
                        wa_id=msg.get("from"),
                    )
                    # The user must never get silence.
                    try:
                        await wa.send_text(
                            msg["from"],
                            "Sorry, something went wrong. Abeg try again.",
                        )
                    except Exception:
                        log.exception("apology_send_failed", wamid=msg.get("id"))


async def _ingest_one(msg: dict, ctx: dict) -> None:
    wamid: str = msg["id"]
    wa_id: str = msg["from"]

    claimed = await _claim(wamid, msg)
    if not claimed:
        log.info("duplicate_webhook_skipped", wamid=wamid, wa_id=wa_id)
        return

    text, button_id = _extract_text(msg)

    if msg["type"] == "audio":
        # Milestone 6 wires the transcriber here:
        #   buf, mime = await download_media(msg["audio"]["id"])
        #   text = await transcriber.transcribe(buf, mime)
        log.info("audio_message_deferred", wamid=wamid, wa_id=wa_id)

    if text is not None:
        async with SessionLocal() as session:
            await session.execute(
                update(Message).where(Message.wamid == wamid).values(body=text)
            )
            await session.commit()

    profile = ((ctx.get("contacts") or [{}])[0].get("profile") or {}).get("name")
    log.info(
        "message_ingested",
        wamid=wamid,
        wa_id=wa_id,
        msg_type=msg["type"],
        has_text=text is not None,
        profile=profile,
    )
    if text is not None:
        await route_message(
            wa_id=wa_id, wamid=wamid, text=text, profile_name=profile,
            button_id=button_id,
        )


async def _claim(wamid: str, msg: dict) -> bool:
    """Insert-or-claim. Returns True iff this call owns processing."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        session.add(
            Message(
                wamid=wamid,
                direction="IN",
                msg_type=msg["type"],
                raw=msg,
                processed_at=now,  # claim at start; see module docstring
            )
        )
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()

        # Row exists. Claim it only if a previous attempt crashed pre-claim
        # (processed_at IS NULL). Atomic — safe under concurrency.
        result = await session.execute(
            update(Message)
            .where(Message.wamid == wamid, Message.processed_at.is_(None))
            .values(processed_at=now)
            .returning(Message.id)
        )
        await session.commit()
        return result.scalar_one_or_none() is not None


def _extract_text(msg: dict) -> tuple[str | None, str | None]:
    """Returns (text, button_id). button_id is set only for button replies —
    handlers use it to skip the LLM on yes/no confirmations."""
    if msg["type"] == "text":
        return msg["text"]["body"], None
    if msg["type"] == "interactive":
        inter = msg.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("title"), reply.get("id")
    return None, None