"""Authentication router."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.auth import schemas
from app.features.auth.service import AuthService
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


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


@router.get("/access-identities", response_model=dict)
async def list_access_identities(
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
    current_user: dict = Depends(get_current_user),
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
async def reset_password(
    identity_id: int,
    data: schemas.ResetPasswordRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = AuthService(db)
    result = await service.reset_password(identity_id, data)
    return {
        "status": "success",
        "data": result,
        "message": "Password reset successfully.",
    }
