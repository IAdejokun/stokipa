"""Milestone 4 tests: onboarding state machine end-to-end with a stubbed LLM.

The LLM seam (app.llm.service) is replaced by FakeLLM so tests are
deterministic and offline. What we verify is OUR logic: state transitions,
pending_action lifecycle, transactional item creation, button fast-path.
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.llm import service
from app.llm.tools import CheckinHour, ConfirmationVerdict, ItemOrDone, ParsedItem
from app.models import (
    ConvoState, Item, Message, Sale, StockMove, StockMoveType, User,
)
from app.pipeline.ingest import ingest_webhook


# ---------------- fixtures ----------------

class FakeLLM:
    """Queue of canned responses per function; raises if exhausted."""

    def __init__(self) -> None:
        self.item_or_done: list[ItemOrDone] = []
        self.confirmations: list[ConfirmationVerdict] = []
        self.hours: list[CheckinHour] = []

    async def extract_item_or_done(self, text: str) -> ItemOrDone:
        return self.item_or_done.pop(0)

    async def interpret_confirmation(self, text: str) -> ConfirmationVerdict:
        return self.confirmations.pop(0)

    async def parse_checkin_hour(self, text: str) -> CheckinHour:
        return self.hours.pop(0)


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(service, "extract_item_or_done", fake.extract_item_or_done)
    monkeypatch.setattr(service, "interpret_confirmation", fake.interpret_confirmation)
    monkeypatch.setattr(service, "parse_checkin_hour", fake.parse_checkin_hour)
    # onboarding.py did `from app.llm import service as llm` — same module
    # object, so patching service's attributes covers it.
    return fake


@pytest.fixture
def wa_id() -> str:
    # unique per test so tests never collide
    return "23480" + uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
async def cleanup(wa_id):
    yield
    async with SessionLocal() as s:
        user = (await s.execute(
            select(User).where(User.wa_id == wa_id)
        )).scalar_one_or_none()
        if user:
            items = (await s.execute(
                select(Item).where(Item.user_id == user.id)
            )).scalars().all()
            for it in items:
                await s.execute(delete(StockMove).where(StockMove.item_id == it.id))
            await s.execute(delete(Item).where(Item.user_id == user.id))
            await s.execute(delete(User).where(User.id == user.id))
        await s.execute(delete(Message).where(Message.raw["from"].astext == wa_id))
        await s.commit()


def _inbound(wa_id: str, text: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Test Owner"}}],
        "messages": [{
            "id": f"wamid.ob.{uuid.uuid4()}", "from": wa_id,
            "type": "text", "text": {"body": text},
        }],
    }}]}]}


def _button(wa_id: str, button_id: str, title: str) -> dict:
    return {"entry": [{"changes": [{"value": {
        "messages": [{
            "id": f"wamid.ob.{uuid.uuid4()}", "from": wa_id,
            "type": "interactive",
            "interactive": {"button_reply": {"id": button_id, "title": title}},
        }],
    }}]}]}


async def _user(wa_id: str) -> User:
    async with SessionLocal() as s:
        return (await s.execute(
            select(User).where(User.wa_id == wa_id)
        )).scalar_one()


RICE = ItemOrDone(
    action="add_item",
    item=ParsedItem(name="Rice (50kg bag)", unit="bag", qty=10,
                    price_naira=85000),
    language="pcm",
)
DONE = ItemOrDone(action="done", language="pcm")


# ---------------- the happy path, step by step ----------------

async def test_full_onboarding_flow(fake_llm, outbox, wa_id):
    # 1. First contact -> welcome, ask shop name
    await ingest_webhook(_inbound(wa_id, "hello"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_NAME
    assert "name of your shop" in outbox.sent[-1]["body"]

    # 2. Shop name -> ask for items
    await ingest_webhook(_inbound(wa_id, "Mama Nkechi Provisions"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_ITEMS
    assert u.shop_name == "Mama Nkechi Provisions"

    # 3. Item message -> confirm buttons + pending action
    fake_llm.item_or_done.append(RICE)
    await ingest_webhook(_inbound(wa_id, "I get 10 bags of rice 85k each"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.AWAITING_ITEM_CONFIRM
    assert u.pending_action["name"] == "Rice (50kg bag)"
    assert outbox.sent[-1]["kind"] == "buttons"
    assert "₦85,000" in outbox.sent[-1]["body"]

    # 4. Button YES (no LLM call needed) -> item saved with INITIAL move
    await ingest_webhook(_button(wa_id, "confirm_yes", "Correct ✅"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_ITEMS
    assert u.pending_action is None
    async with SessionLocal() as s:
        item = (await s.execute(
            select(Item).where(Item.user_id == u.id)
        )).scalar_one()
        move = (await s.execute(
            select(StockMove).where(StockMove.item_id == item.id)
        )).scalar_one()
    assert item.qty == 10
    assert item.price_kobo == 8_500_000
    assert item.low_stock_at == 2  # max(2, ceil(10*0.2)) = 2
    assert move.type == StockMoveType.INITIAL and move.delta == 10

    # 5. "done" -> ask check-in time
    fake_llm.item_or_done.append(DONE)
    await ingest_webhook(_inbound(wa_id, "done"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_CHECKIN_TIME

    # 6. Hour -> IDLE, hour stored, summary sent
    fake_llm.hours.append(CheckinHour(hour=20, language="pcm"))
    await ingest_webhook(_inbound(wa_id, "8 for evening"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.IDLE
    assert u.checkin_hour == 20
    assert "1 items" in outbox.sent[-1]["body"] or "1 item" in outbox.sent[-1]["body"]
    assert u.language == "pcm"  # language detected and stored


async def test_confirm_no_returns_to_items(fake_llm, outbox, wa_id):
    await ingest_webhook(_inbound(wa_id, "hi"))
    await ingest_webhook(_inbound(wa_id, "My Shop"))
    fake_llm.item_or_done.append(RICE)
    await ingest_webhook(_inbound(wa_id, "rice 10 bags"))
    await ingest_webhook(_button(wa_id, "confirm_no", "No, change am ❌"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_ITEMS
    assert u.pending_action is None
    async with SessionLocal() as s:
        count = len((await s.execute(
            select(Item).where(Item.user_id == u.id)
        )).all())
    assert count == 0  # nothing saved


async def test_new_item_while_awaiting_confirm_abandons_pending(
    fake_llm, outbox, wa_id
):
    await ingest_webhook(_inbound(wa_id, "hi"))
    await ingest_webhook(_inbound(wa_id, "My Shop"))
    fake_llm.item_or_done.append(RICE)
    await ingest_webhook(_inbound(wa_id, "rice 10 bags 85k"))

    # Owner ignores buttons and types a different item.
    fake_llm.confirmations.append(ConfirmationVerdict(verdict="other", language="pcm"))
    fake_llm.item_or_done.append(ItemOrDone(
        action="add_item",
        item=ParsedItem(name="Milo (tin)", unit="tin", qty=24, price_naira=2500),
        language="pcm",
    ))
    await ingest_webhook(_inbound(wa_id, "milo 24 tins 2500"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.AWAITING_ITEM_CONFIRM
    assert u.pending_action["name"] == "Milo (tin)"  # old pending replaced


async def test_done_with_zero_items_rejected(fake_llm, outbox, wa_id):
    await ingest_webhook(_inbound(wa_id, "hi"))
    await ingest_webhook(_inbound(wa_id, "My Shop"))
    fake_llm.item_or_done.append(DONE)
    await ingest_webhook(_inbound(wa_id, "done"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_ITEMS  # not advanced
    assert "at least one item" in outbox.sent[-1]["body"]


async def test_duplicate_item_name_handled(fake_llm, outbox, wa_id):
    await ingest_webhook(_inbound(wa_id, "hi"))
    await ingest_webhook(_inbound(wa_id, "My Shop"))
    for _ in range(2):
        fake_llm.item_or_done.append(RICE)
    await ingest_webhook(_inbound(wa_id, "rice 10 bags 85k"))
    await ingest_webhook(_button(wa_id, "confirm_yes", "Correct ✅"))
    await ingest_webhook(_inbound(wa_id, "rice 10 bags 85k"))  # again
    await ingest_webhook(_button(wa_id, "confirm_yes", "Correct ✅"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_ITEMS
    assert "already add" in outbox.sent[-1]["body"]
    async with SessionLocal() as s:
        count = len((await s.execute(
            select(Item).where(Item.user_id == u.id)
        )).all())
    assert count == 1


async def test_unclear_checkin_hour_reasks(fake_llm, outbox, wa_id):
    await ingest_webhook(_inbound(wa_id, "hi"))
    await ingest_webhook(_inbound(wa_id, "My Shop"))
    fake_llm.item_or_done.append(RICE)
    await ingest_webhook(_inbound(wa_id, "rice"))
    await ingest_webhook(_button(wa_id, "confirm_yes", "Correct ✅"))
    fake_llm.item_or_done.append(DONE)
    await ingest_webhook(_inbound(wa_id, "done"))
    fake_llm.hours.append(CheckinHour(hour=None, language="pcm"))
    await ingest_webhook(_inbound(wa_id, "whenever"))
    u = await _user(wa_id)
    assert u.convo_state == ConvoState.ONBOARDING_CHECKIN_TIME  # still asking