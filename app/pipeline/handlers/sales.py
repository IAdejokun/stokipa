"""Sales & restock conversation flow (IDLE-state intents).

log_sale:
  IDLE ──"I sell 3 bags of rice"──► extract ──► resolve matches ──►
  AWAITING_SALE_CONFIRM (buttons) ──yes──► commit_sale ──► alerts ──► IDLE

restock:
  IDLE ──"I buy 5 more cartons"──► extract ──► resolve ──►
  AWAITING_RESTOCK_CONFIRM ──yes──► commit_restock ──► IDLE

Same confirmation philosophy as onboarding: nothing touches the ledger until
the owner says yes; 'other' replies abandon the pending action and re-route.
"""

import structlog

from app.db import SessionLocal
from app.domain.alerts import send_low_stock_alerts
from app.domain.reports import stock_levels
from app.domain.sales import commit_restock, commit_sale
from app.lib.matching import resolve
from app.lib.money import fmt_naira, naira_to_kobo
from app.llm import service as llm
from app.llm.prompts import canned
from app.models import ConvoState, User
from app.whatsapp.client import wa

log = structlog.get_logger()


def _inventory_table(items) -> str:
    return "\n".join(
        f"{i.id} | {i.name} | {i.unit} | {i.price_kobo // 100} | {i.qty}"
        for i in items
    ) or "(empty)"


async def _save(user_id: int, **fields) -> None:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        for k, v in fields.items():
            setattr(user, k, v)
        await session.commit()


# ---------------- sale ----------------

async def start_sale(user: User, wamid: str, text: str) -> None:
    items = await stock_levels(user.id)
    extract = await llm.extract_sale(text, _inventory_table(items))
    lang = extract.language
    await _save(user.id, language=lang)

    matched: list[dict] = []
    unmatched: list[str] = []
    for line in extract.lines:
        item = resolve(line.spoken_name, line.inventory_item_id, items)
        if item is None:
            unmatched.append(line.spoken_name)
            continue
        price_kobo = (
            naira_to_kobo(line.unit_price_naira)
            if line.unit_price_naira is not None else item.price_kobo
        )
        matched.append({
            "item_id": item.id, "name": item.name, "unit": item.unit,
            "qty": line.qty, "unit_price_kobo": price_kobo,
        })

    if not matched:
        names = ", ".join(unmatched) or "that"
        await wa.send_text(user.wa_id,
                           canned("sale_none_matched", lang, names=names))
        return

    total = sum(l["qty"] * l["unit_price_kobo"] for l in matched)
    lines_txt = "\n".join(
        f"• {l['qty']} {l['unit']} {l['name']} — {fmt_naira(l['qty'] * l['unit_price_kobo'])}"
        for l in matched
    )
    body = canned("confirm_sale", lang, lines=lines_txt, total=fmt_naira(total))
    if unmatched:
        body += canned("sale_unmatched_note", lang, names=", ".join(unmatched))

    await _save(
        user.id,
        convo_state=ConvoState.AWAITING_SALE_CONFIRM,
        pending_action={"kind": "sale", "wamid": wamid, "lines": matched},
    )
    await wa.send_confirm_buttons(
        user.wa_id, body, canned("yes_label", lang), canned("no_label", lang)
    )


# ---------------- restock ----------------

async def start_restock(user: User, wamid: str, text: str) -> None:
    items = await stock_levels(user.id)
    extract = await llm.extract_restock(text, _inventory_table(items))
    lang = extract.language
    await _save(user.id, language=lang)

    item = resolve(extract.spoken_name, extract.inventory_item_id, items)
    if item is None:
        await wa.send_text(user.wa_id, canned(
            "restock_unmatched", lang, name=extract.spoken_name))
        return

    cost_kobo = (
        naira_to_kobo(extract.unit_cost_naira)
        if extract.unit_cost_naira is not None else None
    )
    cost_txt = f" for {fmt_naira(cost_kobo)} each" if cost_kobo else ""
    await _save(
        user.id,
        convo_state=ConvoState.AWAITING_RESTOCK_CONFIRM,
        pending_action={
            "kind": "restock", "item_id": item.id, "name": item.name,
            "unit": item.unit, "qty": extract.qty, "unit_cost_kobo": cost_kobo,
        },
    )
    await wa.send_confirm_buttons(
        user.wa_id,
        canned("confirm_restock", lang, qty=extract.qty, unit=item.unit,
               name=item.name, cost=cost_txt),
        canned("yes_label", lang), canned("no_label", lang),
    )


# ---------------- confirmation (both kinds) ----------------

async def handle_confirmation(
    user: User, wamid: str, text: str, button_id: str | None
) -> None:
    lang = user.language
    if button_id == "confirm_yes":
        verdict = "yes"
    elif button_id == "confirm_no":
        verdict = "no"
    else:
        verdict = (await llm.interpret_confirmation(text)).verdict

    pending = user.pending_action or {}
    kind = pending.get("kind")

    if verdict == "yes" and kind == "sale":
        sale, crossed = await commit_sale(
            user.id, pending["lines"], pending.get("wamid"))
        await _save(user.id, pending_action=None, convo_state=ConvoState.IDLE)
        await wa.send_text(user.wa_id, canned(
            "sale_saved", lang, total=fmt_naira(sale.total_kobo)))
        await send_low_stock_alerts(user.wa_id, lang, crossed)
        return

    if verdict == "yes" and kind == "restock":
        item = await commit_restock(
            pending["item_id"], pending["qty"], pending.get("unit_cost_kobo"))
        await _save(user.id, pending_action=None, convo_state=ConvoState.IDLE)
        await wa.send_text(user.wa_id, canned(
            "restock_saved", lang, name=item.name, qty=item.qty,
            unit=item.unit))
        return

    if verdict == "no":
        await _save(user.id, pending_action=None, convo_state=ConvoState.IDLE)
        await wa.send_text(user.wa_id, canned("sale_retry", lang))
        return

    # 'other' — the owner moved on. Abandon pending and re-route the new
    # message through the normal IDLE flow (import here to avoid a cycle).
    from app.pipeline.router import route_idle
    await _save(user.id, pending_action=None, convo_state=ConvoState.IDLE)
    async with SessionLocal() as session:
        fresh = await session.get(User, user.id)
    await route_idle(fresh, wamid, text)