"""Supplier relations router."""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.features.supplier_relations import schemas
from app.features.supplier_relations.service import SupplierRelationService
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

from app.db.models import Contact, ContactSiteRelation, SupplierSiteRelation
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/supplier-relations", tags=["supplier-relations"])


def _resolve_actor(current_user: dict | None) -> Optional[str]:
    if not isinstance(current_user, dict):
        return None
    return current_user.get("email") or current_user.get("upn") or current_user.get("sub")


class ContactRelationPayload(BaseModel):
    """Link an existing contact to a relation, or create a new one on-the-fly."""
    contact_id: Optional[int] = None       # link existing contact
    full_name: Optional[str] = None        # create new contact
    email: Optional[str] = None
    phone: Optional[str] = None
    role_label: Optional[str] = None
    id_supplier_unit: Optional[int] = None  # unit to associate the new contact with


@router.get("/{relation_id}/contacts", response_model=dict)
async def list_relation_contacts(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all contacts linked to a supplier-site relation."""
    from sqlalchemy import select
    stmt = (
        select(Contact)
        .join(ContactSiteRelation, ContactSiteRelation.id_contact == Contact.id_contact)
        .where(ContactSiteRelation.id_relation == relation_id)
        .where(ContactSiteRelation.is_deleted.is_(False))
        .where(Contact.is_deleted.is_(False))
    )
    result = await db.execute(stmt)
    contacts = result.scalars().all()
    return {
        "status": "success",
        "data": {
            "items": [
                {
                    "id_contact": c.id_contact,
                    "full_name": c.full_name,
                    "email": c.email,
                    "phone": c.phone,
                    "role_label": c.role_label,
                    "is_primary_contact": c.is_primary_contact,
                }
                for c in contacts
            ],
            "count": len(contacts),
        },
    }


@router.post("/{relation_id}/contacts", response_model=dict, status_code=201)
async def add_contact_to_relation(
    relation_id: int,
    data: ContactRelationPayload,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Link an existing contact (by contact_id) or create a new one, then attach to a relation."""
    from sqlalchemy import select

    relation = await db.get(SupplierSiteRelation, relation_id)
    if not relation:
        raise AppException(f"Relation {relation_id} not found", status_code=404)

    if data.contact_id:
        contact = await db.get(Contact, data.contact_id)
        if not contact:
            raise AppException(f"Contact {data.contact_id} not found", status_code=404)
    elif data.full_name:
        contact = Contact(
            full_name=data.full_name,
            email=data.email,
            phone=data.phone,
            role_label=data.role_label,
            id_supplier_unit=data.id_supplier_unit,
            is_primary_contact=False,
        )
        db.add(contact)
        await db.flush()
    else:
        raise AppException("Provide either contact_id or full_name", status_code=422)

    # Avoid duplicate junction rows
    existing_stmt = select(ContactSiteRelation).where(
        ContactSiteRelation.id_contact == contact.id_contact,
        ContactSiteRelation.id_relation == relation_id,
    )
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if not existing:
        db.add(ContactSiteRelation(id_contact=contact.id_contact, id_relation=relation_id))

    await db.commit()
    return {
        "status": "success",
        "data": {
            "id_contact": contact.id_contact,
            "full_name": contact.full_name,
            "email": contact.email,
            "role_label": contact.role_label,
        },
        "message": f"Contact linked to relation {relation_id}",
    }


@router.get("/criteria-validity", response_model=dict)
async def get_criteria_validity_bulk(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Bulk endpoint: returns criteria values + validity details for every relation
    in 4 DB queries. Used by the Criteria Validity Tracker page."""
    try:
        service = SupplierRelationService(db)
        items = await service.get_criteria_validity_bulk()
        return {"status": "success", "data": {"items": items, "total": len(items)}}
    except AppException as exc:
        return exc.to_response()


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


class RelationAdminPatch(BaseModel):
    """Partial update for admin-level relation fields."""
    panel_decision: Optional[str] = None


@router.patch("/{relation_id}", response_model=dict)
async def patch_relation(
    relation_id: int,
    data: RelationAdminPatch,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update admin-level fields on a supplier relation (panel_decision)."""
    from sqlalchemy import select
    from app.db.models import SupplierSiteRelation
    result = await db.execute(
        select(SupplierSiteRelation).where(SupplierSiteRelation.id_relation == relation_id)
    )
    relation = result.scalar_one_or_none()
    if not relation:
        from app.core.exceptions import AppException
        raise AppException(f"Relation {relation_id} not found", status_code=404)
    if data.panel_decision is not None:
        relation.panel_decision = data.panel_decision
    await db.commit()
    return {
        "status": "success",
        "data": {"id_relation": relation_id, "panel_decision": relation.panel_decision},
    }


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


@router.get("/{relation_id}/development-plans", response_model=dict)
async def list_relation_development_plans(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        items = await service.list_development_plans(relation_id)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.SupplierDevelopmentPlanResponse(**item)
                    for item in items
                ]
            },
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/development-plans/register", response_model=dict)
async def list_development_plan_register(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        items = await service.list_development_plan_register()
        return {
            "status": "success",
            "data": {
                "items": [
                    {
                        **schemas.DevelopmentPlanRegisterRowResponse(
                            relation=schemas.SupplierRelationSummaryResponse.model_validate(
                                item["relation"]
                            ),
                            development_plan=schemas.SupplierDevelopmentPlanResponse(
                                **item["development_plan"]
                            ),
                            site_name=item["site_name"],
                            site_city=item["site_city"],
                            site_country=item["site_country"],
                            unit_supplier_code=item["unit_supplier_code"],
                            unit_code=item["unit_code"],
                            group_id=item["group_id"],
                            group_name=item["group_name"],
                            group_code=item["group_code"],
                        ).model_dump(),
                        "documents": item.get("documents", []),
                    }
                    for item in items
                ]
            },
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/development-plans", response_model=dict)
async def create_relation_development_plan(
    relation_id: int,
    data: schemas.SupplierDevelopmentPlanCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        plan = await service.create_development_plan(relation_id, data)
        return {
            "status": "success",
            "data": schemas.SupplierDevelopmentPlanResponse(
                **service._serialize_development_plan(plan)
            ),
            "message": "Supplier development plan created successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.put("/{relation_id}/development-plans/{plan_id}", response_model=dict)
async def update_relation_development_plan(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        plan = await service.update_development_plan(relation_id, plan_id, data)
        return {
            "status": "success",
            "data": schemas.SupplierDevelopmentPlanResponse(
                **service._serialize_development_plan(plan)
            ),
            "message": "Supplier development plan updated successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/development-plans/{plan_id}/send-request", response_model=dict)
async def send_relation_development_plan_request(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        plan = await service.send_development_plan_request(relation_id, plan_id, data)
        return {
            "status": "success",
            "data": schemas.SupplierDevelopmentPlanResponse(
                **service._serialize_development_plan(plan)
            ),
            "message": "Development plan request email sent successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/{relation_id}/development-plans/{plan_id}/documents", response_model=dict)
async def list_plan_documents(
    relation_id: int,
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        docs = await service.get_plan_documents(relation_id, plan_id)
        return {
            "status": "success",
            "data": {
                "items": [
                    {
                        "id_document": d.id_document,
                        "file_name": d.original_file_name,
                        "file_url": d.file_url,
                        "file_notes": d.comments,
                        "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                        "comments": d.comments,
                    }
                    for d in docs
                ]
            },
        }
    except AppException:
        raise
    except Exception:
        raise


@router.delete(
    "/{relation_id}/development-plans/{plan_id}/documents/{document_id}",
    response_model=dict,
)
async def delete_plan_document(
    relation_id: int,
    plan_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        await service.delete_plan_document(relation_id, plan_id, document_id)
        return {"status": "success", "message": "Document deleted."}
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/development-plans/{plan_id}/send-reminder", response_model=dict)
async def send_development_plan_reminder(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanSendReminder,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        await service.send_development_plan_reminder(relation_id, plan_id, data)
        return {
            "status": "success",
            "message": "Reminder email sent to supplier.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/development-plans/{plan_id}/send-revision-request", response_model=dict)
async def send_revision_request(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanRevisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        await service.send_revision_request(relation_id, plan_id, data)
        return {"status": "success", "message": "Revision request email sent to supplier."}
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/development-plans/{plan_id}/send-decision-notification", response_model=dict)
async def send_decision_notification(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanDecisionNotification,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        await service.send_decision_notification(relation_id, plan_id, data)
        return {"status": "success", "message": "Decision notification email sent to supplier."}
    except AppException:
        raise
    except Exception:
        raise


@router.post(
    "/{relation_id}/development-plans/{plan_id}/send-received-notification",
    response_model=dict,
)
async def send_plan_received_notification(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanReceivedNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        await service.send_plan_received_notification(relation_id, plan_id, data)
        return {
            "status": "success",
            "message": "Received notification email sent with attached documents.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post(
    "/{relation_id}/development-plans/{plan_id}/send-review-notification",
    response_model=dict,
)
async def send_relation_development_plan_review_notification(
    relation_id: int,
    plan_id: int,
    data: schemas.SupplierDevelopmentPlanReviewNotificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = SupplierRelationService(db)
        plan = await service.send_development_plan_review_notification(
            relation_id, plan_id, data
        )
        return {
            "status": "success",
            "data": schemas.SupplierDevelopmentPlanResponse(
                **service._serialize_development_plan(plan)
            ),
            "message": "Review notification email sent to committee successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/development-plans/{plan_id}/document", response_model=dict)
async def upload_relation_development_plan_document(
    relation_id: int,
    plan_id: int,
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
        document = await service.upload_development_plan_file(
            relation_id=relation_id,
            plan_id=plan_id,
            file=file,
            uploaded_by=uploaded_by,
            comments=comments,
        )
        return {
            "status": "success",
            "data": schemas.DevelopmentPlanDocumentUploadResponse(
                relation_id=relation_id,
                plan_id=plan_id,
                document_id=document.id_document,
                document_name=document.document_name,
                original_file_name=document.original_file_name,
                file_url=document.file_url,
                mime_type=document.mime_type,
                file_size=document.file_size,
                uploaded_at=document.uploaded_at,
            ),
            "message": "Development plan document uploaded successfully.",
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


@router.put("/{relation_id}/evaluation-draft", response_model=dict)
async def save_evaluation_draft(
    relation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Persist raw evaluation form data as a draft — no business logic, no grade/status changes."""
    try:
        service = SupplierRelationService(db)
        await service.save_evaluation_draft(relation_id, payload)
        return {"status": "success", "message": "Draft saved."}
    except AppException:
        raise
    except Exception:
        raise


@router.delete("/{relation_id}/evaluation-draft", response_model=dict)
async def clear_evaluation_draft(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Clear the evaluation draft after a successful submit."""
    try:
        service = SupplierRelationService(db)
        await service.save_evaluation_draft(relation_id, None)
        return {"status": "success", "message": "Draft cleared."}
    except AppException:
        raise
    except Exception:
        raise


@router.get("/{relation_id}/evaluation-cycle-history")
async def get_evaluation_cycle_history(
    relation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Full IATF audit timeline — all cycles with snapshots and diffs."""
    try:
        service = SupplierRelationService(db)
        return await service.get_evaluation_cycle_history(relation_id)
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/documents/evaluation-reference", response_model=dict)
async def upload_evaluation_reference(
    relation_id: int,
    file: UploadFile = File(...),
    comments: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Upload a reference document for this relation's evaluation (e.g. the filled Excel scorecard)."""
    try:
        service = SupplierRelationService(db)
        doc = await service.upload_evaluation_reference(
            relation_id=relation_id,
            file=file,
            uploaded_by=_resolve_actor(current_user),
            comments=comments,
        )
        return {
            "status": "success",
            "data": {
                "id_document": doc.id_document,
                "document_name": doc.document_name,
                "file_url": doc.file_url,
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            },
            "message": "Evaluation reference uploaded.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/{relation_id}/documents/lta", response_model=dict)
async def upload_lta_document(
    relation_id: int,
    file: UploadFile = File(...),
    comments: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Upload the Long Term Agreement document for this relation."""
    try:
        service = SupplierRelationService(db)
        doc = await service.upload_lta_document(
            relation_id=relation_id,
            file=file,
            uploaded_by=_resolve_actor(current_user),
            comments=comments,
        )
        return {
            "status": "success",
            "data": {
                "id_document": doc.id_document,
                "document_name": doc.document_name,
                "file_url": doc.file_url,
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            },
            "message": "LTA document uploaded.",
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


