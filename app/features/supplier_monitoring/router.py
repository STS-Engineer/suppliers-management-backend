"""Supplier monitoring router — data-completeness dashboard endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.dependencies.db import get_db
from app.shared.dependencies.auth import get_current_user
from app.features.supplier_monitoring.service import SupplierMonitoringService

router = APIRouter(prefix="/supplier-monitoring", tags=["supplier-monitoring"])


@router.get("/overview", response_model=dict)
async def get_monitoring_overview(
    country: Optional[str] = Query(None),
    commodity: Optional[str] = Query(None),
    group_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="Search unit or group name"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Per-check counts + drill-down list of supplier units with missing data.

    Read-only — available to every authenticated role (including viewers /
    directors) since it only surfaces existing data-completeness gaps.
    """
    service = SupplierMonitoringService(db)
    data = await service.get_overview(
        country=country,
        commodity=commodity,
        group_id=group_id,
        q=q,
    )
    return {"status": "success", "data": data}
