"""High-level LLM operations. Handlers call THESE functions, never the raw
client — this module is the seam that tests stub out.
"""

from app.llm import prompts
from app.llm.client import extract_with_tool
from app.llm.tools import (
    CHECKIN_HOUR_TOOL,
    CONFIRMATION_TOOL,
    ITEM_OR_DONE_TOOL,
    CheckinHour,
    ConfirmationVerdict,
    ItemOrDone,
)


async def extract_item_or_done(text: str) -> ItemOrDone:
    return await extract_with_tool(
        prompts.ITEM_OR_DONE_SYSTEM, text, ITEM_OR_DONE_TOOL, ItemOrDone
    )


async def interpret_confirmation(text: str) -> ConfirmationVerdict:
    return await extract_with_tool(
        prompts.CONFIRMATION_SYSTEM, text, CONFIRMATION_TOOL, ConfirmationVerdict
    )


async def parse_checkin_hour(text: str) -> CheckinHour:
    return await extract_with_tool(
        prompts.CHECKIN_HOUR_SYSTEM, text, CHECKIN_HOUR_TOOL, CheckinHour
    )

async def classify_intent(text: str):
    from app.llm.tools import INTENT_TOOL, Intent
    return await extract_with_tool(
        prompts.INTENT_SYSTEM, text, INTENT_TOOL, Intent
    )


async def extract_sale(text: str, inventory_table: str):
    from app.llm.tools import SALE_TOOL, SaleExtract
    return await extract_with_tool(
        prompts.SALE_SYSTEM.format(inventory=inventory_table),
        text, SALE_TOOL, SaleExtract,
    )


async def extract_restock(text: str, inventory_table: str):
    from app.llm.tools import RESTOCK_TOOL, RestockExtract
    return await extract_with_tool(
        prompts.RESTOCK_SYSTEM.format(inventory=inventory_table),
        text, RESTOCK_TOOL, RestockExtract,
    )