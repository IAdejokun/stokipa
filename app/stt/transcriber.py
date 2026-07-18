"""Speech-to-text behind a Protocol so the provider is swappable.

Provider: Groq's free tier serving whisper-large-v3 via an OpenAI-compatible
endpoint — zero cost, excellent Nigerian-accent coverage, and because it
speaks the OpenAI API we reuse the same client library as the LLM layer.

WhatsApp voice notes arrive as audio/ogg (opus codec), which Whisper accepts
directly — no transcoding needed.
"""

from typing import Protocol

from openai import AsyncOpenAI

from app.config import settings


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str) -> str: ...


# Biasing the decoder toward domain vocabulary measurably improves accuracy
# on product names and Nigerian speech patterns.
_DOMAIN_PROMPT = (
    "Nigerian shop owner talking about sales and stock: rice, garri, beans, "
    "Indomie, Milo, Peak milk, naira, kobo, carton, bag, sachet, crate, "
    "bottle, I sell, I buy, how much remain."
)


class GroqWhisperTranscriber:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        if "ogg" in mime_type:
            ext = "ogg"
        elif "mp4" in mime_type or "m4a" in mime_type:
            ext = "m4a"
        else:
            ext = "mp3"
        res = await self._client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=(f"note.{ext}", audio, mime_type),
            prompt=_DOMAIN_PROMPT,
            temperature=0.0,
        )
        return res.text.strip()


transcriber: Transcriber = GroqWhisperTranscriber()