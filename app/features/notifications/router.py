"""Notifications router."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.features.notifications import schemas
from app.features.notifications.service import NotificationService
from app.features.auth.models import AccessIdentity
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db
from sqlalchemy import select

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _resolve_identity_id(current_user: dict, db: AsyncSession) -> int:
    email = current_user.get("email") or current_user.get("sub")
    if not email:
        raise UnauthorizedError("Invalid session.")
    stmt = select(AccessIdentity).where(AccessIdentity.email.ilike(email))
    result = await db.execute(stmt)
    identity = result.scalar_one_or_none()
    if not identity:
        raise UnauthorizedError("Your session is no longer active.")
    return identity.id_identity


@router.get("/unread-count", response_model=dict)
async def get_unread_count(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    identity_id = await _resolve_identity_id(current_user, db)
    svc = NotificationService(db)
    count = await svc.get_unread_count(identity_id)
    return {"status": "success", "data": schemas.UnreadCountResponse(count=count)}


@router.get("", response_model=dict)
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    identity_id = await _resolve_identity_id(current_user, db)
    svc = NotificationService(db)
    items = await svc.list_notifications(identity_id, unread_only=unread_only, limit=limit)
    return {
        "status": "success",
        "data": {"items": items, "count": len(items)},
    }


@router.post("/{notification_id}/read", response_model=dict)
async def mark_as_read(
    notification_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    identity_id = await _resolve_identity_id(current_user, db)
    svc = NotificationService(db)
    item = await svc.mark_as_read(notification_id, identity_id)
    return {"status": "success", "data": item}


@router.post("/read-all", response_model=dict)
async def mark_all_as_read(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    identity_id = await _resolve_identity_id(current_user, db)
    svc = NotificationService(db)
    updated = await svc.mark_all_as_read(identity_id)
    return {"status": "success", "data": {"marked_read": updated}}
