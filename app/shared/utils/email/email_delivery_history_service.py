# app/services/email_delivery_history_service.py

from sqlalchemy.orm import Session

from app.db.models import EmailDeliveryHistory


class EmailDeliveryHistoryService:
    """Service for saving email delivery history."""

    @staticmethod
    def create(
        db: Session | None,
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
        db.commit()
