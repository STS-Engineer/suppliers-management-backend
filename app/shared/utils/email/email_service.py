"""Email service for sending notifications."""

import asyncio
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from sqlalchemy.orm import Session

from app.core.config import settings

from app.shared.utils.email.email_delivery_history_service import (
    EmailDeliveryHistoryService,
)

logger = logging.getLogger(__name__)


class EmailService:
    """Shared service for sending emails."""

    def __init__(self):
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_password = settings.SMTP_PASSWORD
        self.use_starttls = settings.SMTP_USE_STARTTLS
        self.use_login = settings.SMTP_USE_LOGIN
        self.timeout_seconds = settings.SMTP_TIMEOUT_SECONDS

    def _build_message(
        self,
        *,
        subject: str,
        recipients: list[str],
        body_html: str,
        cc: list[str] | None = None,
        attachment_path: str | None = None,
        attachment_filename: str | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        reply_to: str | None = None,
    ) -> MIMEMultipart:
        from_email = sender_email or self.smtp_user

        message = MIMEMultipart("mixed" if attachment_path else "alternative")
        message["From"] = formataddr((sender_name or "", from_email))
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Reply-To"] = reply_to or from_email

        if cc:
            message["Cc"] = ", ".join(cc)

        message.attach(MIMEText(body_html, "html"))

        if attachment_path:
            if not os.path.exists(attachment_path):
                raise FileNotFoundError(f"Attachment not found: {attachment_path}")

            with open(attachment_path, "rb") as file:
                attachment = MIMEApplication(file.read())

            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=attachment_filename or os.path.basename(attachment_path),
            )
            message.attach(attachment)

        return message

    def send_sync(
        self,
        *,
        subject: str,
        recipients: list[str],
        body_html: str,
        cc: list[str] | None = None,
        db: Session | None = None,
        attachment_path: str | None = None,
        attachment_filename: str | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        if not self.smtp_host or not self.smtp_user:
            raise RuntimeError("SMTP configuration is missing.")

        all_recipients = recipients + (cc or [])

        try:
            message = self._build_message(
                subject=subject,
                recipients=recipients,
                body_html=body_html,
                cc=cc,
                attachment_path=attachment_path,
                attachment_filename=attachment_filename,
                sender_email=sender_email,
                sender_name=sender_name,
                reply_to=reply_to,
            )

            with smtplib.SMTP(
                self.smtp_host,
                self.smtp_port,
                timeout=self.timeout_seconds,
            ) as server:
                if self.use_starttls:
                    server.starttls()

                if self.use_login:
                    server.login(self.smtp_user, self.smtp_password)

                server.sendmail(
                    self.smtp_user,
                    all_recipients,
                    message.as_string(),
                )

            for recipient in recipients:
                EmailDeliveryHistoryService.create(
                    db,
                    recipient_email=recipient,
                    subject=subject,
                    body=body_html,
                    delivery_status="sent",
                )

            return True

        except Exception as exc:
            logger.exception("Email sending failed")

            for recipient in recipients:
                EmailDeliveryHistoryService.create(
                    db,
                    recipient_email=recipient,
                    subject=subject,
                    body=body_html,
                    delivery_status="failed",
                    error_message=str(exc),
                )

            return False

    async def send_email(
        self,
        *,
        subject: str,
        recipients: list[str],
        body_html: str,
        cc: list[str] | None = None,
        db: Session | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        success = await asyncio.to_thread(
            self.send_sync,
            subject=subject,
            recipients=recipients,
            body_html=body_html,
            cc=cc,
            db=db,
            sender_email=sender_email,
            sender_name=sender_name,
            reply_to=reply_to,
        )

        if not success:
            raise Exception("Error sending email")

        return {"status": "Email sent successfully"}

    async def send_email_with_attachment(
        self,
        *,
        subject: str,
        recipients: list[str],
        body_html: str,
        attachment_path: str,
        attachment_filename: str,
        cc: list[str] | None = None,
        db: Session | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        success = await asyncio.to_thread(
            self.send_sync,
            subject=subject,
            recipients=recipients,
            body_html=body_html,
            cc=cc,
            db=db,
            attachment_path=attachment_path,
            attachment_filename=attachment_filename,
            sender_email=sender_email,
            sender_name=sender_name,
            reply_to=reply_to,
        )

        if not success:
            raise Exception("Error sending email")

        return {"status": "Email sent successfully"}


_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    global _email_service

    if _email_service is None:
        _email_service = EmailService()

    return _email_service


async def send_email(*args, **kwargs):
    return await get_email_service().send_email(*args, **kwargs)


async def send_email_with_attachment(*args, **kwargs):
    return await get_email_service().send_email_with_attachment(*args, **kwargs)


# from app.services.email_service import get_email_service

# email_service = get_email_service()

# email_service.send_sync(
#     subject="Test",
#     recipients=["user@example.com"],
#     body_html="<p>Hello</p>",
#     db=db,
# )
