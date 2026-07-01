"""Notification service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.features.notifications import schemas
from app.features.notifications.models import Notification


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_notification(
        self,
        *,
        recipient_id: int,
        notification_type: str,
        title: str,
        body: str | None = None,
        action_url: str | None = None,
        metadata_json: str | None = None,
    ) -> Notification:
        notif = Notification(
            recipient_id=recipient_id,
            notification_type=notification_type,
            title=title,
            body=body,
            action_url=action_url,
            metadata_json=metadata_json,
        )
        self.db.add(notif)
        return notif

    async def list_notifications(
        self,
        identity_id: int,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[schemas.NotificationResponse]:
        stmt = select(Notification).where(Notification.recipient_id == identity_id)
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return [self._to_response(n) for n in result.scalars().all()]

    async def get_unread_count(self, identity_id: int) -> int:
        stmt = select(func.count()).where(
            Notification.recipient_id == identity_id,
            Notification.is_read.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def mark_as_read(
        self, notification_id: int, identity_id: int
    ) -> schemas.NotificationResponse:
        notif = await self._get_by_id(notification_id)
        if not notif:
            raise NotFoundError("Notification", notification_id)
        if notif.recipient_id != identity_id:
            raise ForbiddenError("This notification does not belong to you.")

        if not notif.is_read:
            notif.is_read = True
            notif.read_at = datetime.now(UTC).replace(tzinfo=None)
            await self.db.commit()

        return self._to_response(notif)

    async def mark_all_as_read(self, identity_id: int) -> int:
        stmt = select(Notification).where(
            Notification.recipient_id == identity_id,
            Notification.is_read.is_(False),
        )
        result = await self.db.execute(stmt)
        now = datetime.now(UTC).replace(tzinfo=None)
        updated = 0
        for notif in result.scalars().all():
            notif.is_read = True
            notif.read_at = now
            updated += 1
        if updated:
            await self.db.commit()
        return updated

    async def _get_by_id(self, notification_id: int) -> Notification | None:
        stmt = select(Notification).where(Notification.id == notification_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _to_response(self, n: Notification) -> schemas.NotificationResponse:
        return schemas.NotificationResponse(
            id=n.id,
            notification_type=n.notification_type,
            title=n.title,
            body=n.body,
            action_url=n.action_url,
            is_read=n.is_read,
            read_at=n.read_at,
            created_at=n.created_at,
        )
