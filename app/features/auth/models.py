"""Authentication ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AccessIdentity(Base):
    __tablename__ = "access_identity"

    id_identity: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    access_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    auth_source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="local"
    )
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_directory: Mapped[str | None] = mapped_column(String(150), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Lifecycle: 'active' (admin-created or fully activated), 'pending' (awaiting approval),
    # 'approved' (approved, activation link sent, password not yet set), 'rejected'.
    registration_status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="active"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthToken(Base):
    """Single-use tokens for OTP password reset and account activation links."""

    __tablename__ = "auth_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("access_identity.id_identity", ondelete="CASCADE"),
        nullable=False,
    )
    # SHA-256 hash of the raw token (OTP digits or activation UUID).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 'password_reset_otp' | 'account_activation'
    token_type: Mapped[str] = mapped_column(String(50), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


class AuthAuditLog(Base):
    """Immutable audit trail for authentication and account lifecycle events."""

    __tablename__ = "auth_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # e.g. signup_requested, account_approved, account_rejected, account_activated,
    #      password_reset_requested, otp_verified, password_reset_completed, login
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    identity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("access_identity.id_identity", ondelete="SET NULL"),
        nullable=True,
    )
    # Email of the user who triggered the action (e.g. approver).
    actor_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
