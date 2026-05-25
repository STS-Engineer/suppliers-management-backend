"""Authentication service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import create_access_token, hash_password, verify_password
from app.features.auth import schemas
from app.features.auth.models import AccessIdentity


class AuthService:
    """Authenticate application users from the database."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def authenticate(self, payload: schemas.SignInRequest) -> schemas.SignInResponse:
        identity = await self._get_identity_by_email(payload.email)

        if not identity or not verify_password(payload.password, identity.password_hash):
            raise UnauthorizedError("Invalid email or password.")

        if not identity.is_active:
            raise ForbiddenError("This account is inactive.")

        identity.last_login_at = datetime.now(UTC).replace(tzinfo=None)
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

    async def _get_identity_by_email(self, email: str) -> AccessIdentity | None:
        statement = select(AccessIdentity).where(AccessIdentity.email.ilike(email))
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    def _to_authenticated_user(self, identity: AccessIdentity) -> schemas.AuthenticatedUser:
        return schemas.AuthenticatedUser(
            email=identity.email,
            full_name=identity.full_name,
            access_profile=identity.access_profile,
        )

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

        identity.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await self.db.commit()
        await self.db.refresh(identity)
        return self._to_access_identity_response(identity)

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
        identity.updated_at = datetime.now(UTC).replace(tzinfo=None)
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
        identity.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await self.db.commit()

        return schemas.PasswordOperationResponse(
            email=identity.email,
            changed_at=identity.updated_at,
        )

    async def _get_identity_by_id(self, identity_id: int) -> AccessIdentity | None:
        statement = select(AccessIdentity).where(
            AccessIdentity.id_identity == identity_id
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

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
            last_login_at=identity.last_login_at,
            created_at=identity.created_at,
            updated_at=identity.updated_at,
        )
