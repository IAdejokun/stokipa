"""Tool schemas (what we send Anthropic) + pydantic models (how we gate what
comes back). The pydantic validation is the trust boundary: malformed LLM
output raises here and never reaches the database.
"""

from pydantic import BaseModel, Field

LANGS = ["en", "pcm", "yo", "ha", "ig"]


# ---------------- onboarding: item-or-done ----------------

class ParsedItem(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    unit: str = "unit"
    qty: int = Field(ge=0)
    price_naira: float = Field(gt=0)
    cost_naira: float | None = None


class ItemOrDone(BaseModel):
    action: str  # "add_item" | "done" | "unclear"
    item: ParsedItem | None = None
    language: str = "en"


ITEM_OR_DONE_TOOL = {
    "name": "record_item_or_done",
    "description": (
        "The shop owner is listing inventory items one message at a time, or "
        "saying they are finished. Decide which, and extract the item if any."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add_item", "done", "unclear"],
                "description": (
                    "'add_item' if the message describes a product with a "
                    "quantity and price. 'done' if the owner says they have "
                    "finished listing (e.g. 'done', 'i don finish', 'oya na', "
                    "'mo ti pari', 'na gama', 'emechaala'). 'unclear' otherwise."
                ),
            },
            "item": {
                "type": ["object", "null"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Clean canonical product name, e.g. 'Rice (50kg bag)'",
                    },
                    "unit": {
                        "type": "string",
                        "description": "bag, carton, bottle, sachet, crate, tin, unit…",
                    },
                    "qty": {"type": "integer", "minimum": 0},
                    "price_naira": {
                        "type": "number",
                        "description": "Selling price per unit in naira. '5k' means 5000.",
                    },
                    "cost_naira": {
                        "type": ["number", "null"],
                        "description": "Cost per unit if stated, else null.",
                    },
                },
                "required": ["name", "qty", "price_naira"],
            },
            "language": {"type": "string", "enum": LANGS},
        },
        "required": ["action", "language"],
    },
}


# ---------------- confirmation ----------------

class ConfirmationVerdict(BaseModel):
    verdict: str  # "yes" | "no" | "other"
    language: str = "en"


CONFIRMATION_TOOL = {
    "name": "interpret_confirmation",
    "description": (
        "The assistant asked the shop owner to confirm something. Classify "
        "the owner's reply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["yes", "no", "other"],
                "description": (
                    "'yes' for affirmation in any language/register (yes, "
                    "correct, na so, e don do, ok, oya, beeni, eh, i, toh). "
                    "'no' for rejection (no, wrong, no be so, mba, a'a, rara). "
                    "'other' if the reply is something else entirely, e.g. a "
                    "new item or a correction."
                ),
            },
            "language": {"type": "string", "enum": LANGS},
        },
        "required": ["verdict", "language"],
    },
}


# ---------------- check-in hour ----------------

class CheckinHour(BaseModel):
    hour: int | None = Field(default=None, ge=0, le=23)
    language: str = "en"


CHECKIN_HOUR_TOOL = {
    "name": "parse_checkin_hour",
    "description": (
        "The owner is saying what time of day the assistant should ask about "
        "sales. Extract a 24h hour. '8 for evening'/'8 for night' = 20. "
        "'morning' alone = 8. 'evening' alone = 19. 'night' alone = 20. "
        "If genuinely unparseable, hour is null."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "hour": {"type": ["integer", "null"], "minimum": 0, "maximum": 23},
            "language": {"type": "string", "enum": LANGS},
        },
        "required": ["hour", "language"],
    },
}