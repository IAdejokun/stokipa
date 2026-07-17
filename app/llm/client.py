"""Low-level Anthropic wrapper.

Strategy: 2 attempts on the cheap model (settings.ANTHROPIC_MODEL, Haiku),
then 1 attempt on the escalation model (Sonnet). An attempt fails if the API
errors OR the tool output fails pydantic validation — both mean we didn't
get trustworthy structure.
"""

from typing import TypeVar

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

from app.config import settings

log = structlog.get_logger()

_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

ESCALATION_MODEL = "claude-sonnet-4-6"

T = TypeVar("T", bound=BaseModel)


class ExtractionFailed(Exception):
    """All model attempts failed — caller should apologize and re-ask."""


async def extract_with_tool(
    system: str, user_text: str, tool: dict, model_cls: type[T]
) -> T:
    plan = [settings.ANTHROPIC_MODEL, settings.ANTHROPIC_MODEL, ESCALATION_MODEL]
    last: Exception | None = None
    for model in plan:
        try:
            res = await _client.messages.create(
                model=model,
                max_tokens=1024,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": user_text}],
                timeout=20.0,
            )
            block = next(b for b in res.content if b.type == "tool_use")
            return model_cls.model_validate(block.input)
        except (anthropic.APIError, ValidationError, StopIteration) as exc:
            last = exc
            log.warning("llm_attempt_failed", model=model, error=str(exc))
    raise ExtractionFailed(str(last))