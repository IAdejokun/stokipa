"""Low-level LLM wrapper — DeepSeek via the OpenAI-compatible API.

The rest of the app never imports this directly except through
app/llm/service.py; swapping providers (DeepSeek <-> Anthropic <-> anything
OpenAI-compatible) means editing this file only.

Strategy: 3 attempts on the same model. An attempt fails if the API errors
OR the tool output fails pydantic validation — both mean we didn't get
trustworthy structure.
"""

import json
from typing import TypeVar

import structlog
from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.config import settings

log = structlog.get_logger()

_client = AsyncOpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
)

T = TypeVar("T", bound=BaseModel)


class ExtractionFailed(Exception):
    """All attempts failed — caller should apologize and re-ask."""


def _to_openai_tool(tool: dict) -> dict:
    """Our tool dicts use Anthropic's shape (name/description/input_schema);
    convert to OpenAI function-calling shape so the schemas in tools.py stay
    provider-neutral."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


async def extract_with_tool(
    system: str, user_text: str, tool: dict, model_cls: type[T]
) -> T:
    last: Exception | None = None
    for attempt in range(3):
        try:
            res = await _client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
                tools=[_to_openai_tool(tool)],
                tool_choice={
                    "type": "function",
                    "function": {"name": tool["name"]},
                },
                timeout=25.0,
            )
            calls = res.choices[0].message.tool_calls
            if not calls:
                raise ValueError("model returned no tool call")
            args = json.loads(calls[0].function.arguments)
            return model_cls.model_validate(args)
        except (APIError, ValidationError, ValueError,
                json.JSONDecodeError) as exc:
            last = exc
            log.warning("llm_attempt_failed", attempt=attempt, error=str(exc))
    raise ExtractionFailed(str(last))