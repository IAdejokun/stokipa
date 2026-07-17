"""System prompts + canned (non-LLM) replies, localized.

Canned replies exist for two reasons: (1) zero LLM cost/latency on fixed
turns, (2) the bot's fixed lines stay stable and on-brand. English and
Nigerian Pidgin are fully written; yo/ha/ig fall back to Pidgin for the MVP
(Pidgin is the lingua franca and universally understood by the target user).
"""

CONTEXT = (
    "You are the language-understanding layer of Stokipa, a WhatsApp "
    "assistant for Nigerian shop owners, many of them semi-literate. Owners "
    "write or speak English, Nigerian Pidgin, Yoruba, Hausa, or Igbo, often "
    "mixed. Numbers may be words. 'k' after a number means thousand naira "
    "(5k = 5000). Detect the language/register the owner used and report it "
    "as: en, pcm (Pidgin), yo, ha, or ig."
)

ITEM_OR_DONE_SYSTEM = CONTEXT + (
    " The owner is currently listing the products they sell, one message at "
    "a time. A product message includes a name, a quantity they have, and a "
    "unit selling price. Examples: 'I get 10 bags of rice, 85000 each' | "
    "'Indomie carton, 15 of them, 11k' | 'garri paint bucket 20, 1500'. "
    "The owner may instead be saying they are finished listing."
)

CONFIRMATION_SYSTEM = CONTEXT + (
    " The assistant just asked the owner to confirm a summary (yes/no "
    "question). Classify the reply."
)

CHECKIN_HOUR_SYSTEM = CONTEXT + (
    " The assistant asked what time of day it should message the owner to "
    "ask about the day's sales."
)


_CANNED: dict[str, dict[str, str]] = {
    "welcome": {
        "en": (
            "Welcome to Stokipa! 🏪 I go help you track your stock, sales "
            "and money — right here for WhatsApp.\n\nFirst, wetin be the "
            "name of your shop?"
        ),
        "pcm": (
            "Welcome to Stokipa! 🏪 I go help you track your stock, sales "
            "and money — right here for WhatsApp.\n\nFirst, wetin be the "
            "name of your shop?"
        ),
    },
    "ask_items": {
        "en": (
            "Nice one, {shop}! 📦 Now tell me the things you sell, one by "
            "one.\n\nExample: *I get 10 bags of rice, ₦85,000 each*\n\n"
            "When you finish, just talk *done*."
        ),
        "pcm": (
            "Nice one, {shop}! 📦 Now tell me wetin you dey sell, one by "
            "one.\n\nExample: *I get 10 bags of rice, ₦85,000 each*\n\n"
            "When you don finish, just talk *done*."
        ),
    },
    "confirm_item": {
        "en": "📦 {name} — {qty} {unit} at {price} each. Correct?",
        "pcm": "📦 {name} — {qty} {unit} for {price} each. E correct?",
    },
    "yes_label": {"en": "Correct ✅", "pcm": "Correct ✅"},
    "no_label": {"en": "No, change am ❌", "pcm": "No, change am ❌"},
    "item_saved": {
        "en": "Saved ✅ Next item — or talk *done* if that's everything.",
        "pcm": "I don save am ✅ Next item — or talk *done* if na everything be that.",
    },
    "item_retry": {
        "en": "No wahala — send the item again with the quantity and price.",
        "pcm": "No wahala — send the item again with how many and the price.",
    },
    "item_unclear": {
        "en": (
            "I no fully get that one. Send the item like this: "
            "*I get 10 bags of rice, ₦85,000 each* — or talk *done* to finish."
        ),
        "pcm": (
            "I no too get that one. Send the item like this: "
            "*I get 10 bags of rice, ₦85,000 each* — or talk *done* make we finish."
        ),
    },
    "duplicate_item": {
        "en": "You don already add *{name}*. Send another item, or talk *done*.",
        "pcm": "You don already add *{name}*. Send another item, or talk *done*.",
    },
    "need_one_item": {
        "en": "Add at least one item first — example: *I get 10 bags of rice, ₦85,000 each*.",
        "pcm": "Abeg add at least one item first — example: *I get 10 bags of rice, ₦85,000 each*.",
    },
    "ask_checkin": {
        "en": (
            "All set! 🎉 Every day I go ask you wetin you sell.\n\nWhich time "
            "you want make I ask? (Example: *8 for evening*)"
        ),
        "pcm": (
            "All don set! 🎉 Every day I go ask you wetin you sell.\n\nWhich "
            "time you want make I dey ask? (Example: *8 for evening*)"
        ),
    },
    "checkin_unclear": {
        "en": "Tell me a time like *8 for evening* or *7 for morning*.",
        "pcm": "Talk time like *8 for evening* or *7 for morning*.",
    },
    "setup_done": {
        "en": (
            "Ready! ✅ {shop} dey track {count} items now. I go message you "
            "around {hour} every day.\n\nAnytime you sell something, just "
            "tell me — like *I sold 2 bags of rice*."
        ),
        "pcm": (
            "E don ready! ✅ {shop} dey track {count} items now. I go message "
            "you around {hour} every day.\n\nAnytime you sell something, just "
            "tell me — like *I sell 2 bags of rice*."
        ),
    },
    "help_idle": {
        "en": (
            "Tell me about your sales — like *I sold 2 bags of rice* — and I "
            "go update your records. (Sales tracking dey come for the next "
            "update — for now I don save your shop setup.)"
        ),
        "pcm": (
            "Tell me wetin you sell — like *I sell 2 bags of rice* — make I "
            "update your records. (Sales tracking dey come next update — for "
            "now I don save your shop setup.)"
        ),
    },
    "error_retry": {
        "en": "Sorry, something went wrong. Abeg try again.",
        "pcm": "Sorry o, something spoil small. Abeg try again.",
    },
}


def canned(key: str, lang: str, **fmt: object) -> str:
    variants = _CANNED[key]
    # yo/ha/ig fall back to Pidgin, then English.
    text = variants.get(lang) or variants.get("pcm") or variants["en"]
    return text.format(**fmt) if fmt else text


def fmt_hour(hour: int) -> str:
    if hour == 0:
        return "12 midnight"
    if hour < 12:
        return f"{hour} in the morning"
    if hour == 12:
        return "12 noon"
    if hour < 17:
        return f"{hour - 12} in the afternoon"
    return f"{hour - 12} for evening"