"""Authentication service."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.core.security import create_access_token, decode_token, hash_password, verify_password
from app.features.auth import schemas
from app.features.auth.email_templates import (
    build_activation_email,
    build_otp_email,
    build_rejection_email,
)
from app.features.auth.models import AccessIdentity, AuthAuditLog, AuthToken
from app.features.notifications.service import NotificationService
from app.shared.utils.email.email_service import get_email_service


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_otp() -> tuple[str, str]:
    """Return (otp_plain, otp_hash). Uses secrets for CSPRNG."""
    otp = f"{secrets.randbelow(1_000_000):06d}"
    return otp, _hash_token(otp)


def _generate_activation_token() -> tuple[str, str]:
    """Return (token_plain, token_hash). 32-byte URL-safe token."""
    token = secrets.token_urlsafe(32)
    return token, _hash_token(token)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AuthService:
    """Authenticate application users from the database."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Sign-in
    # ------------------------------------------------------------------

    async def authenticate(self, payload: schemas.SignInRequest) -> schemas.SignInResponse:
        identity = await self._get_identity_by_email(payload.email)

        if not identity:
            raise UnauthorizedError("Invalid email or password.")

        now = _utcnow()

        if identity.locked_until and identity.locked_until > now:
            minutes_left = int((identity.locked_until - now).total_seconds() // 60) + 1
            raise TooManyRequestsError(
                f"Too many failed sign-in attempts. Please try again in "
                f"{minutes_left} minute{'s' if minutes_left != 1 else ''}."
            )
        if identity.locked_until and identity.locked_until <= now:
            # Lockout window has passed — start a fresh attempt count.
            identity.locked_until = None
            identity.failed_login_attempts = 0

        # Give registration-aware errors before checking the password,
        # so users who submitted a request know their status.
        if identity.registration_status == "pending":
            raise ForbiddenError(
                "Your account is pending approval. You will receive an email once it has been reviewed."
            )
        if identity.registration_status == "approved":
            raise ForbiddenError(
                "Your account has been approved. Please check your email for the activation link to set your password."
            )
        if identity.registration_status == "rejected":
            raise ForbiddenError(
                "Your account request has been rejected. Please contact an administrator."
            )

        if not verify_password(payload.password, identity.password_hash):
            identity.failed_login_attempts += 1
            if identity.failed_login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
                identity.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                await self._log_audit("account_locked", identity.email, identity.id_identity)
                await self.db.commit()
                raise TooManyRequestsError(
                    "Too many failed sign-in attempts. Your account is locked for "
                    f"{settings.LOGIN_LOCKOUT_MINUTES} minutes."
                )
            await self._log_audit("login_failed", identity.email, identity.id_identity)
            await self.db.commit()
            raise UnauthorizedError("Invalid email or password.")

        if not identity.is_active:
            raise ForbiddenError("This account is inactive.")

        identity.last_login_at = now
        identity.failed_login_attempts = 0
        identity.locked_until = None
        await self.db.commit()

        expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        token = create_access_token(
            {
                "sub": identity.email,
                "email": identity.email,
                "name": identity.full_name,
                "access_profile": identity.access_profile,
                "auth_source": identity.auth_source,
            },
            expires_delta=expires,
        )

        return schemas.SignInResponse(
            access_token=token,
            expires_in_seconds=int(expires.total_seconds()),
            user=self._to_authenticated_user(identity),
        )

    async def get_current_user_profile(self, current_user: dict) -> schemas.AuthenticatedUser:
        email = current_user.get("email") or current_user.get("sub")
        if not email:
            raise UnauthorizedError("Invalid session.")

        identity = await self._get_identity_by_email(email)
        if not identity or not identity.is_active:
            raise UnauthorizedError("Your session is no longer active.")

        return self._to_authenticated_user(identity)

    # ------------------------------------------------------------------
    # Sign-up / account request
    # ------------------------------------------------------------------

    async def signup(self, payload: schemas.SignUpRequest) -> schemas.SignUpResponse:
        existing = await self._get_identity_by_email(payload.email)
        if existing:
            if existing.registration_status == "rejected":
                raise ConflictError(
                    "Your previous account request was rejected. Please contact an administrator."
                )
            raise ConflictError(
                "An account already exists or is pending for this email address."
            )

        identity = AccessIdentity(
            email=payload.email.strip().lower(),
            full_name=payload.full_name.strip(),
            access_profile=payload.requested_role.strip(),
            password_hash="",
            auth_source="local",
            is_active=False,
            registration_status="pending",
        )
        self.db.add(identity)
        await self.db.flush()

        await self._log_audit("signup_requested", identity.email, identity.id_identity)
        await self.db.commit()

        # Notify all active approvers via in-app notifications (best-effort).
        approvers = await self._get_approvers()
        notif_svc = NotificationService(self.db)
        for approver in approvers:
            await notif_svc.create_notification(
                recipient_id=approver.id_identity,
                notification_type="account_request_pending",
                title="New account request",
                body=(
                    f"{identity.full_name} ({identity.email}) "
                    f"requested access as {identity.access_profile.replace('_', ' ')}."
                ),
                action_url="/account-requests",
            )
        if approvers:
            await self.db.commit()

        return schemas.SignUpResponse(
            message=(
                "Your account request has been submitted. "
                "You will receive an email once it has been reviewed."
            ),
            email=identity.email,
        )

    # ------------------------------------------------------------------
    # Forgot password (OTP flow)
    # ------------------------------------------------------------------

    async def forgot_password(
        self, payload: schemas.ForgotPasswordRequest
    ) -> schemas.ForgotPasswordResponse:
        # Always return the same message to prevent email enumeration.
        _GENERIC = (
            "If an active account exists for this email address, "
            "you will receive a password reset code shortly."
        )

        identity = await self._get_identity_by_email(payload.email)
        if identity and identity.is_active and identity.registration_status == "active":
            if await self._otp_resend_on_cooldown(identity.id_identity):
                # Silently no-op — the previously issued OTP (and its remaining
                # attempt budget) is still valid, so avoid handing an attacker
                # a fresh unlimited-attempt code just by spamming this endpoint.
                await self._log_audit(
                    "password_reset_resend_throttled", identity.email, identity.id_identity
                )
                await self.db.commit()
                return schemas.ForgotPasswordResponse(message=_GENERIC)

            # Invalidate any prior unused OTPs.
            await self._invalidate_tokens(identity.id_identity, "password_reset_otp")

            otp_plain, otp_hash = _generate_otp()
            expires_at = _utcnow() + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

            self.db.add(
                AuthToken(
                    identity_id=identity.id_identity,
                    token_hash=otp_hash,
                    token_type="password_reset_otp",
                    expires_at=expires_at,
                )
            )
            await self._log_audit("password_reset_requested", identity.email, identity.id_identity)
            await self.db.commit()

            body = build_otp_email(identity.full_name, otp_plain, settings.OTP_EXPIRE_MINUTES)
            try:
                await get_email_service().send_email(
                    subject="Your Password Reset Code",
                    recipients=[identity.email],
                    body_html=body,
                    db=self.db,
                )
            except Exception:
                pass

        return schemas.ForgotPasswordResponse(message=_GENERIC)

    async def verify_otp(self, payload: schemas.VerifyOtpRequest) -> schemas.VerifyOtpResponse:
        identity = await self._get_identity_by_email(payload.email)
        if not identity or not identity.is_active or identity.registration_status != "active":
            raise UnauthorizedError("Invalid email or OTP.")

        now = _utcnow()

        # Look up the active OTP by identity only (not by guessed value) so a
        # wrong guess can still be counted against it. Locks the row so two
        # concurrent submits can't each read the same attempt_count and both
        # slip through under the cap.
        stmt = (
            select(AuthToken)
            .where(
                AuthToken.identity_id == identity.id_identity,
                AuthToken.token_type == "password_reset_otp",
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > now,
            )
            .order_by(AuthToken.created_at.desc())
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        auth_token = result.scalars().first()

        if not auth_token:
            raise UnauthorizedError("Invalid or expired OTP.")

        if auth_token.attempt_count >= settings.OTP_MAX_ATTEMPTS:
            auth_token.used_at = now
            await self._log_audit("otp_locked", identity.email, identity.id_identity)
            await self.db.commit()
            raise TooManyRequestsError(
                "Too many incorrect attempts. Please request a new code."
            )

        if not hmac.compare_digest(auth_token.token_hash, _hash_token(payload.otp)):
            auth_token.attempt_count += 1
            remaining = settings.OTP_MAX_ATTEMPTS - auth_token.attempt_count
            if remaining <= 0:
                auth_token.used_at = now
                await self._log_audit("otp_locked", identity.email, identity.id_identity)
                await self.db.commit()
                raise TooManyRequestsError(
                    "Too many incorrect attempts. Please request a new code."
                )
            await self._log_audit("otp_verify_failed", identity.email, identity.id_identity)
            await self.db.commit()
            attempt_word = "attempt" if remaining == 1 else "attempts"
            raise UnauthorizedError(
                f"Invalid OTP. {remaining} {attempt_word} remaining."
            )

        auth_token.used_at = now
        await self._log_audit("otp_verified", identity.email, identity.id_identity)

        # Issue a short-lived (10 min) JWT and store its hash so it can only be
        # used once — the JWT alone is not sufficient because it would be replayable
        # for its full validity window.
        reset_token = create_access_token(
            {
                "sub": identity.email,
                "email": identity.email,
                "token_purpose": "password_reset",
            },
            expires_delta=timedelta(minutes=10),
        )
        reset_expires_at = _utcnow() + timedelta(minutes=10)
        self.db.add(
            AuthToken(
                identity_id=identity.id_identity,
                token_hash=_hash_token(reset_token),
                token_type="password_reset",
                expires_at=reset_expires_at,
            )
        )
        await self.db.commit()

        return schemas.VerifyOtpResponse(
            reset_token=reset_token,
            message="OTP verified. You may now set a new password.",
        )

    async def set_password_with_token(
        self, payload: schemas.SetPasswordWithTokenRequest
    ) -> schemas.PasswordOperationResponse:
        try:
            claims = decode_token(payload.reset_token)
        except UnauthorizedError:
            raise UnauthorizedError("Invalid or expired reset token.")

        if claims.get("token_purpose") != "password_reset":
            raise UnauthorizedError("Invalid reset token.")

        email = claims.get("email") or claims.get("sub")
        identity = await self._get_identity_by_email(email or "")
        if not identity or not identity.is_active:
            raise UnauthorizedError("Invalid reset token.")

        # Verify the token has not been used before (single-use enforcement).
        now = _utcnow()
        token_hash = _hash_token(payload.reset_token)
        stmt = select(AuthToken).where(
            AuthToken.identity_id == identity.id_identity,
            AuthToken.token_type == "password_reset",
            AuthToken.token_hash == token_hash,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > now,
        )
        db_token = (await self.db.execute(stmt)).scalar_one_or_none()
        if not db_token:
            raise UnauthorizedError("Invalid or already used reset token.")

        db_token.used_at = now
        identity.password_hash = hash_password(payload.new_password)
        identity.updated_at = now
        await self._log_audit("password_reset_completed", identity.email, identity.id_identity)
        await self.db.commit()

        return schemas.PasswordOperationResponse(
            email=identity.email,
            changed_at=identity.updated_at,
        )

    # ------------------------------------------------------------------
    # Account request management (approver actions)
    # ------------------------------------------------------------------

    async def list_account_requests(
        self, status_filter: str | None = None, approver_email: str | None = None
    ) -> list[schemas.AccountRequestResponse]:
        stmt = select(AccessIdentity).where(
            AccessIdentity.registration_status.in_(["pending", "approved", "active", "rejected"])
        )
        if status_filter == "approved":
            # "Approved" covers the full post-approval lifecycle: still shows
            # accounts that have since completed activation.
            stmt = stmt.where(AccessIdentity.registration_status.in_(["approved", "active"]))
            if approver_email:
                stmt = stmt.where(AccessIdentity.approved_by == approver_email)
        elif status_filter:
            stmt = stmt.where(AccessIdentity.registration_status == status_filter)
        stmt = stmt.order_by(AccessIdentity.created_at.desc())
        result = await self.db.execute(stmt)
        return [self._to_account_request_response(i) for i in result.scalars().all()]

    async def approve_account(
        self,
        identity_id: int,
        payload: schemas.ApproveAccountRequest,
        actor_email: str,
    ) -> dict:
        identity = await self._get_identity_by_id(identity_id)
        if not identity:
            raise NotFoundError("Access identity", identity_id)
        if identity.registration_status != "pending":
            raise ConflictError(
                f"Account is not pending (current status: {identity.registration_status})."
            )

        token_plain, token_hash = _generate_activation_token()
        expires_at = _utcnow() + timedelta(hours=settings.ACTIVATION_LINK_EXPIRE_HOURS)

        self.db.add(
            AuthToken(
                identity_id=identity.id_identity,
                token_hash=token_hash,
                token_type="account_activation",
                expires_at=expires_at,
            )
        )
        identity.registration_status = "approved"
        identity.approved_by = actor_email
        identity.updated_at = _utcnow()

        await self._log_audit(
            "account_approved", identity.email, identity.id_identity, actor_email
        )
        await self.db.commit()

        activation_url = (
            f"{settings.frontend_base_url}/activate?token={token_plain}"
        )
        body = build_activation_email(
            identity.full_name,
            activation_url,
            settings.ACTIVATION_LINK_EXPIRE_HOURS,
            payload.message,
        )
        try:
            await get_email_service().send_email(
                subject="Your Account Has Been Approved – Activate Now",
                recipients=[identity.email],
                body_html=body,
                db=self.db,
            )
        except Exception:
            pass

        return {"message": f"Account {identity.email} approved. Activation email sent."}

    async def reject_account(
        self,
        identity_id: int,
        payload: schemas.RejectAccountRequest,
        actor_email: str,
    ) -> dict:
        identity = await self._get_identity_by_id(identity_id)
        if not identity:
            raise NotFoundError("Access identity", identity_id)
        if identity.registration_status != "pending":
            raise ConflictError(
                f"Account is not pending (current status: {identity.registration_status})."
            )

        identity.registration_status = "rejected"
        identity.updated_at = _utcnow()

        await self._log_audit(
            "account_rejected",
            identity.email,
            identity.id_identity,
            actor_email,
            {"reason": payload.reason} if payload.reason else None,
        )
        await self.db.commit()

        body = build_rejection_email(identity.full_name, payload.reason)
        try:
            await get_email_service().send_email(
                subject="Update on Your Account Request",
                recipients=[identity.email],
                body_html=body,
                db=self.db,
            )
        except Exception:
            pass

        return {"message": f"Account {identity.email} rejected."}

    # ------------------------------------------------------------------
    # Account activation (first-time password via approval link)
    # ------------------------------------------------------------------

    async def activate_account(
        self, payload: schemas.ActivateAccountRequest
    ) -> schemas.PasswordOperationResponse:
        token_hash = _hash_token(payload.token)
        now = _utcnow()

        stmt = select(AuthToken).where(
            AuthToken.token_type == "account_activation",
            AuthToken.token_hash == token_hash,
            AuthToken.used_at.is_(None),
            AuthToken.expires_at > now,
        )
        result = await self.db.execute(stmt)
        auth_token = result.scalar_one_or_none()

        if not auth_token:
            raise UnauthorizedError("Invalid or expired activation link.")

        identity = await self._get_identity_by_id(auth_token.identity_id)
        if not identity or identity.registration_status != "approved":
            raise UnauthorizedError("Invalid activation link.")

        auth_token.used_at = now
        identity.password_hash = hash_password(payload.new_password)
        identity.is_active = True
        identity.registration_status = "active"
        identity.updated_at = now

        await self._log_audit("account_activated", identity.email, identity.id_identity)
        await self.db.commit()

        return schemas.PasswordOperationResponse(
            email=identity.email,
            changed_at=identity.updated_at,
        )

    # ------------------------------------------------------------------
    # Admin identity CRUD (unchanged from before)
    # ------------------------------------------------------------------

    async def list_access_identities(self) -> list[schemas.AccessIdentityResponse]:
        statement = select(AccessIdentity).order_by(AccessIdentity.created_at.desc())
        result = await self.db.execute(statement)
        items = result.scalars().all()
        return [self._to_access_identity_response(item) for item in items]

    async def create_access_identity(
        self,
        payload: schemas.AccessIdentityCreateRequest,
    ) -> schemas.AccessIdentityResponse:
        existing = await self._get_identity_by_email(payload.email)
        if existing:
            raise ConflictError("An access identity already exists for this email.")

        identity = AccessIdentity(
            email=payload.email.strip().lower(),
            full_name=payload.full_name.strip(),
            access_profile=payload.access_profile.strip(),
            password_hash=hash_password(payload.password),
            auth_source=payload.auth_source.strip(),
            external_subject=payload.external_subject,
            external_directory=payload.external_directory,
            is_active=payload.is_active,
            registration_status="active",
        )
        self.db.add(identity)
        await self.db.commit()
        await self.db.refresh(identity)
        return self._to_access_identity_response(identity)

    async def update_access_identity(
        self,
        identity_id: int,
        payload: schemas.AccessIdentityUpdateRequest,
    ) -> schemas.AccessIdentityResponse:
        identity = await self._get_identity_by_id(identity_id)
        if not identity:
            raise NotFoundError("Access identity", identity_id)

        updates = payload.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(identity, field, value.strip() if isinstance(value, str) else value)

        identity.updated_at = _utcnow()
        await self.db.commit()
        await self.db.refresh(identity)
        return self._to_access_identity_response(identity)

    async def delete_access_identity(
        self,
        identity_id: int,
        actor_email: str | None = None,
    ) -> None:
        identity = await self._get_identity_by_id(identity_id)
        if not identity:
            raise NotFoundError("Access identity", identity_id)
        # Guard against locking yourself out — deactivate instead of self-delete.
        if actor_email and identity.email.strip().lower() == actor_email.strip().lower():
            raise ConflictError("You cannot delete your own account.")
        # Keep an audit record of the removal. identity_id is left null (the row is
        # about to disappear); the email + details preserve who was deleted.
        await self._log_audit(
            "account_deleted",
            email=identity.email,
            identity_id=None,
            actor_email=actor_email,
            details={
                "id_identity": identity.id_identity,
                "full_name": identity.full_name,
                "access_profile": identity.access_profile,
            },
        )
        await self.db.delete(identity)
        await self.db.commit()

    async def change_password(
        self,
        current_user: dict,
        payload: schemas.ChangePasswordRequest,
    ) -> schemas.PasswordOperationResponse:
        email = current_user.get("email") or current_user.get("sub")
        identity = await self._get_identity_by_email(email or "")
        if not identity:
            raise NotFoundError("Access identity", email or "unknown")

        if not verify_password(payload.current_password, identity.password_hash):
            raise UnauthorizedError("Current password is incorrect.")

        identity.password_hash = hash_password(payload.new_password)
        identity.updated_at = _utcnow()
        await self.db.commit()

        return schemas.PasswordOperationResponse(
            email=identity.email,
            changed_at=identity.updated_at,
        )

    async def reset_password(
        self,
        identity_id: int,
        payload: schemas.ResetPasswordRequest,
    ) -> schemas.PasswordOperationResponse:
        identity = await self._get_identity_by_id(identity_id)
        if not identity:
            raise NotFoundError("Access identity", identity_id)

        identity.password_hash = hash_password(payload.new_password)
        identity.updated_at = _utcnow()
        await self.db.commit()

        return schemas.PasswordOperationResponse(
            email=identity.email,
            changed_at=identity.updated_at,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_identity_by_email(self, email: str) -> AccessIdentity | None:
        statement = select(AccessIdentity).where(AccessIdentity.email.ilike(email))
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def _get_identity_by_id(self, identity_id: int) -> AccessIdentity | None:
        statement = select(AccessIdentity).where(
            AccessIdentity.id_identity == identity_id
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def _get_approvers(self) -> list[AccessIdentity]:
        stmt = select(AccessIdentity).where(
            AccessIdentity.access_profile.in_(settings.approver_roles),
            AccessIdentity.is_active.is_(True),
            AccessIdentity.registration_status == "active",
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _otp_resend_on_cooldown(self, identity_id: int) -> bool:
        stmt = (
            select(AuthToken)
            .where(
                AuthToken.identity_id == identity_id,
                AuthToken.token_type == "password_reset_otp",
            )
            .order_by(AuthToken.created_at.desc())
        )
        result = await self.db.execute(stmt)
        latest = result.scalars().first()
        if not latest:
            return False
        now = _utcnow()
        # A token that's already locked out (5 failed verify_otp attempts),
        # consumed, or expired has no live code left to protect against
        # spam -- treat that the same as "no token at all" so forgot_password
        # can issue a fresh OTP right away. Previously the cooldown was
        # measured from the dead token's created_at regardless of its state,
        # so clicking "Send reset code" right after a lockout silently
        # no-op'd (no new token, no email) for up to OTP_RESEND_COOLDOWN_SECONDS
        # -- forgot_password still returns its generic success message either
        # way, so the user had no sign anything was wrong.
        latest_is_live = (
            latest.used_at is None
            and latest.expires_at > now
            and latest.attempt_count < settings.OTP_MAX_ATTEMPTS
        )
        if not latest_is_live:
            return False
        cooldown_expires_at = latest.created_at + timedelta(
            seconds=settings.OTP_RESEND_COOLDOWN_SECONDS
        )
        return now < cooldown_expires_at

    async def _invalidate_tokens(self, identity_id: int, token_type: str) -> None:
        stmt = select(AuthToken).where(
            AuthToken.identity_id == identity_id,
            AuthToken.token_type == token_type,
            AuthToken.used_at.is_(None),
        )
        result = await self.db.execute(stmt)
        now = _utcnow()
        for token in result.scalars().all():
            token.used_at = now

    async def _log_audit(
        self,
        event_type: str,
        email: str,
        identity_id: int | None = None,
        actor_email: str | None = None,
        details: dict | None = None,
    ) -> None:
        self.db.add(
            AuthAuditLog(
                event_type=event_type,
                email=email,
                identity_id=identity_id,
                actor_email=actor_email,
                details=json.dumps(details) if details else None,
            )
        )

    def _to_authenticated_user(self, identity: AccessIdentity) -> schemas.AuthenticatedUser:
        return schemas.AuthenticatedUser(
            email=identity.email,
            full_name=identity.full_name,
            access_profile=identity.access_profile,
        )

    def _to_access_identity_response(
        self,
        identity: AccessIdentity,
    ) -> schemas.AccessIdentityResponse:
        return schemas.AccessIdentityResponse(
            id_identity=identity.id_identity,
            email=identity.email,
            full_name=identity.full_name,
            access_profile=identity.access_profile,
            auth_source=identity.auth_source,
            external_subject=identity.external_subject,
            external_directory=identity.external_directory,
            is_active=identity.is_active,
            registration_status=identity.registration_status,
            last_login_at=identity.last_login_at,
            created_at=identity.created_at,
            updated_at=identity.updated_at,
        )

    def _to_account_request_response(
        self, identity: AccessIdentity
    ) -> schemas.AccountRequestResponse:
        return schemas.AccountRequestResponse(
            id_identity=identity.id_identity,
            email=identity.email,
            full_name=identity.full_name,
            requested_role=identity.access_profile,
            registration_status=identity.registration_status,
            approved_by=identity.approved_by,
            created_at=identity.created_at,
        )
