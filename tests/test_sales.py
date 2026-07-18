"""Milestone 5 tests: sales, restock, low-stock alerts, queries.

LLM stubbed (FakeLLM extended); everything else real against Postgres.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.llm import service
from app.llm.tools import (
    ConfirmationVerdict, Intent, RestockExtract, SaleExtract, SaleLineOut,
)
from app.models import (
    ConvoState, Item, Message, Sale, SaleLine, StockMove, StockMoveType, User,
)
from app.pipeline.ingest import ingest_webhook


class FakeLLM:
    def __init__(self) -> None:
        self.intents: list[Intent] = []
        self.sales: list[SaleExtract] = []
        self.restocks: list[RestockExtract] = []
        self.confirmations: list[ConfirmationVerdict] = []

    async def classify_intent(self, text):
        return self.intents.pop(0)

    async def extract_sale(self, text, inventory_table):
        self.last_inventory_table = inventory_table
        return self.sales.pop(0)

    async def extract_restock(self, text, inventory_table):
        return self.restocks.pop(0)

    async def interpret_confirmation(self, text):
        return self.confirmations.pop(0)


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    for name in ("classify_intent", "extract_sale", "extract_restock",
                 "interpret_confirmation"):
        monkeypatch.setattr(service, name, getattr(fake, name))
    return fake


@pytest.fixture
def wa_id() -> str:
    return "23481" + uuid.uuid4().hex[:8]


@pytest.fixture
async def shop(wa_id):
    """A ready IDLE user with two items: rice (10 @ ₦85,000, low@2) and
    milk cartons (5 @ ₦9,500, low@2)."""
    async with SessionLocal() as s:
        user = User(wa_id=wa_id, shop_name="Test Shop", language="pcm",
                    convo_state=ConvoState.IDLE, checkin_hour=20)
        s.add(user)
        await s.flush()
        rice = Item(user_id=user.id, name="Rice (50kg bag)", unit="bag",
                    qty=10, price_kobo=8_500_000, low_stock_at=2)
        milk = Item(user_id=user.id, name="Peak Milk (carton)", unit="carton",
                    qty=5, price_kobo=950_000, low_stock_at=2)
        s.add_all([rice, milk])
        await s.commit()
        ids = {"user": user.id, "rice": rice.id, "milk": milk.id}
    yield ids
    async with SessionLocal() as s:
        sales = (await s.execute(
            select(Sale).where(Sale.user_id == ids["user"]))).scalars().all()
        for sale in sales:
            await s.execute(delete(SaleLine).where(SaleLine.sale_id == sale.id))
        await s.execute(delete(Sale).where(Sale.user_id == ids["user"]))
        for iid in (ids["rice"], ids["milk"]):
            await s.execute(delete(StockMove).where(StockMove.item_id == iid))
        await s.execute(delete(Item).where(Item.user_id == ids["user"]))
        await s.execute(delete(User).where(User.id == ids["user"]))
        await s.commit()


def _inbound(wa_id: str, text: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "messages": [{
            "id": f"wamid.s5.{uuid.uuid4()}", "from": wa_id,
            "type": "text", "text": {"body": text},
        }],
    }}]}]}


def _button(wa_id: str, button_id: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "messages": [{
            "id": f"wamid.s5.{uuid.uuid4()}", "from": wa_id,
            "type": "interactive",
            "interactive": {"button_reply": {"id": button_id, "title": "x"}},
        }],
    }}]}]}


async def _item(item_id: int) -> Item:
    async with SessionLocal() as s:
        return (await s.execute(
            select(Item).where(Item.id == item_id))).scalar_one()


async def _user(wa_id: str) -> User:
    async with SessionLocal() as s:
        return (await s.execute(
            select(User).where(User.wa_id == wa_id))).scalar_one()


SALE_INTENT = Intent(type="log_sale", language="pcm")


# ---------------- sale flow ----------------

async def test_sale_happy_path(fake_llm, outbox, wa_id, shop):
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["rice"], spoken_name="rice", qty=3),
        SaleLineOut(inventory_item_id=shop["milk"], spoken_name="milk", qty=1),
    ]))
    await ingest_webhook(_inbound(wa_id, "I sell 3 bags of rice and 1 milk"))

    u = await _user(wa_id)
    assert u.convo_state == ConvoState.AWAITING_SALE_CONFIRM
    # 3*85,000 + 1*9,500 = 264,500
    assert "₦264,500" in outbox.sent[-1]["body"]

    await ingest_webhook(_button(wa_id, "confirm_yes"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.IDLE
    rice = await _item(shop["rice"])
    milk = await _item(shop["milk"])
    assert rice.qty == 7 and milk.qty == 4

    async with SessionLocal() as s:
        sale = (await s.execute(
            select(Sale).where(Sale.user_id == shop["user"]))).scalar_one()
        moves = (await s.execute(
            select(StockMove).where(
                StockMove.type == StockMoveType.SALE,
                StockMove.item_id.in_([shop["rice"], shop["milk"]]),
            ))).scalars().all()
    assert sale.total_kobo == 26_450_000
    assert sorted(m.delta for m in moves) == [-3, -1]


async def test_sale_confirm_no_leaves_stock_untouched(fake_llm, outbox, wa_id, shop):
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["rice"], spoken_name="rice", qty=3),
    ]))
    await ingest_webhook(_inbound(wa_id, "I sell 3 bags of rice"))
    await ingest_webhook(_button(wa_id, "confirm_no"))
    rice = await _item(shop["rice"])
    assert rice.qty == 10
    async with SessionLocal() as s:
        count = len((await s.execute(
            select(Sale).where(Sale.user_id == shop["user"]))).all())
    assert count == 0


async def test_low_stock_alert_fires_once(fake_llm, outbox, wa_id, shop):
    # milk: 5 in stock, low_stock_at=2. Sell 3 -> qty 2 -> crosses.
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["milk"], spoken_name="milk", qty=3),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 3 cartons of milk"))
    await ingest_webhook(_button(wa_id, "confirm_yes"))
    alert_msgs = [m for m in outbox.sent if "⚠️" in m["body"]]
    assert len(alert_msgs) == 1 and "Peak Milk" in alert_msgs[0]["body"]

    # Sell 1 more (2 -> 1): still low, but alert must NOT repeat.
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["milk"], spoken_name="milk", qty=1),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 1 milk"))
    await ingest_webhook(_button(wa_id, "confirm_yes"))
    alert_msgs = [m for m in outbox.sent if "⚠️" in m["body"]]
    assert len(alert_msgs) == 1


async def test_overselling_clamps_to_zero_with_adjustment(
    fake_llm, outbox, wa_id, shop
):
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["milk"], spoken_name="milk", qty=8),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 8 cartons of milk"))
    await ingest_webhook(_button(wa_id, "confirm_yes"))
    milk = await _item(shop["milk"])
    assert milk.qty == 0  # clamped, sale not blocked
    async with SessionLocal() as s:
        adj = (await s.execute(
            select(StockMove).where(
                StockMove.item_id == shop["milk"],
                StockMove.type == StockMoveType.ADJUSTMENT,
            ))).scalar_one()
        sale = (await s.execute(
            select(Sale).where(Sale.user_id == shop["user"]))).scalar_one()
    assert adj.delta == 3  # 8 sold vs 5 recorded
    assert sale.total_kobo == 8 * 950_000


async def test_unmatched_item_excluded_and_flagged(fake_llm, outbox, wa_id, shop):
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["rice"], spoken_name="rice", qty=2),
        SaleLineOut(inventory_item_id=None, spoken_name="chinchin", qty=4),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 2 rice and 4 chinchin"))
    body = outbox.sent[-1]["body"]
    assert "chinchin" in body and "⚠️" in body   # flagged
    assert "₦170,000" in body                     # only rice priced


async def test_llm_wrong_id_distrusted(fake_llm, outbox, wa_id, shop):
    """LLM claims 'chinchin' is the rice item — fuzzy disagrees, so the line
    must be treated as unmatched rather than silently selling rice."""
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["rice"], spoken_name="chinchin", qty=2),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 2 chinchin"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.IDLE  # nothing to confirm
    assert "chinchin" in outbox.sent[-1]["body"]


# ---------------- restock ----------------

async def test_restock_happy_path(fake_llm, outbox, wa_id, shop):
    fake_llm.intents.append(Intent(type="restock", language="pcm"))
    fake_llm.restocks.append(RestockExtract(
        inventory_item_id=shop["milk"], spoken_name="milk", qty=5,
        unit_cost_naira=9000, language="pcm"))
    await ingest_webhook(_inbound(wa_id, "I buy 5 more cartons of milk 9k each"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.AWAITING_RESTOCK_CONFIRM

    await ingest_webhook(_button(wa_id, "confirm_yes"))
    milk = await _item(shop["milk"])
    assert milk.qty == 10
    assert milk.cost_kobo == 900_000
    assert milk.low_stock_alerted is False


# ---------------- queries ----------------

async def test_stock_query_single_item(fake_llm, outbox, wa_id, shop):
    fake_llm.intents.append(Intent(
        type="query", query_kind="stock_level", item_name="rice",
        language="pcm"))
    await ingest_webhook(_inbound(wa_id, "how many rice remain"))
    assert "Rice (50kg bag)" in outbox.sent[-1]["body"]
    assert "10 bag" in outbox.sent[-1]["body"]


async def test_revenue_query_after_sale(fake_llm, outbox, wa_id, shop):
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["rice"], spoken_name="rice", qty=2),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 2 rice"))
    await ingest_webhook(_button(wa_id, "confirm_yes"))

    fake_llm.intents.append(Intent(
        type="query", query_kind="revenue", period="today", language="pcm"))
    await ingest_webhook(_inbound(wa_id, "how much I make today"))
    assert "₦170,000" in outbox.sent[-1]["body"]


async def test_new_sale_while_awaiting_confirm_abandons_and_reroutes(
    fake_llm, outbox, wa_id, shop
):
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["rice"], spoken_name="rice", qty=3),
    ]))
    await ingest_webhook(_inbound(wa_id, "sold 3 rice"))

    # Owner ignores buttons, reports a different sale instead.
    fake_llm.confirmations.append(ConfirmationVerdict(verdict="other",
                                                      language="pcm"))
    fake_llm.intents.append(SALE_INTENT)
    fake_llm.sales.append(SaleExtract(language="pcm", lines=[
        SaleLineOut(inventory_item_id=shop["milk"], spoken_name="milk", qty=2),
    ]))
    await ingest_webhook(_inbound(wa_id, "no I mean 2 milk"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.AWAITING_SALE_CONFIRM
    assert u.pending_action["lines"][0]["item_id"] == shop["milk"]
    rice = await _item(shop["rice"])
    assert rice.qty == 10  # abandoned sale never committed