"""API V1 routes registration."""

from fastapi import APIRouter

from app.features.sites.router import router as sites_router
from app.features.suppliers.router import router as suppliers_router
from app.features.supplier_relations.router import router as supplier_relations_router


# Create API router
api_router = APIRouter(prefix="/api/v1")

# Include all feature routers
api_router.include_router(sites_router)
api_router.include_router(suppliers_router)
api_router.include_router(supplier_relations_router)

