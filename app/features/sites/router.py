"""Sites router."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.shared.dependencies.db import get_db
from app.shared.dependencies.auth import get_current_user, get_current_user_optional
from app.features.sites.service import SiteService
from app.features.sites import schemas

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("/", response_model=dict)
async def list_sites(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: dict | None = Depends(get_current_user_optional),
):
    try:
        service = SiteService(db)
        result = await service.list_sites(skip=skip, limit=limit)

        return {
            "status": "success",
            "data": {
    **result,
    "items": [
        schemas.SiteResponse.model_validate(site)
        for site in result["items"]
    ],
},
            "message": f"Found {result['total']} sites",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/options", response_model=dict)
async def list_site_options(
    db: AsyncSession = Depends(get_db),
    current_user: dict | None = Depends(get_current_user_optional),
):
    service = SiteService(db)
    sites = await service.list_site_options()

    return {
        "status": "success",
        "data": sites,
    }

@router.post("/", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_site(
    data: schemas.SiteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SiteService(db)
        site = await service.create_site(data)

        return {
            "status": "success",
            "data": schemas.SiteResponse.model_validate(site),
            "message": f"Site '{site.site_name}' created successfully",
            "id": site.id_site,
        }
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{site_id}", response_model=dict)
async def get_site(
    site_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict | None = Depends(get_current_user_optional),
):
    try:
        service = SiteService(db)
        site = await service.get_site(site_id)

        return {
            "status": "success",
            "data": schemas.SiteResponse.model_validate(site),
            "message": f"Site {site_id} retrieved",
        }
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{site_id}", response_model=dict)
async def update_site(
    site_id: int,
    data: schemas.SiteUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SiteService(db)
        site = await service.update_site(site_id, data)

        return {
            "status": "success",
            "data": site,
            "message": f"Site {site_id} updated successfully",
        }
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{site_id}", response_model=dict)
async def delete_site(
    site_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SiteService(db)
        deleted = await service.delete_site(site_id)

        return {
            "status": "success",
            "data": {"deleted": deleted},
            "message": f"Site {site_id} deleted successfully",
        }
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))