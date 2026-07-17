"""Meta WhatsApp Cloud API webhook endpoints.

GET  /webhook — one-time verification handshake when registering the URL.
POST /webhook — inbound messages/statuses.

Rules:
- Verify X-Hub-Signature-256 over the RAW body before trusting anything.
- Ack 200 immediately; Meta retries slow or non-200 responses, which would
  cause duplicate processing and eventually disable the webhook. All real
  work (STT, LLM, DB) happens in a background task.
"""

import json

import structlog
from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.config import settings
from app.pipeline.ingest import ingest_webhook
from app.whatsapp.signature import verify_signature

log = structlog.get_logger()
router = APIRouter()


@router.get("/webhook")
async def verify(request: Request) -> Response:
    q = request.query_params
    if (
        q.get("hub.mode") == "subscribe"
        and q.get("hub.verify_token") == settings.WA_VERIFY_TOKEN
    ):
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks) -> Response:
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not verify_signature(raw, sig, settings.WA_APP_SECRET):
        import hashlib, hmac as _h  # TEMP DEBUG — remove after diagnosis
        expected = _h.new(settings.WA_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        log.warning("webhook_bad_signature",
                    got=(sig or "")[:15], expected="sha256=" + expected[:8],
                    body_len=len(raw), secret_fp=settings.WA_APP_SECRET[:4])
        return Response(status_code=401)
    

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Signed but malformed — ack so Meta doesn't retry garbage forever.
        log.warning("webhook_bad_json")
        return Response(status_code=200)

    background.add_task(ingest_webhook, payload)
    return Response(status_code=200)