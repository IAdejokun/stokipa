"""Owner questions: stock levels, revenue, top sellers. Read-only — answers
come straight from Postgres; the LLM only classified the question.
"""

from app.domain import reports
from app.lib.matching import best_match, MATCH_THRESHOLD
from app.lib.money import fmt_naira
from app.llm.prompts import canned
from app.llm.tools import Intent
from app.models import User
from app.whatsapp.client import wa


async def handle(user: User, intent: Intent) -> None:
    lang = intent.language
    if intent.query_kind == "stock_level":
        await _stock(user, intent, lang)
    elif intent.query_kind in ("revenue", "top_sellers"):
        await _money(user, intent, lang)
    else:
        await wa.send_text(user.wa_id, canned("help_full", lang))


async def _stock(user: User, intent: Intent, lang: str) -> None:
    items = await reports.stock_levels(user.id)
    if intent.item_name:
        item, score = best_match(intent.item_name, items)
        if item is None or score < MATCH_THRESHOLD:
            await wa.send_text(user.wa_id, canned(
                "stock_item_unknown", lang, name=intent.item_name))
            return
        await wa.send_text(user.wa_id, canned(
            "stock_one", lang, name=item.name, qty=item.qty, unit=item.unit))
        return

    lines = "\n".join(f"• {i.name}: {i.qty} {i.unit}" for i in items)
    await wa.send_text(
        user.wa_id, canned("stock_all_header", lang) + "\n" + lines)


async def _money(user: User, intent: Intent, lang: str) -> None:
    period = intent.period or "today"
    label = canned(f"period_{period}", lang)
    total, count = await reports.revenue(user.id, period)
    if count == 0:
        await wa.send_text(user.wa_id, canned(
            "no_sales_yet", lang, period_label=label))
        return
    body = canned("revenue_report", lang, period_label=label,
                  total=fmt_naira(total), count=count)
    if intent.query_kind == "top_sellers":
        top = await reports.top_sellers(user.id, period)
        if top:
            body += "\n🏆 " + ", ".join(f"{n} ({u})" for n, u in top)
    await wa.send_text(user.wa_id, body)