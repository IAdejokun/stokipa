"""WhatsApp Cloud API client.

Design notes:
- One AsyncClient reused across calls (connection pooling); closed in app
  lifespan via `wa.aclose()`.
- `transport` is injectable so tests run against httpx.MockTransport without
  touching Meta.
- Retries: 2 retries with exponential backoff on network errors and 5xx.
  4xx are NOT retried (they mean the request is wrong, retrying won't help).
- Every successful send is recorded as a Message(direction="OUT") row using
  the wamid Meta returns — full conversation audit trail in one table.
- Error 131047 = outside the 24h customer-service window. Free-form sends
  fail there; the fallback is a pre-approved template (milestone 8 wires it).
  For now we surface it as a distinct exception so callers can react.
"""

import asyncio
from typing import Any

import httpx
import structlog

from app.config import settings
from app.db import SessionLocal
from app.models import Message

log = structlog.get_logger()

GRAPH_BASE = "https://graph.facebook.com/v21.0"
RETRIABLE_STATUS = {500, 502, 503, 504, 429}


class WhatsAppError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"WhatsApp API {status}: {body}")


class OutsideServiceWindowError(WhatsAppError):
    """Error 131047: >24h since the user's last message; template required."""


class WhatsAppClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=GRAPH_BASE,
            timeout=10.0,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.WA_ACCESS_TOKEN}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------------- sending ----------------

    async def send_text(self, to: str, body: str) -> str:
        """Send a free-form text message. Returns the outbound wamid."""
        return await self._send(to, {"type": "text", "text": {"body": body}}, body)

    async def send_confirm_buttons(
        self, to: str, body: str, yes_label: str, no_label: str
    ) -> str:
        """Yes/No reply buttons — far better UX than typed 'yes' for
        semi-literate users. Button titles max 20 chars (Meta limit)."""
        payload = {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply",
                         "reply": {"id": "confirm_yes", "title": yes_label[:20]}},
                        {"type": "reply",
                         "reply": {"id": "confirm_no", "title": no_label[:20]}},
                    ]
                },
            },
        }
        return await self._send(to, payload, body)

    async def _send(self, to: str, message: dict, audit_body: str) -> str:
        payload = {"messaging_product": "whatsapp", "to": to, **message}
        data = await self._request(
            "POST", f"/{settings.WA_PHONE_NUMBER_ID}/messages", json=payload
        )
        wamid = data["messages"][0]["id"]
        await self._audit_out(wamid, to, message["type"], audit_body, payload)
        log.info("wa_sent", to=to, wamid=wamid, msg_type=message["type"])
        return wamid

    # ---------------- media ----------------

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Resolve a media id to (bytes, mime_type). Media URLs are
        short-lived and require the bearer token."""
        meta = await self._request("GET", f"/{media_id}")
        resp = await self._http.get(meta["url"], timeout=20.0)
        resp.raise_for_status()
        return resp.content, meta["mime_type"]

    # ---------------- internals ----------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        last_exc: Exception | None = None
        attempts = 3  # 1 try + 2 retries
        for attempt in range(attempts):
            try:
                resp = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            if resp.status_code in RETRIABLE_STATUS:
                last_exc = WhatsAppError(resp.status_code, resp.text)
                if attempt < attempts - 1:
                    await asyncio.sleep(0.5 * 2**attempt)
                continue

            if resp.is_error:
                if '"code":131047' in resp.text or "131047" in resp.text:
                    raise OutsideServiceWindowError(resp.status_code, resp.text)
                raise WhatsAppError(resp.status_code, resp.text)

            return resp.json()

        raise last_exc if last_exc else WhatsAppError(0, "unreachable")

    async def _audit_out(
        self, wamid: str, to: str, msg_type: str, body: str, raw: dict
    ) -> None:
        try:
            async with SessionLocal() as session:
                session.add(
                    Message(
                        wamid=wamid,
                        direction="OUT",
                        msg_type=msg_type,
                        body=body,
                        raw=raw,
                    )
                )
                await session.commit()
        except Exception:
            # Audit failure must never block a send that already succeeded.
            log.exception("wa_audit_failed", wamid=wamid, to=to)


# Module singleton used by the app; tests build their own with MockTransport.
wa = WhatsAppClient()