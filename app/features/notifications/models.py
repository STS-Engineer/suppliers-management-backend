"""Notification ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    recipient_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("access_identity.id_identity", ondelete="CASCADE"),
        nullable=False,
    )

    # Extensible type key — e.g. 'account_request_pending', 'system'
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Frontend route the user lands on when clicking the notification.
    action_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Arbitrary JSON payload for future use (e.g. linked entity IDs).
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
