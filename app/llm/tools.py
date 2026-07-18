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

# ---------------- intent classification (IDLE state) ----------------

class Intent(BaseModel):
    type: str  # log_sale | restock | add_item | query | help | smalltalk
    query_kind: str | None = None  # stock_level | revenue | top_sellers
    item_name: str | None = None
    period: str | None = None      # today | week | month
    language: str = "en"


INTENT_TOOL = {
    "name": "classify_intent",
    "description": (
        "Classify what the shop owner wants. They may speak English, Pidgin, "
        "Yoruba, Hausa, or Igbo."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["log_sale", "restock", "add_item", "query",
                         "add_guardian", "share_shop", "help", "smalltalk"],
                "description": (
                    "'log_sale': reporting things sold ('I sell 3 rice'). "
                    "'restock': bought/added new stock of an EXISTING item "
                    "('I buy 5 more cartons'). 'add_item': introducing a NEW "
                    "product to track. 'query': asking about stock, money, or "
                    "sales. 'add_guardian': wanting a family member or friend "
                    "to monitor, oversee, or receive updates about the shop. "
                    "'share_shop': asking for their shop link/page/storefront "
                    "to share with customers. 'help'/'smalltalk': anything else."
                ),
            },
            "query_kind": {
                "type": ["string", "null"],
                "enum": ["stock_level", "revenue", "top_sellers", None],
            },
            "item_name": {
                "type": ["string", "null"],
                "description": "Item the question is about, if any.",
            },
            "period": {
                "type": ["string", "null"],
                "enum": ["today", "week", "month", None],
            },
            "language": {"type": "string", "enum": LANGS},
        },
        "required": ["type", "language"],
    },
}

# ---------------- sale extraction ----------------

class SaleLineOut(BaseModel):
    inventory_item_id: int | None = None
    spoken_name: str
    qty: int = Field(ge=1)
    unit_price_naira: float | None = None


class SaleExtract(BaseModel):
    lines: list[SaleLineOut]
    language: str = "en"


SALE_TOOL = {
    "name": "record_sale",
    "description": (
        "Extract the items the owner says they sold. Match names to the "
        "inventory list when confident."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "inventory_item_id": {
                            "type": ["integer", "null"],
                            "description": (
                                "id from the inventory list if confidently "
                                "matched, else null"
                            ),
                        },
                        "spoken_name": {"type": "string"},
                        "qty": {"type": "integer", "minimum": 1},
                        "unit_price_naira": {
                            "type": ["number", "null"],
                            "description": (
                                "Only if the owner stated a price for THIS "
                                "sale; else null (the recorded price is used)."
                            ),
                        },
                    },
                    "required": ["spoken_name", "qty"],
                },
            },
            "language": {"type": "string", "enum": LANGS},
        },
        "required": ["lines", "language"],
    },
}


# ---------------- restock extraction ----------------

class RestockExtract(BaseModel):
    inventory_item_id: int | None = None
    spoken_name: str
    qty: int = Field(ge=1)
    unit_cost_naira: float | None = None
    language: str = "en"


RESTOCK_TOOL = {
    "name": "record_restock",
    "description": (
        "The owner bought more stock of an existing item. Extract which item, "
        "how many, and the unit cost if stated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "inventory_item_id": {"type": ["integer", "null"]},
            "spoken_name": {"type": "string"},
            "qty": {"type": "integer", "minimum": 1},
            "unit_cost_naira": {"type": ["number", "null"]},
            "language": {"type": "string", "enum": LANGS},
        },
        "required": ["spoken_name", "qty", "language"],
    },
}