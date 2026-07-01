"""
Evaluation notification job queue.

Flow:
  pg_cron (08:00)  →  INSERT pending row + pg_notify('eval_due')
                               ↓                     ↓
                       persists if app          asyncpg LISTEN
                       is down                  wakes up instantly
                               ↓
                       startup scan picks
                       up missed jobs

Public entry points:
  run_evaluation_notifications(db, source)  — manual button / page-visit
  process_pending_jobs(db)                  — startup scan
  process_job(db, job)                      — LISTEN handler
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from sqlalchemy import Date, DateTime, Integer, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.features.auth.models import AccessIdentity
from app.features.evaluations.service import get_evaluations_due
from app.features.notifications.service import NotificationService


# ---------------------------------------------------------------------------
# ORM model (table created by migration 20260701_0068)
# ---------------------------------------------------------------------------

class EvalNotificationJob(Base):
    __tablename__ = "eval_notification_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheduled_for: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notifications_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


TriggerSource = Literal["pg_cron", "startup", "manual", "page_visit"]


# ---------------------------------------------------------------------------
# Internal: send notifications (no commit — caller owns the transaction)
# ---------------------------------------------------------------------------

async def _send_notifications(db: AsyncSession) -> dict:
    items = await get_evaluations_due(db)
    actionable = [x for x in items if x["eval_status"] in ("OVERDUE", "DUE_SOON", "NEVER_EVALUATED")]

    if not actionable:
        return {"notifications_sent": 0, "actionable_relations": 0}

    overdue = sum(1 for x in actionable if x["eval_status"] == "OVERDUE")
    due_soon = sum(1 for x in actionable if x["eval_status"] == "DUE_SOON")
    never = sum(1 for x in actionable if x["eval_status"] == "NEVER_EVALUATED")

    stmt = select(AccessIdentity).where(
        AccessIdentity.access_profile.in_(["vp_conversion", "purchasing_director"]),
        AccessIdentity.is_active.is_(True),
        AccessIdentity.registration_status == "active",
    )
    recipients = list((await db.execute(stmt)).scalars().all())
    if not recipients:
        return {"notifications_sent": 0, "actionable_relations": len(actionable)}

    parts = []
    if overdue:
        parts.append(f"{overdue} overdue")
    if due_soon:
        parts.append(f"{due_soon} due soon")
    if never:
        parts.append(f"{never} never evaluated")

    svc = NotificationService(db)
    for identity in recipients:
        await svc.create_notification(
            recipient_id=identity.id,
            notification_type="evaluation_due",
            title=f"Supplier evaluations require attention — {', '.join(parts)}",
            body=(
                f"{len(actionable)} supplier relation(s) need evaluation: {', '.join(parts)}. "
                "Open the Evaluation Scorecard to review and schedule."
            ),
            action_url="/evaluations",
        )

    return {
        "notifications_sent": len(recipients),
        "actionable_relations": len(actionable),
        "breakdown": {"overdue": overdue, "due_soon": due_soon, "never_evaluated": never},
    }


# ---------------------------------------------------------------------------
# Public: process a single pending job row
# ---------------------------------------------------------------------------

async def process_job(db: AsyncSession, job: EvalNotificationJob) -> None:
    """
    Transition: pending → running → completed | failed.
    Called by the LISTEN handler and the startup scan.
    """
    if job.status != "pending":
        return

    job.status = "running"
    job.started_at = datetime.utcnow()
    await db.commit()

    try:
        result = await _send_notifications(db)
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.notifications_sent = result["notifications_sent"]
        await db.commit()
    except Exception as exc:
        await db.rollback()
        job.status = "failed"
        job.error = str(exc)[:500]
        job.completed_at = datetime.utcnow()
        await db.commit()
        raise


# ---------------------------------------------------------------------------
# Public: startup scan — pick up jobs missed while app was down
# ---------------------------------------------------------------------------

async def process_pending_jobs(db: AsyncSession) -> int:
    stmt = select(EvalNotificationJob).where(EvalNotificationJob.status == "pending")
    jobs = (await db.execute(stmt)).scalars().all()
    for job in jobs:
        await process_job(db, job)
    return len(jobs)


# ---------------------------------------------------------------------------
# Public: manual / page-visit trigger
# ---------------------------------------------------------------------------

async def run_evaluation_notifications(
    db: AsyncSession,
    source: TriggerSource = "manual",
) -> dict:
    """
    Ensure a job exists for today and process it if still pending.
    Idempotent — safe to call multiple times per day.
    """
    today = date.today()

    job = EvalNotificationJob(scheduled_for=today, status="pending", source=source)
    db.add(job)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(EvalNotificationJob).where(EvalNotificationJob.scheduled_for == today)
        )
        job = result.scalar_one_or_none()
        if job is None or job.status in ("running", "completed"):
            return {
                "skipped": True,
                "reason": "already_ran_today",
                "status": job.status if job else "unknown",
            }

    await process_job(db, job)
    return {
        "skipped": False,
        "notifications_sent": job.notifications_sent or 0,
    }
