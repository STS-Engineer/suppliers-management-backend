"""Supplier relations router."""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.features.supplier_relations import schemas
from app.features.supplier_relations.service import SupplierRelationService
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/supplier-relations", tags=["supplier-relations"])


@router.get("/{relation_id}", response_model=dict)
async def get_relation(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        relation = await service.get_relation(relation_id)
        return {
            "status": "success",
            "data": schemas.SupplierRelationSummaryResponse.model_validate(relation),
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/{relation_id}/evaluation-workspace", response_model=dict)
async def get_relation_evaluation_workspace(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        workspace = await service.get_relation_evaluation_workspace(relation_id)
        return {
            "status": "success",
            "data": schemas.RelationEvaluationWorkspaceResponse(**workspace),
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/{relation_id}/status-history", response_model=dict)
async def get_relation_status_history(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        workspace = await service.get_relation_evaluation_workspace(relation_id)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.SupplierStatusHistoryResponse.model_validate(entry)
                    for entry in workspace["status_history"]
                ]
            },
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/criteria-documents", response_model=dict)
async def upload_relation_criteria_document(
    relation_id: int,
    criteria_type: str = Form(...),
    comments: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        uploaded_by = None
        if isinstance(current_user, dict):
            uploaded_by = (
                current_user.get("email")
                or current_user.get("upn")
                or current_user.get("sub")
            )
        document = await service.upload_criteria_document(
            relation_id=relation_id,
            criteria_type=criteria_type,
            file=file,
            uploaded_by=uploaded_by,
            comments=comments,
        )
        return {
            "status": "success",
            "data": schemas.EvaluationCriterionDocumentUploadResponse(
                relation_id=relation_id,
                criteria_type=criteria_type,
                document_id=document.id_document,
                document_name=document.document_name,
                original_file_name=document.original_file_name,
                file_url=document.file_url,
                mime_type=document.mime_type,
                file_size=document.file_size,
                uploaded_at=document.uploaded_at,
            ),
            "message": "Criterion document uploaded successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.delete("/{relation_id}/criteria-documents/{criteria_type}", response_model=dict)
async def delete_relation_criteria_document(
    relation_id: int,
    criteria_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        result = await service.delete_criteria_document(
            relation_id=relation_id,
            criteria_type=criteria_type,
        )
        return {
            "status": "success",
            "data": schemas.EvaluationCriterionDocumentDeleteResponse(**result),
            "message": "Criterion document deleted successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/initial-evaluation", response_model=dict)
async def create_initial_evaluation(
    relation_id: int,
    data: schemas.InitialRelationEvaluationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        result = await service.create_initial_evaluation(relation_id, data)
        return {
            "status": "success",
            "data": {
                "relation": schemas.SupplierRelationSummaryResponse.model_validate(
                    result["relation"]
                ),
                "cycle_id": result["cycle"].id_cycle,
                "score_card_id": result["score_card"].id_score_card
                if result["score_card"]
                else None,
                "classification_id": result["classification"].id_classification,
                "status_history_id": result["status_history"].id_history
                if result["status_history"]
                else None,
            },
            "message": "Initial relation evaluation saved successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.put("/{relation_id}/class-evaluation", response_model=dict)
async def update_class_evaluation(
    relation_id: int,
    data: schemas.ClassEvaluationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        result = await service.update_class_evaluation(relation_id, data)
        return {
            "status": "success",
            "data": {
                "relation": schemas.SupplierRelationSummaryResponse.model_validate(
                    result["relation"]
                ),
                "cycle_id": result["cycle"].id_cycle if result["cycle"] else None,
                "classification_id": result["classification"].id_classification
                if result["classification"]
                else None,
                "status_history_id": result["status_history"].id_history
                if result["status_history"]
                else None,
            },
            "message": "Class evaluation updated successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.put("/{relation_id}/operational-evaluation", response_model=dict)
async def update_operational_evaluation(
    relation_id: int,
    data: schemas.OperationalEvaluationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        result = await service.update_operational_evaluation(relation_id, data)
        return {
            "status": "success",
            "data": {
                "relation": schemas.SupplierRelationSummaryResponse.model_validate(
                    result["relation"]
                ),
                "cycle_id": result["cycle"].id_cycle,
                "score_card_id": result["score_card"].id_score_card
                if result["score_card"]
                else None,
                "classification_id": result["classification"].id_classification,
                "status_history_id": result["status_history"].id_history
                if result["status_history"]
                else None,
            },
            "message": "Operational evaluation updated successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/status-override", response_model=dict)
async def override_supplier_status(
    relation_id: int,
    data: schemas.SupplierStatusOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        result = await service.override_supplier_status(relation_id, data)
        return {
            "status": "success",
            "data": {
                "relation": schemas.SupplierRelationSummaryResponse.model_validate(
                    result["relation"]
                ),
                "status_history_id": result["status_history"].id_history,
                "computed_supplier_status": result["computed_supplier_status"],
            },
            "message": "Supplier status overridden successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


