"""API V1 routes registration."""

from fastapi import APIRouter

from app.features.auth.router import router as auth_router
from app.features.sites.router import router as sites_router
from app.features.suppliers.router import router as suppliers_router
from app.features.supplier_relations.router import router as supplier_relations_router
from app.features.supplier_monitoring.router import router as supplier_monitoring_router

from app.features.purchasing_value.router import router as purchasing_value_router
from app.features.evaluations.router import router as evaluations_router
from app.features.gate_approval.router import router as gate_approval_router

from app.features.public.router import router as public_router
from app.features.notifications.router import router as notifications_router
from app.features.committee_review.router import router as committee_review_router


# Create API router
api_router = APIRouter(prefix="/api/v1")

# Include all feature routers
api_router.include_router(
    public_router
)  # no auth — must come before auth-gated routers
api_router.include_router(auth_router)
api_router.include_router(sites_router)
api_router.include_router(suppliers_router)
api_router.include_router(supplier_relations_router)
api_router.include_router(supplier_monitoring_router)
api_router.include_router(purchasing_value_router)
api_router.include_router(evaluations_router)
api_router.include_router(gate_approval_router)
api_router.include_router(notifications_router)
api_router.include_router(committee_review_router)
