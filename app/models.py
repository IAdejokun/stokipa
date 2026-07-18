import enum
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class ConvoState(str, enum.Enum):
    NEW = "NEW"
    ONBOARDING_NAME = "ONBOARDING_NAME"
    ONBOARDING_ITEMS = "ONBOARDING_ITEMS"
    ONBOARDING_CHECKIN_TIME = "ONBOARDING_CHECKIN_TIME"
    IDLE = "IDLE"
    AWAITING_ITEM_CONFIRM = "AWAITING_ITEM_CONFIRM"
    AWAITING_SALE_CONFIRM = "AWAITING_SALE_CONFIRM"
    AWAITING_RESTOCK_CONFIRM = "AWAITING_RESTOCK_CONFIRM"
    AWAITING_GUARDIAN_CONSENT = "AWAITING_GUARDIAN_CONSENT"


class StockMoveType(str, enum.Enum):
    INITIAL = "INITIAL"
    SALE = "SALE"
    RESTOCK = "RESTOCK"
    ADJUSTMENT = "ADJUSTMENT"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "checkin_hour IS NULL OR (checkin_hour >= 0 AND checkin_hour <= 23)",
            name="ck_users_checkin_hour_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    shop_name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(5), default="en")  # en|pcm|yo|ha|ig
    checkin_hour: Mapped[int | None] = mapped_column(Integer)
    last_checkin_sent: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    convo_state: Mapped[ConvoState] = mapped_column(
        Enum(ConvoState, name="convo_state"), default=ConvoState.NEW
    )
    pending_action: Mapped[dict | None] = mapped_column(JSONB)
    last_seen_at: Mapped[datetime] = _ts()
    quiet_alerted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = _ts()

    items: Mapped[list["Item"]] = relationship(back_populates="user")


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_items_user_name"),
        CheckConstraint("qty >= 0", name="ck_items_qty_nonneg"),
        CheckConstraint("price_kobo >= 0", name="ck_items_price_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    unit: Mapped[str] = mapped_column(String(24), default="unit")
    qty: Mapped[int]
    cost_kobo: Mapped[int | None]
    price_kobo: Mapped[int]
    low_stock_at: Mapped[int]
    low_stock_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="items")


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (Index("ix_sales_user_sold", "user_id", "sold_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    total_kobo: Mapped[int]
    sold_at: Mapped[datetime] = _ts()
    source_wamid: Mapped[str | None] = mapped_column(String(128))

    lines: Mapped[list["SaleLine"]] = relationship(back_populates="sale")


class SaleLine(Base):
    __tablename__ = "sale_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int]
    unit_price_kobo: Mapped[int]

    sale: Mapped["Sale"] = relationship(back_populates="lines")


class StockMove(Base):
    __tablename__ = "stock_moves"
    __table_args__ = (Index("ix_moves_item_created", "item_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    type: Mapped[StockMoveType] = mapped_column(Enum(StockMoveType, name="stock_move_type"))
    delta: Mapped[int]
    reason: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = _ts()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    wamid: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    direction: Mapped[str] = mapped_column(String(3))   # IN | OUT
    msg_type: Mapped[str] = mapped_column(String(20))
    body: Mapped[str | None]
    raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()
    # Dedupe must not swallow crashed messages: a row with processed_at IS NULL
    # is claimable on retry. See milestone 2.
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )


class GuardianLink(Base):
    __tablename__ = "guardian_links"
    __table_args__ = (
        UniqueConstraint("owner_id", "guardian_wa_id", name="uq_guardian_owner_wa"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    guardian_wa_id: Mapped[str | None] = mapped_column(String(20))
    guardian_name: Mapped[str | None] = mapped_column(String(120))
    invite_code: Mapped[str | None] = mapped_column(String(16), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")
    digest_freq: Mapped[str] = mapped_column(String(10), default="WEEKLY")
    digest_sent_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = _ts()