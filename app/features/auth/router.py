"""Authentication router."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenError
from app.features.auth import schemas
from app.features.auth.service import AuthService
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_approver(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency that enforces the caller holds an approver role."""
    if current_user.get("access_profile") not in settings.approver_roles:
        raise ForbiddenError("You do not have permission to manage account requests.")
    return current_user


# ---------------------------------------------------------------------------
# Sign-in / profile
# ---------------------------------------------------------------------------

@router.post("/signin", response_model=dict)
async def sign_in(
    data: schemas.SignInRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    session = await service.authenticate(data)
    return {
        "status": "success",
        "data": session,
        "message": "Signed in successfully.",
    }


@router.get("/me", response_model=dict)
async def get_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    user = await service.get_current_user_profile(current_user)
    return {
        "status": "success",
        "data": user,
    }


# ---------------------------------------------------------------------------
# Sign-up (self-registration, no auth required)
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=dict)
async def signup(
    data: schemas.SignUpRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.signup(data)
    return {
        "status": "success",
        "data": result,
    }


# ---------------------------------------------------------------------------
# Forgot password – OTP flow (no auth required)
# ---------------------------------------------------------------------------

@router.post("/forgot-password", response_model=dict)
async def forgot_password(
    data: schemas.ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.forgot_password(data)
    return {
        "status": "success",
        "data": result,
    }


@router.post("/verify-otp", response_model=dict)
async def verify_otp(
    data: schemas.VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.verify_otp(data)
    return {
        "status": "success",
        "data": result,
    }


@router.post("/reset-password", response_model=dict)
async def reset_password_with_token(
    data: schemas.SetPasswordWithTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.set_password_with_token(data)
    return {
        "status": "success",
        "data": result,
        "message": "Password updated successfully.",
    }


# ---------------------------------------------------------------------------
# Account activation (no auth required – token in body)
# ---------------------------------------------------------------------------

@router.post("/activate", response_model=dict)
async def activate_account(
    data: schemas.ActivateAccountRequest,
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.activate_account(data)
    return {
        "status": "success",
        "data": result,
        "message": "Account activated successfully. You can now sign in.",
    }


# ---------------------------------------------------------------------------
# Account request management (approvers only)
# ---------------------------------------------------------------------------

@router.get("/account-requests", response_model=dict)
async def list_account_requests(
    status: str | None = Query(default=None, description="Filter by registration_status"),
    _approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    items = await service.list_account_requests(status_filter=status)
    return {
        "status": "success",
        "data": {
            "items": items,
            "count": len(items),
        },
    }


@router.post("/account-requests/{identity_id}/approve", response_model=dict)
async def approve_account(
    identity_id: int,
    data: schemas.ApproveAccountRequest,
    approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.approve_account(
        identity_id, data, actor_email=approver["email"]
    )
    return {
        "status": "success",
        "data": result,
    }


@router.post("/account-requests/{identity_id}/reject", response_model=dict)
async def reject_account(
    identity_id: int,
    data: schemas.RejectAccountRequest,
    approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.reject_account(
        identity_id, data, actor_email=approver["email"]
    )
    return {
        "status": "success",
        "data": result,
    }


# ---------------------------------------------------------------------------
# Admin identity CRUD
# ---------------------------------------------------------------------------

@router.get("/access-identities", response_model=dict)
async def list_access_identities(
    _approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    items = await service.list_access_identities()
    return {
        "status": "success",
        "data": {
            "items": items,
            "count": len(items),
        },
    }


@router.post("/access-identities", response_model=dict)
async def create_access_identity(
    data: schemas.AccessIdentityCreateRequest,
    _approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    identity = await service.create_access_identity(data)
    return {
        "status": "success",
        "data": identity,
        "message": "Access identity created successfully.",
    }


@router.put("/access-identities/{identity_id}", response_model=dict)
async def update_access_identity(
    identity_id: int,
    data: schemas.AccessIdentityUpdateRequest,
    _approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    identity = await service.update_access_identity(identity_id, data)
    return {
        "status": "success",
        "data": identity,
        "message": "Access identity updated successfully.",
    }


@router.post("/change-password", response_model=dict)
async def change_password(
    data: schemas.ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.change_password(current_user, data)
    return {
        "status": "success",
        "data": result,
        "message": "Password updated successfully.",
    }


@router.post("/access-identities/{identity_id}/reset-password", response_model=dict)
async def reset_password_admin(
    identity_id: int,
    data: schemas.ResetPasswordRequest,
    _approver: dict = Depends(_require_approver),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.reset_password(identity_id, data)
    return {
        "status": "success",
        "data": result,
        "message": "Password reset successfully.",
    }
