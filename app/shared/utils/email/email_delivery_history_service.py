# app/services/email_delivery_history_service.py

from typing import Any

from app.db.models import EmailDeliveryHistory


class EmailDeliveryHistoryService:
    """Service for saving email delivery history.

    ``db`` accepts either a sync ``Session`` or an async ``AsyncSession`` —
    this only calls ``add()``, which is synchronous on both (it just
    registers the row with the session's identity map). It deliberately does
    NOT commit: the caller's own transaction commit (already happening at the
    end of every request) persists it. Calling `.commit()` here on an
    AsyncSession would return an un-awaited coroutine and silently no-op.
    """

    @staticmethod
    def create(
        db: Any | None,
        *,
        recipient_email: str,
        subject: str | None,
        body: str | None,
        delivery_status: str,
        error_message: str | None = None,
    ) -> None:
        if db is None:
            return

        history = EmailDeliveryHistory(
            recipient_email=recipient_email,
            subject=subject,
            body=body,
            delivery_status=delivery_status,
            error_message=error_message,
        )

        db.add(history)
