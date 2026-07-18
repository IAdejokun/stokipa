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

INTENT_SYSTEM = CONTEXT + (
    " Classify the owner's message. Reporting a sale, buying more stock of "
    "an existing item, introducing a brand-new product, asking a question "
    "about stock or money, or something else."
)

SALE_SYSTEM = CONTEXT + (
    " The owner is reporting what they sold. Extract every line item.\n"
    "Owner's inventory (id | name | unit | price naira | stock):\n{inventory}\n"
    "Match spoken names generously ('indomie' -> the Indomie item, 'mineral' "
    "-> soft drinks). If not confident, set inventory_item_id to null and "
    "keep the spoken name exactly as said."
)

RESTOCK_SYSTEM = CONTEXT + (
    " The owner bought more stock of one of their existing items.\n"
    "Owner's inventory (id | name | unit | price naira | stock):\n{inventory}\n"
    "If not confident which item, set inventory_item_id to null."
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

_CANNED.update({
    "confirm_sale": {
        "en": "🧾 You sold:\n{lines}\n\nTotal: *{total}*. Correct?",
        "pcm": "🧾 You sell:\n{lines}\n\nTotal: *{total}*. E correct?",
    },
    "guardian_invite": {
        "en": ("No wahala! Send this code to the person wey you want make e dey "
               "monitor the shop:\n\n*{code}*\n\nMake dem send am to this same "
               "number for WhatsApp, then you go confirm am."),
        "pcm": ("No wahala! Send this code give the person wey you wan make e dey "
                "look the shop matter:\n\n*{code}*\n\nMake dem send am come this "
                "same number for WhatsApp, then you go confirm am."),
    },
    "guardian_code_received": {
        "en": "Code received ✅ I don ask the shop owner to confirm. Once dem gree, you go start to dey receive updates.",
        "pcm": "I don receive the code ✅ I don ask the shop owner make e confirm. Once e gree, you go dey receive update.",
    },
    "guardian_confirm_ask": {
        "en": "👤 *{name}* ({number}) wan dey monitor {shop} — dem go receive weekly summary and alert if the shop quiet. You gree?",
        "pcm": "👤 *{name}* ({number}) wan dey look {shop} matter — dem go receive weekly summary and alert if shop quiet. You gree?",
    },
    "guardian_reask": {
        "en": "Abeg tap ✅ to allow am or ❌ to reject am.",
        "pcm": "Abeg tap ✅ make you gree or ❌ make you no gree.",
    },
    "guardian_active_owner": {
        "en": "Done ✅ *{name}* go now dey receive weekly updates about the shop.",
        "pcm": "E don set ✅ *{name}* go dey receive weekly update about the shop.",
    },
    "guardian_active_guardian": {
        "en": "The owner don confirm ✅ You go dey receive weekly summary of {shop}, and I go alert you if the shop quiet for more than 2 days.",
        "pcm": "Owner don gree ✅ You go dey receive weekly summary of {shop}, and I go alert you if shop quiet pass 2 days.",
    },
    "guardian_declined_owner": {
        "en": "No wahala — I no add am.",
        "pcm": "No wahala — I no add am.",
    },
    "guardian_declined_guardian": {
        "en": "The owner never approve the link this time.",
        "pcm": "Owner no approve the link this time.",
    },
    "guardian_invalid_code": {
        "en": "That code no valid or e don expire. Ask the shop owner to send you a fresh one.",
        "pcm": "That code no correct or e don expire. Ask the shop owner make e send you new one.",
    },
    "guardian_self_link": {
        "en": "You no fit be your own guardian 😄 Share the code with family or friend instead.",
        "pcm": "You no fit be your own guardian 😄 Share the code give family or friend.",
    },
    "quiet_alert": {
        "en": "🔕 *{shop}* never send any update since {days} days. You fit check on them make sure everything dey okay.",
        "pcm": "🔕 *{shop}* never send any update reach {days} days now. Abeg you fit check on dem make sure say everything dey alright.",
    },
    "weekly_digest_header": {
        "en": "📊 *Weekly summary — {shop}*",
        "pcm": "📊 *Weekly summary — {shop}*",
    },
    "share_shop": {
        "en": "Here be your shop page 🏪\n{url}\n\nShare am for WhatsApp Status, groups, anywhere! Customers fit see wetin you sell and order straight to your WhatsApp.",
        "pcm": "See your shop page 🏪\n{url}\n\nShare am for WhatsApp Status, group, anywhere! Customer fit see wetin you dey sell and order enter your WhatsApp sharp sharp.",
    },
    "daily_checkin": {
        "en": "Good evening! 🌙 Wetin you sell today? Tell me — like *I sold 2 bags of rice and 1 carton of milk* — make I update your records.",
        "pcm": "Good evening! 🌙 Wetin you sell today? Talk am — like *I sell 2 bags of rice and 1 carton of milk* — make I update your book.",
    },
    "sale_unmatched_note": {
        "en": "\n\n⚠️ I no see *{names}* for your stock list, so I never count am. You fit add am as new item later.",
        "pcm": "\n\n⚠️ I no see *{names}* for your stock list, so I never count am. You fit add am as new item later.",
    },
    "sale_none_matched": {
        "en": "⚠️ I no see *{names}* for your stock list. Add am first — talk something like *I get 10 packs of chinchin, ₦500 each*.",
        "pcm": "⚠️ I no see *{names}* for your stock list. Add am first — talk something like *I get 10 packs of chinchin, ₦500 each*.",
    },
    "sale_saved": {
        "en": "Recorded ✅ Total: *{total}*. Well done!",
        "pcm": "I don record am ✅ Total: *{total}*. Weldone!",
    },
    "sale_retry": {
        "en": "No wahala, I cancel am. Tell me again wetin you sell.",
        "pcm": "No wahala, I don cancel am. Talk am again make I hear.",
    },
    "confirm_restock": {
        "en": "📦 Adding {qty} {unit} of {name}{cost}. Correct?",
        "pcm": "📦 I wan add {qty} {unit} of {name}{cost}. E correct?",
    },
    "restock_saved": {
        "en": "Done ✅ {name}: now {qty} {unit} in stock.",
        "pcm": "E don enter ✅ {name}: na {qty} {unit} dey now.",
    },
    "restock_unmatched": {
        "en": "I no see *{name}* for your stock list. If na new item, talk am like *I get 10 packs of chinchin, ₦500 each*.",
        "pcm": "I no see *{name}* for your stock list. If na new item, talk am like *I get 10 packs of chinchin, ₦500 each*.",
    },
    "low_stock": {
        "en": "⚠️ *{name}* don low — only {qty} {unit} remain. Time to buy more!",
        "pcm": "⚠️ *{name}* don dey finish — na only {qty} {unit} remain. Make you think of buying more!",
    },
    "stock_one": {
        "en": "📦 {name}: {qty} {unit} remain.",
        "pcm": "📦 {name}: {qty} {unit} remain.",
    },
    "stock_item_unknown": {
        "en": "I no see *{name}* for your stock list.",
        "pcm": "I no see *{name}* for your stock list.",
    },
    "stock_all_header": {
        "en": "📦 Your stock:",
        "pcm": "📦 Your stock:",
    },
    "period_today": {"en": "today", "pcm": "today"},
    "period_week": {"en": "this week", "pcm": "this week"},
    "period_month": {"en": "this month", "pcm": "this month"},
    "revenue_report": {
        "en": "💰 You made *{total}* {period_label} from {count} sale(s).",
        "pcm": "💰 You don make *{total}* {period_label} from {count} sale(s).",
    },
    "no_sales_yet": {
        "en": "No sales recorded {period_label} yet. When you sell, just tell me!",
        "pcm": "No sale don enter {period_label} yet. Anytime you sell, just talk am!",
    },
    "help_full": {
        "en": ("You fit tell me:\n• *I sold 2 bags of rice* — record a sale\n"
               "• *I buy 5 more cartons of milk* — add stock\n"
               "• *How many rice remain?* — check stock\n"
               "• *How much I make today?* — check money"),
        "pcm": ("You fit talk:\n• *I sell 2 bags of rice* — make I record am\n"
                "• *I buy 5 more cartons of milk* — add stock\n"
                "• *How many rice remain?* — check stock\n"
                "• *How much I don make today?* — check money"),
    },
    "item_saved_idle": {
        "en": "Added ✅ I dey track am now.",
        "pcm": "I don add am ✅ I dey track am now.",
    },
})

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