"""
PostgreSQL LISTEN/NOTIFY listener for evaluation notifications.

pg_cron fires pg_notify('eval_due', ...) at 08:00 daily.
This listener wakes up immediately and processes the pending job.

The listener runs as a background asyncio task inside the FastAPI lifespan.
It uses a raw asyncpg connection (not SQLAlchemy) because SQLAlchemy's async
engine does not support LISTEN natively.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.features.evaluations.scheduler import EvalNotificationJob, process_job

logger = logging.getLogger(__name__)

_stop_event: asyncio.Event | None = None
_listener_task: asyncio.Task | None = None  # type: ignore[type-arg]


def _raw_dsn() -> str:
    """Convert postgresql+asyncpg://... to postgresql://... for raw asyncpg."""
    url = settings.database_url
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _handle_notification(connection, pid, channel, payload) -> None:  # noqa: ANN001
    """Called by asyncpg when pg_notify fires on 'eval_due'."""
    try:
        data = json.loads(payload) if payload else {}
        trigger_date_str = data.get("date")
        trigger_date = date.fromisoformat(trigger_date_str) if trigger_date_str else date.today()
    except Exception:
        trigger_date = date.today()

    logger.info("eval_due notification received for %s", trigger_date)

    async with SessionLocal() as db:
        result = await db.execute(
            select(EvalNotificationJob).where(
                EvalNotificationJob.scheduled_for == trigger_date,
                EvalNotificationJob.status == "pending",
            )
        )
        job = result.scalar_one_or_none()
        if job is None:
            logger.info("No pending job found for %s — already processed or not created yet", trigger_date)
            return
        try:
            await process_job(db, job)
            logger.info(
                "Evaluation notifications sent: %d recipient(s) for %s",
                job.notifications_sent or 0,
                trigger_date,
            )
        except Exception as exc:
            logger.error("Failed to process eval_due job for %s: %s", trigger_date, exc)


async def _listen_loop() -> None:
    """
    Maintain a persistent asyncpg connection subscribed to 'eval_due'.
    Reconnects automatically on transient errors with exponential back-off.
    Stops cleanly when _stop_event is set.
    """
    import asyncpg  # asyncpg is already a dependency via asyncpg in requirements

    global _stop_event
    assert _stop_event is not None

    backoff = 2.0
    dsn = _raw_dsn()

    while not _stop_event.is_set():
        conn = None
        try:
            conn = await asyncpg.connect(dsn)
            await conn.add_listener("eval_due", _handle_notification)
            logger.info("Listening on PostgreSQL channel 'eval_due'")
            backoff = 2.0  # reset on successful connect

            # Keep alive until stop is requested
            while not _stop_event.is_set():
                await asyncio.sleep(5)
                # Ping to detect dropped connections
                await conn.execute("SELECT 1")

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("eval_due listener error: %s — reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            if conn and not conn.is_closed():
                try:
                    await conn.remove_listener("eval_due", _handle_notification)
                    await conn.close()
                except Exception:
                    pass

    logger.info("eval_due listener stopped")


def start_listener() -> None:
    """Start the LISTEN loop as a background asyncio task."""
    global _stop_event, _listener_task
    _stop_event = asyncio.Event()
    _listener_task = asyncio.create_task(_listen_loop(), name="eval_due_listener")


async def stop_listener() -> None:
    """Signal the listener to stop and await its completion."""
    global _stop_event, _listener_task
    if _stop_event:
        _stop_event.set()
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        try:
            await _listener_task
        except (asyncio.CancelledError, Exception):
            pass
