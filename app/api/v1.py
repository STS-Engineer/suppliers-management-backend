"""API V1 routes registration."""

from fastapi import APIRouter

# Import all routers
from app.features.sites.router import router as sites_router
# from app.features.suppliers.router import router as suppliers_router
# from app.features.supplier_relations.router import router as supplier_relations_router
# from app.features.documents.router import router as documents_router
# from app.features.evaluation_cycles.router import router as evaluation_cycles_router
# from app.features.scorecards.router import router as scorecards_router
# from app.features.scorecard_inputs.router import router as scorecard_inputs_router
# from app.features.data_quality.router import router as data_quality_router
# from app.features.classifications.router import router as classifications_router
# from app.features.self_assessments.router import router as self_assessments_router
# from app.features.approvals.router import router as approvals_router
# from app.features.escalations.router import router as escalations_router
# from app.features.status_history.router import router as status_history_router

# Create API router
api_router = APIRouter(prefix="/api/v1")

# Include all feature routers
api_router.include_router(sites_router)
# api_router.include_router(suppliers_router)
# api_router.include_router(supplier_relations_router)
# api_router.include_router(documents_router)

# api_router.include_router(scorecard_inputs_router)
# api_router.include_router(data_quality_router)

# api_router.include_router(status_history_router)
