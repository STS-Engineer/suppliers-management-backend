"""Sites router."""

from datetime import date
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.shared.dependencies.db import get_db
from app.shared.dependencies.auth import get_current_user
from app.features.sites.service import SiteService
from app.features.sites import schemas

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=dict)
async def list_sites(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
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
    except Exception:
        raise


@router.get("/panel", response_model=dict)
async def list_site_panel(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    site_name: str | None = Query(default=None),
    supplier_owner: str | None = Query(default=None),
    class_grade: str | None = Query(default=None),
    status: str | None = Query(default=None),
    panel_decision: str | None = Query(default=None),
    category: str | None = Query(default=None),
    evaluation_start: date | None = Query(default=None),
    evaluation_end: date | None = Query(default=None),
    purchase_manager: str | None = Query(default=None),
    plant_manager: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    family: str | None = Query(default=None),
    sub_family: str | None = Query(default=None),
    product_line: str | None = Query(default=None),
    supplier_name: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SiteService(db)
        result = await service.list_site_panel(
            skip=skip,
            limit=limit,
            site_name=site_name,
            supplier_owner=supplier_owner,
            class_grade=class_grade,
            status=status,
            panel_decision=panel_decision,
            category=category,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            purchase_manager=purchase_manager,
            plant_manager=plant_manager,
            scope=scope,
            family=family,
            sub_family=sub_family,
            product_line=product_line,
            supplier_name=supplier_name,
        )

        return {
            "status": "success",
            "data": {
                "items": result["items"],
                "total": result["total"],
                "skip": result["skip"],
                "limit": result["limit"],
            },
            "message": f"Found {result['total']} sites",
        }
    except Exception:
        raise


@router.get("/options", response_model=dict)
async def list_site_options(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    service = SiteService(db)
    sites = await service.list_site_options()

    return {
        "status": "success",
        "data": sites,
    }


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
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
    except AppException:
        raise
    except Exception:
        raise


@router.get("/{site_id}", response_model=dict)
async def get_site(
    site_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SiteService(db)
        site = await service.get_site(site_id)

        return {
            "status": "success",
            "data": schemas.SiteResponse.model_validate(site),
            "message": f"Site {site_id} retrieved",
        }
    except AppException:
        raise
    except Exception:
        raise


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
    except AppException:
        raise
    except Exception:
        raise


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
    except AppException:
        raise
    except Exception:
        raise


