"""Suppliers router."""

from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from sqlalchemy import select

from app.shared.dependencies.db import get_db
from app.shared.dependencies.auth import get_current_user
from app.features.suppliers.service import SupplierService
from app.features.suppliers import schemas
from app.features.supplier_onboarding.workflow import SupplierOnboardingWorkflow
from app.core.exceptions import AppException
from app.db.models import SupplierGroup, SupplierUnit, SupplierSiteRelation, AvocarbonSite
from app.features.auth.models import AccessIdentity
from app.features.notifications.service import NotificationService

from sqlalchemy.orm import Session

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


_NON_VIEWER = ["purchasing_manager", "vp_conversion", "purchasing_director", "supplier_owner", "global_purchaser", "local_purchaser"]


def _block_viewer(current_user: dict) -> None:
    """Raise 403 if the caller is a viewer (read-only role)."""
    if current_user.get("access_profile") == "viewer":
        raise HTTPException(status_code=403, detail="Viewer role cannot perform write operations.")


def _require_purchasing_manager(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("access_profile") != "purchasing_manager":
        raise HTTPException(status_code=403, detail="Purchasing manager role required")
    return current_user


def _require_vp_conversion(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("access_profile") != "vp_conversion":
        raise HTTPException(status_code=403, detail="VP Conversion role required for supplier validation.")
    return current_user


def _raise_clearer_unit_persistence_error(exc: Exception) -> None:
    if not isinstance(exc, DBAPIError):
        return

    message = str(exc.orig or exc).lower()
    if "character varying" not in message:
        return

    if "too long" in message or "trop longue" in message or "righttruncation" in message:
        raise AppException(
            "One of the supplier unit fields is longer than the database allows. Please shorten values like country, unit name, category, or percentage fields and try again.",
            status_code=400,
        ) from exc



def _resolve_actor(current_user: dict | None) -> Optional[str]:
    if not isinstance(current_user, dict):
        return None
    return (
        current_user.get("email") or current_user.get("upn") or current_user.get("sub")
    )


# ============================================================================
# Complete Supplier Onboarding Workflow (Main Endpoint)
# ============================================================================


@router.post(
    "/onboarding/complete",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    tags=["onboarding"],
)
async def complete_supplier_onboarding(
    data: schemas.CompleteSupplierOnboardingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    This is the main endpoint for onboarding a new supplier. It handles:

    **Supplier Creation**: Creates supplier group, unit, contacts, and certifications


    **Request Schema Example:**
    ```json
    {
      "group": {
        "nom": "Acme Manufacturing Ltd",
        "supplier_scope": "global",
        "strategique": true,
        "supplier_type": "manufacturer"
      },
      "unit": {
        "supplier_code": "ACME-CN-001",
        "address_line": "123 Industrial Park",
        "city": "Shanghai",
        "country": "China",
        "product_type": "Electronics",
        "product_category": "Semiconductors",
        "amount_currency": "USD"
      },
      "contacts": [
        {
          "full_name": "John Zhang",
          "email": "john@acme-cn.com",
          "role_label": "Quality Manager",
          "is_primary_contact": true
        }
      ],
      "certifications": [
        {
          "certification_type": "ISO 9001:2015",
          "certificate_name": "Quality Management System",
          "start_date": "2024-01-01",
          "end_date": "2027-01-01"
        }
      ],
      "site_id": 1,
      "supplier_scope": "global",
      "supplier_owner": "procurement.manager@avocarbon.com",
      "template_id": null
    }
    ```

    **Response includes:**
    - Created supplier details (group, unit)
    - Relation details with owner and classification
    - Contact information
    - Prequalification cycle and assessment details
    - Email notification status (success/failure for each email)
    """
    try:
        workflow = SupplierOnboardingWorkflow(db)

        result = await workflow.create_supplier_complete_workflow(
            group_data=data.group.model_dump(exclude_unset=True),
            unit_data=data.unit.model_dump(exclude_unset=True),
            contacts=[c.model_dump(exclude_unset=True) for c in data.contacts],
            certifications=[
                c.model_dump(exclude_unset=True) for c in data.certifications
            ],
            site_id=data.site_id,
            supplier_scope=data.supplier_scope,
            supplier_owner=data.supplier_owner,
            template_id=data.template_id,
            evaluation=data.evaluation.model_dump(exclude_unset=True)
            if data.evaluation
            else None,
            unit_contacts=[c.model_dump(exclude_unset=True) for c in data.unit_contacts],
            annual_spend_value=data.annual_spend_value,
            annual_spend_currency=data.annual_spend_currency,
        )

        # Notify all purchasing managers that a new supplier awaits validation
        group_name = result["supplier"]["group_name"]
        group_id = result["supplier"]["group_id"]
        managers = (await db.execute(
            select(AccessIdentity).where(
                AccessIdentity.access_profile == "purchasing_manager",
                AccessIdentity.is_active == True,
                AccessIdentity.registration_status == "active",
            )
        )).scalars().all()

        notif_svc = NotificationService(db)
        for mgr in managers:
            await notif_svc.create_notification(
                recipient_id=mgr.id_identity,
                notification_type="supplier_pending_validation",
                title=f"New supplier to validate: {group_name}",
                body="A new supplier has been onboarded and is awaiting your validation before being added to the panel.",
                action_url=f"/pending-validation/{group_id}",
            )
        await db.commit()

        return {
            "status": "success",
            "data": result,
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# Supplier Validation (Purchasing Manager only)
# ============================================================================


@router.get("/pending-validation", response_model=dict, tags=["validation"])
async def list_pending_validation(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(_require_vp_conversion),
):
    """List all supplier groups awaiting purchasing manager validation."""
    rows = (await db.execute(
        select(
            SupplierGroup,
            SupplierUnit,
            SupplierSiteRelation,
            AvocarbonSite,
        )
        .join(SupplierUnit, SupplierUnit.id_group == SupplierGroup.id_group)
        .outerjoin(SupplierSiteRelation, SupplierSiteRelation.id_supplier_unit == SupplierUnit.id_supplier_unit)
        .outerjoin(AvocarbonSite, AvocarbonSite.id_site == SupplierSiteRelation.id_site)
        .where(
            SupplierGroup.validation_status == "pending",
            SupplierGroup.is_deleted == False,
            SupplierUnit.is_deleted == False,
        )
        .order_by(SupplierGroup.id_group.desc())
    )).all()

    # Deduplicate by group_id: one card per group even if multiple relations exist
    seen_groups: set[int] = set()
    items = []
    for group, unit, relation, site in rows:
        if group.id_group in seen_groups:
            continue
        seen_groups.add(group.id_group)
        items.append({
            "group_id": group.id_group,
            "group_name": group.nom,
            "group_code": f"GRP-{group.id_group:06d}",
            "validation_status": group.validation_status,
            "unit_id": unit.id_supplier_unit,
            "unit_code": unit.supplier_code,
            "unit_country": unit.country,
            "relation_id": relation.id_relation if relation else None,
            "site_id": site.id_site if site else None,
            "site_name": site.site_name if site else None,
            "supplier_scope": relation.global_status if relation else None,
            "supplier_owner": relation.buyer_owner if relation else None,
            "created_at": group.updated_at,
        })

    return {"status": "success", "data": items, "total": len(items)}


async def _notify_buyer_for_group(
    db: AsyncSession,
    group: SupplierGroup,
    notification_type: str,
    title: str,
    body: str,
    action_url: str,
) -> None:
    """Find the buyer_owner on the first relation of this group and notify them."""
    result = await db.execute(
        select(SupplierSiteRelation)
        .join(SupplierUnit, SupplierUnit.id_supplier_unit == SupplierSiteRelation.id_supplier_unit)
        .where(SupplierUnit.id_group == group.id_group)
        .limit(1)
    )
    relation = result.scalar_one_or_none()
    buyer_email = (relation.buyer_owner if relation else None) or group.group_supplier_owner_email
    if not buyer_email:
        return

    buyer = (await db.execute(
        select(AccessIdentity).where(
            AccessIdentity.email == buyer_email,
            AccessIdentity.is_active == True,
        )
    )).scalar_one_or_none()

    if buyer:
        notif_svc = NotificationService(db)
        await notif_svc.create_notification(
            recipient_id=buyer.id_identity,
            notification_type=notification_type,
            title=title,
            body=body,
            action_url=action_url,
        )


@router.post("/groups/{group_id}/approve", response_model=dict, tags=["validation"])
async def approve_supplier(
    group_id: int,
    body: schemas.ValidationDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(_require_vp_conversion),
):
    """Approve a pending supplier — it will become visible in the supplier panel."""
    group = await db.get(SupplierGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Supplier group not found")
    if group.validation_status != "pending":
        raise HTTPException(status_code=400, detail=f"Supplier is already '{group.validation_status}'")

    group.validation_status = "approved"

    await _notify_buyer_for_group(
        db=db,
        group=group,
        notification_type="supplier_approved",
        title=f"Supplier approved: {group.nom}",
        body="Your supplier has been validated and is now visible in the panel.",
        action_url="/suppliers",
    )
    await db.commit()

    return {"status": "success", "message": f"Supplier '{group.nom}' approved and added to panel."}


@router.post("/groups/{group_id}/reject", response_model=dict, tags=["validation"])
async def reject_supplier(
    group_id: int,
    body: schemas.ValidationDecisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(_require_vp_conversion),
):
    """Reject a pending supplier."""
    group = await db.get(SupplierGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Supplier group not found")
    if group.validation_status != "pending":
        raise HTTPException(status_code=400, detail=f"Supplier is already '{group.validation_status}'")

    group.validation_status = "rejected"

    reason_text = body.comment or "No reason provided."
    await _notify_buyer_for_group(
        db=db,
        group=group,
        notification_type="supplier_rejected",
        title=f"Supplier rejected: {group.nom}",
        body=f"Your supplier was not validated. Reason: {reason_text}",
        action_url="/suppliers",
    )
    await db.commit()

    return {"status": "success", "message": f"Supplier '{group.nom}' rejected."}


# ============================================================================
# SupplierGroup Endpoints
# ============================================================================


@router.get("/groups", response_model=dict)
async def list_supplier_groups(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    List all supplier groups with pagination.

    - **skip**: Number of records to skip (default: 0)
    - **limit**: Number of records to return (default: 100, max: 1000)
    """
    try:
        service = SupplierService(db)
        result = await service.list_supplier_groups(skip=skip, limit=limit)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.SupplierGroupResponse.model_validate(g)
                    for g in result["items"]
                ],
                "total": result["total"],
                "skip": result["skip"],
                "limit": result["limit"],
            },
            "message": f"Found {result['total']} supplier groups",
        }
    except Exception:
        raise


@router.get("/groups/{group_id}", response_model=dict)
async def get_supplier_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get a specific supplier group with its units, contacts, and documents."""
    try:
        service = SupplierService(db)
        group = await service.get_supplier_group(group_id)
        return {
            "status": "success",
            "data": schemas.SupplierDetailResponse.model_validate(group),
            "message": f"Supplier group {group_id} retrieved",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/groups", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_supplier_group(
    data: schemas.SupplierGroupCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create a new supplier group.

    **Required fields:**
    - **nom**: Supplier group name (max 200 chars)

    **Optional fields:**
    - **supplier_scope**: Scope (local/regional/global)
    - **strategique**: Is this a strategic supplier?
    - **monopolistique**: Is this monopolistic?
    - **multi_site**: Multiple sites?
    - **directed**: Is directed/approved?
    - **exit_supplier**: In exit status?
    - **strategic_reason**: Why strategic?
    - **supplier_type**: Type of supplier
    """
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        group = await service.create_supplier_group(data)
        return {
            "status": "success",
            "data": schemas.SupplierGroupResponse.model_validate(group),
            "message": f"Supplier group '{data.nom}' created successfully",
            "id": group.id_group,
        }
    except AppException:
        raise
    except Exception:
        raise


@router.put("/groups/{group_id}", response_model=dict)
async def update_supplier_group(
    group_id: int,
    data: schemas.SupplierGroupUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update an existing supplier group."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        group = await service.update_supplier_group(group_id, data)
        return {
            "status": "success",
            "data": schemas.SupplierGroupResponse.model_validate(group),
            "message": f"Supplier group {group_id} updated successfully",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.delete("/groups/{group_id}", response_model=dict)
async def delete_supplier_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete a supplier group and all associated units."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        success = await service.delete_supplier_group(group_id, changed_by=_resolve_actor(current_user))
        return {
            "status": "success",
            "data": {"deleted": success},
            "message": f"Supplier group {group_id} deleted successfully",
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# SupplierUnit Endpoints
# ============================================================================


@router.get("/units", response_model=dict)
async def list_supplier_units(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all supplier units with pagination."""
    try:
        service = SupplierService(db)
        result = await service.list_supplier_units(skip=skip, limit=limit)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.SupplierUnitResponse.model_validate(u)
                    for u in result["items"]
                ],
                "total": result["total"],
                "skip": result["skip"],
                "limit": result["limit"],
            },
            "message": f"Found {result['total']} supplier units",
        }
    except Exception:
        raise


@router.get("/units/{unit_id}", response_model=dict)
async def get_supplier_unit(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get a specific supplier unit."""
    try:
        service = SupplierService(db)
        unit = await service.get_supplier_unit(unit_id)
        return {
            "status": "success",
            "data": schemas.SupplierUnitResponse.model_validate(unit),
            "message": f"Supplier unit {unit_id} retrieved",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/groups/{group_id}/units", response_model=dict)
async def list_units_for_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all supplier units for a specific supplier group."""
    try:
        service = SupplierService(db)
        units = await service.list_units_by_group(group_id)
        return {
            "status": "success",
            "data": {
                "units": [
                    schemas.SupplierUnitResponse.model_validate(u) for u in units
                ],
                "count": len(units),
            },
            "message": f"Found {len(units)} units for group {group_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post(
    "/groups/{group_id}/units/complete",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_unit_complete(
    group_id: int,
    data: schemas.CreateUnitCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a unit with its contacts and certifications in a single transaction."""
    _block_viewer(current_user)
    from app.db.models import Contact, SupplierCertification, SupplierUnit
    from sqlalchemy import select

    # Verify group exists
    from app.db.models import SupplierGroup
    group = await db.get(SupplierGroup, group_id)
    if not group:
        raise AppException(f"Supplier group {group_id} not found", status_code=404)

    # Enforce unique supplier_code within the same supplier group (active units only)
    existing_unit_stmt = select(SupplierUnit).where(
        SupplierUnit.id_group == group_id,
        SupplierUnit.supplier_code == data.unit.supplier_code,
        SupplierUnit.is_deleted.is_(False),
    )
    existing_unit = (await db.execute(existing_unit_stmt)).scalar_one_or_none()
    if existing_unit:
        raise AppException(
            f"A supplier unit with name '{data.unit.supplier_code}' already exists in this supplier group. "
            "Each unit name must be unique within the same group.",
            status_code=409,
        )

    try:
        unit_data = data.unit.model_dump(exclude_unset=True)
        unit_data["id_group"] = group_id
        unit = SupplierUnit(**unit_data)
        db.add(unit)
        await db.flush()

        created_contacts = []
        for c in data.contacts:
            contact_data = c.model_dump(exclude_unset=True)
            contact_data["id_supplier_unit"] = unit.id_supplier_unit
            contact = Contact(**contact_data)
            db.add(contact)
            await db.flush()
            created_contacts.append(contact)

        created_certs = []
        for cert in data.certifications:
            cert_data = cert.model_dump(exclude_unset=True)
            cert_data["id_supplier_unit"] = unit.id_supplier_unit
            certification = SupplierCertification(**cert_data)
            db.add(certification)
            await db.flush()
            created_certs.append(certification)

        await db.commit()

        return {
            "status": "success",
            "data": {
                "unit": schemas.SupplierUnitResponse.model_validate(unit),
                "contacts_count": len(created_contacts),
                "certifications_count": len(created_certs),
            },
            "message": f"Unit '{unit.supplier_code}' created with {len(created_contacts)} contact(s) and {len(created_certs)} certification(s)",
        }
    except Exception as exc:
        await db.rollback()
        _raise_clearer_unit_persistence_error(exc)
        raise


@router.post("/units", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_supplier_unit(
    data: schemas.SupplierUnitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create a new supplier unit (manufacturing/operating location).

    **Required fields:**
    - **supplier_code**: Unique identifier (max 50 chars)

    **Optional fields:**
    - **id_group**: Parent supplier group ID
    - **address_line**: Street address
    - **city**: City
    - **country**: Country
    - **product_type**: Type of products
    - **product_category**: Product category
    - **amount_value**: Annual spend
    - **amount_currency**: Currency code
    """
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        unit = await service.create_supplier_unit(
            data, changed_by=_resolve_actor(current_user)
        )
        return {
            "status": "success",
            "data": schemas.SupplierUnitResponse.model_validate(unit),
            "message": f"Supplier unit '{data.supplier_code}' created successfully",
            "id": unit.id_supplier_unit,
        }
    except AppException:
        raise
    except Exception:
        raise


@router.put("/units/{unit_id}", response_model=dict)
async def update_supplier_unit(
    unit_id: int,
    data: schemas.SupplierUnitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update an existing supplier unit."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        unit = await service.update_supplier_unit(
            unit_id, data, changed_by=_resolve_actor(current_user)
        )
        return {
            "status": "success",
            "data": schemas.SupplierUnitResponse.model_validate(unit),
            "message": f"Supplier unit {unit_id} updated successfully",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.delete("/units/{unit_id}", response_model=dict)
async def delete_supplier_unit(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Delete a supplier unit."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        success = await service.delete_supplier_unit(
            unit_id, changed_by=_resolve_actor(current_user)
        )
        return {
            "status": "success",
            "data": {"deleted": success},
            "message": f"Supplier unit {unit_id} deleted successfully",
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# Supplier-Site Relation Endpoints (Link units to sites)
# ============================================================================


@router.post(
    "/units/{unit_id}/sites/{site_id}",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def link_unit_to_site(
    unit_id: int,
    site_id: int,
    data: Optional[schemas.SupplierSiteRelationCreate] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Link a supplier unit to an Avocarbon site, creating a supplier-site relation.

    This allows a single unit to be linked to multiple sites with assignment-specific
    owner and scope. Supplier qualification remains unit-centered.

    **Optional fields in request body:**
    - **supplier_scope**: Assignment scope (global/regional/local/strategic)
    - **supplier_owner**: Name or email of owner
    - **evaluation_frequency**: How often to evaluate
    """
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        relation = await service.create_supplier_site_relation(
            unit_id=unit_id,
            site_id=site_id,
            data=data.model_dump(exclude_unset=True) if data else {},
            changed_by=_resolve_actor(current_user),
        )
        return {
            "status": "success",
            "data": schemas.SupplierSiteRelationResponse.model_validate(relation),
            "message": f"Supplier unit {unit_id} linked to site {site_id}",
            "relation_id": relation.id_relation,
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/units/{unit_id}/sites", response_model=dict)
async def list_sites_for_unit(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all Avocarbon sites linked to a specific supplier unit."""
    try:
        service = SupplierService(db)
        relations = await service.list_unit_site_relations(unit_id)
        return {
            "status": "success",
            "data": {
                "relations": [
                    schemas.SupplierSiteRelationResponse.model_validate(r)
                    for r in relations
                ],
                "count": len(relations),
            },
            "message": f"Found {len(relations)} sites linked to unit {unit_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/units/{unit_id}/evaluation-summary", response_model=dict)
async def get_unit_evaluation_summary(
    unit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get the latest qualification snapshot for a supplier unit."""
    try:
        service = SupplierService(db)
        summary = await service.get_unit_evaluation_summary(unit_id)
        return {
            "status": "success",
            "data": schemas.UnitEvaluationSummaryResponse(**summary),
            "message": f"Evaluation summary retrieved for unit {unit_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/groups/{group_id}/audit-trail", response_model=dict)
async def get_group_audit_trail(
    group_id: int,
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Get recent audit events for a supplier group and its related units and site links."""
    try:
        service = SupplierService(db)
        items = await service.get_group_audit_trail(group_id, limit=limit)
        return {
            "status": "success",
            "data": {
                "items": items,
                "count": len(items),
                "limit": limit,
            },
            "message": f"Found {len(items)} audit events for group {group_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post(
    "/units/{unit_id}/initial-evaluation",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def create_initial_unit_evaluation(
    unit_id: int,
    data: schemas.InitialUnitEvaluationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Save the initial supplier evaluation baseline for a specific unit.

    The unit must already be assigned to at least one site; the first existing
    relation is used as the evaluation context until unit-owned evaluation
    tables are introduced.
    """
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        result = await service.create_initial_unit_evaluation(
            unit_id=unit_id,
            data=data,
            changed_by=_resolve_actor(current_user),
        )
        return {
            "status": "success",
            "data": schemas.InitialUnitEvaluationResponse(
                unit_id=unit_id,
                relation_id=result["relation"].id_relation,
                cycle_id=result["cycle"].id_cycle,
                score_card_id=result["score_card"].id_score_card
                if result["score_card"]
                else None,
                classification_id=result["classification"].id_classification
                if result["classification"]
                else None,
                status_history_id=result["status_history"].id_history
                if result["status_history"]
                else None,
                final_grade=result["relation"].final_grade,
                class_value=result["relation"].class_value,
                operational_grade=result["relation"].operational_grade,
                panel_decision=result["relation"].panel_decision,
            ),
            "message": f"Initial evaluation saved for unit {unit_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.delete("/units/{unit_id}/sites/{site_id}", response_model=dict)
async def unlink_unit_from_site(
    unit_id: int,
    site_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Remove the link between a supplier unit and an Avocarbon site."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        success = await service.delete_supplier_site_relation(
            unit_id, site_id, changed_by=_resolve_actor(current_user)
        )
        return {
            "status": "success",
            "data": {"deleted": success},
            "message": f"Supplier unit {unit_id} unlinked from site {site_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# Complete Supplier Creation (Composite Endpoint)
# ============================================================================


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_complete_supplier(
    data: schemas.CreateSupplierRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create a complete supplier with all related entities in a single transaction.

    This endpoint creates:
    - A new Supplier Group (top-level entity)
    - A Supplier Unit (manufacturing location) linked to the group
    - Associated Contacts (optional)
    - Associated Certifications (optional)

    **Request body structure:**
    ```json
    {
      "group": {
        "nom": "Acme Manufacturing",
        "supplier_scope": "global",
        "strategique": true,
        "supplier_type": "manufacturer"
      },
      "unit": {
        "supplier_code": "ACME001",
        "address_line": "123 Industrial Way",
        "city": "Shanghai",
        "country": "China",
        "product_type": "Electronics",
        "product_category": "Semiconductors"
      },
      "contacts": [
        {
          "full_name": "John Doe",
          "role_label": "Quality Manager",
          "email": "john@acme.com",
          "is_primary_contact": true
        }
      ],
      "certifications": [
        {
          "certification_type": "ISO 9001",
          "certificate_name": "Quality Management",
          "start_date": "2024-01-01",
          "end_date": "2027-01-01"
        }
      ]
    }
    ```
    """
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        result = await service.create_complete_supplier(data=data)

        group = result["group"]
        unit = result["unit"]

        await db.commit()

        return {
            "status": "success",
            "data": {
                "group_id": group.id_group,
                "group_code": group.group_code,
                "group_name": group.nom,
                "unit_id": unit.id_supplier_unit,
                "unit_code": unit.supplier_code,
                "unit_reference_code": unit.unit_code,
                "contacts_count": len(result["contacts"]),
                "certifications_count": len(result["certifications"]),
            },
            "message": f"Supplier '{data.group.nom}' created successfully with unit '{data.unit.supplier_code}'",
            "details": {
                "group": schemas.SupplierGroupResponse.model_validate(group),
                "unit": schemas.SupplierUnitResponse.model_validate(unit),
                "contacts": [
                    schemas.ContactResponse.model_validate(c)
                    for c in result["contacts"]
                ],
                "certifications": [
                    schemas.SupplierCertificationResponse.model_validate(c)
                    for c in result["certifications"]
                ],
            },
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# Contact Management Endpoints
# ============================================================================


@router.post(
    "/groups/{group_id}/contacts",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def add_contact_to_group(
    group_id: int,
    data: schemas.ContactCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Add a contact to a supplier group."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        contact = await service.create_contact_for_group(group_id, data)
        return {
            "status": "success",
            "data": schemas.ContactResponse.model_validate(contact),
            "message": f"Contact '{data.full_name}' added to group {group_id}",
            "id": contact.id_contact,
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/groups/{group_id}/contacts", response_model=dict)
async def list_contacts_for_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List contacts for a supplier group."""
    try:
        service = SupplierService(db)
        contacts = await service.list_contacts_for_group(group_id)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.ContactResponse.model_validate(contact)
                    for contact in contacts
                ],
                "count": len(contacts),
            },
            "message": f"Found {len(contacts)} contacts for group {group_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post(
    "/units/{unit_id}/contacts",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def add_contact_to_unit(
    unit_id: int,
    data: schemas.ContactCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Add a contact to a supplier unit."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        contact = await service.create_contact_for_unit(unit_id, data)
        return {
            "status": "success",
            "data": schemas.ContactResponse.model_validate(contact),
            "message": f"Contact '{data.full_name}' added to unit {unit_id}",
            "id": contact.id_contact,
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/units/{unit_id}/contacts", response_model=dict)
async def list_contacts_for_unit(
    unit_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List contacts for a supplier unit."""
    try:
        service = SupplierService(db)
        contacts = await service.list_contacts_for_unit(unit_id)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.ContactResponse.model_validate(contact)
                    for contact in contacts
                ],
                "count": len(contacts),
            },
            "message": f"Found {len(contacts)} contacts for unit {unit_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# Certification Management Endpoints
# ============================================================================


@router.post(
    "/units/{unit_id}/certifications",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def add_certification_to_unit(
    unit_id: int,
    data: schemas.SupplierCertificationCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Add a certification to a supplier unit."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        cert = await service.create_certification_for_unit(unit_id, data)
        from app.features.supplier_relations.service import SupplierRelationService
        rel_service = SupplierRelationService(db)
        affected = await rel_service.sync_quality_certification_for_unit(
            unit_id,
            triggered_by=_resolve_actor(current_user),
            source_cert_id=cert.id_certification,
            change="create",
        )
        return {
            "status": "success",
            "data": schemas.SupplierCertificationResponse.model_validate(cert),
            "affected_evaluations": affected,
            "message": f"Certification added to unit {unit_id}."
                       f"{f' {len(affected)} evaluation(s) recomputed.' if affected else ''}",
            "id": cert.id_certification,
        }
    except AppException:
        raise
    except Exception:
        raise


@router.patch("/units/{unit_id}/certifications/{cert_id}", response_model=dict)
async def patch_certification(
    unit_id: int,
    cert_id: int,
    data: schemas.SupplierCertificationUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update a certification's dates, type, or document. Quality certs cascade to evaluations."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        # Capture the cert's standard_type BEFORE the patch: if it was "quality" and is
        # being moved to another standard, we must still re-sync so the relation stops
        # referencing it as a quality cert.
        cert = await service.patch_certification(unit_id, cert_id, data)
        from app.features.supplier_relations.service import SupplierRelationService
        rel_service = SupplierRelationService(db)
        affected = await rel_service.sync_quality_certification_for_unit(
            unit_id,
            triggered_by=_resolve_actor(current_user),
            source_cert_id=cert.id_certification,
            change="update",
        )
        return {
            "status": "success",
            "data": schemas.SupplierCertificationResponse.model_validate(cert),
            "affected_evaluations": affected,
            "message": f"Certification updated.{f' {len(affected)} evaluation(s) recomputed.' if affected else ''}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.delete("/units/{unit_id}/certifications/{cert_id}", response_model=dict)
async def delete_certification(
    unit_id: int,
    cert_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete a certification. If it was a quality cert, re-derive the
    quality_certification criterion on all affected relations."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        cert = await service.soft_delete_certification(
            unit_id, cert_id, deleted_by=_resolve_actor(current_user)
        )
        from app.features.supplier_relations.service import SupplierRelationService
        rel_service = SupplierRelationService(db)
        affected = await rel_service.sync_quality_certification_for_unit(
            unit_id,
            triggered_by=_resolve_actor(current_user),
            source_cert_id=cert_id,
            change="delete",
        )
        return {
            "status": "success",
            "data": schemas.SupplierCertificationResponse.model_validate(cert),
            "affected_evaluations": affected,
            "message": f"Certification removed.{f' {len(affected)} evaluation(s) recomputed.' if affected else ''}",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/units/{unit_id}/certifications/{cert_id}/file", response_model=dict)
async def upload_certification_file(
    unit_id: int,
    cert_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Upload or replace the document file for a certification."""
    _block_viewer(current_user)
    from app.shared.utils.blob_storage import upload_certification_document, _extract_blob_name, delete_blob
    try:
        service = SupplierService(db)
        cert = await service.repo.find_certification_by_id(cert_id)
        if not cert or cert.id_supplier_unit != unit_id:
            from app.core.exceptions import AppException
            raise AppException(f"Certification {cert_id} not found for unit {unit_id}", status_code=404)

        # Delete old blob if present
        if cert.file_url:
            old_blob = _extract_blob_name(cert.file_url)
            if old_blob:
                try:
                    await delete_blob(old_blob)
                except Exception:
                    pass

        result = await upload_certification_document(file, unit_id, cert_id)
        await service.repo.update_certification(cert_id, {
            "file_name": result["filename"],
            "file_url":  result["file_url"],
            "file_size": result["size"],
        })
        await db.commit()
        updated = await service.repo.find_certification_by_id(cert_id)
        return {
            "status": "success",
            "data": schemas.SupplierCertificationResponse.model_validate(updated),
            "message": f"File '{result['filename']}' uploaded successfully.",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.get("/units/{unit_id}/certifications", response_model=dict)
async def list_certifications_for_unit(
    unit_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List certifications for a supplier unit."""
    try:
        service = SupplierService(db)
        certifications = await service.list_certifications_for_unit(unit_id)
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.SupplierCertificationResponse.model_validate(certification)
                    for certification in certifications
                ],
                "count": len(certifications),
            },
            "message": f"Found {len(certifications)} certifications for unit {unit_id}",
        }
    except AppException:
        raise
    except Exception:
        raise


# ============================================================================
# Carbon Footprint Endpoints (SB8)
# ============================================================================


@router.get("/carbon-footprints", response_model=dict)
async def list_carbon_footprints(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=2000),
    unit_id: Optional[int] = Query(None, description="Filter by supplier unit ID"),
    relation_id: Optional[int] = Query(None, description="Filter by relation ID"),
    year: Optional[int] = Query(None, description="Filter by year"),
    continent: Optional[str] = Query(None, description="Filter by supplier continent"),
    origin: Optional[str] = Query(None, description="Filter by supplier origin country"),
    site_location: Optional[str] = Query(None, description="Filter by site location"),
    supplier_unit_code: Optional[str] = Query(None, description="Filter by supplier unit code (SAP)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List carbon footprint records (SB8) with optional filters."""
    try:
        service = SupplierService(db)
        result = await service.list_carbon_footprints(
            skip=skip,
            limit=limit,
            unit_id=unit_id,
            relation_id=relation_id,
            year=year,
            continent=continent,
            origin=origin,
            site_location=site_location,
            supplier_unit_code=supplier_unit_code,
        )
        return {
            "status": "success",
            "data": {
                "items": [
                    {
                        **schemas.SupplierCarbonFootprintResponse.model_validate(fp).model_dump(),
                        "supplier_unit_code": fp.supplier_unit.supplier_code if fp.supplier_unit else None,
                    }
                    for fp in result["items"]
                ],
                "total": result["total"],
                "total_all": result["total_all"],
                "skip": result["skip"],
                "limit": result["limit"],
            },
            "message": f"Found {result['total']} carbon footprint records ({result['total_all']} total in DB)",
        }
    except Exception:
        raise


@router.patch("/carbon-footprints/{fp_id}", response_model=dict)
async def update_carbon_footprint(
    fp_id: int,
    body: schemas.CarbonFootprintUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Update specific fields of a carbon footprint record."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        fp = await service.update_carbon_footprint(fp_id, body.model_dump(exclude_none=True))
        if not fp:
            raise AppException(status_code=404, detail="Carbon footprint record not found")
        return {
            "status": "success",
            "data": {
                **schemas.SupplierCarbonFootprintResponse.model_validate(fp).model_dump(),
                "supplier_unit_code": fp.supplier_unit.supplier_code if fp.supplier_unit else None,
            },
            "message": "Carbon footprint record updated",
        }
    except AppException:
        raise
    except Exception:
        raise


@router.post("/carbon-footprints", response_model=dict, status_code=201)
async def create_carbon_footprint(
    body: schemas.CarbonFootprintCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Create a new carbon footprint record."""
    _block_viewer(current_user)
    try:
        service = SupplierService(db)
        fp = await service.create_carbon_footprint(body.model_dump(exclude_none=True))
        return {
            "status": "success",
            "data": {
                **schemas.SupplierCarbonFootprintResponse.model_validate(fp).model_dump(),
                "supplier_unit_code": None,
            },
            "message": "Carbon footprint record created",
        }
    except Exception:
        raise


# ============================================================================
# Certifications Tracking Endpoints
# ============================================================================


@router.post("/certifications/sync-quality", response_model=dict)
async def sync_quality_certifications(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retroactively sync quality_certification criteria on all relations from their
    unit's certifications. Use this once to backfill relations created before the
    auto-sync was in place."""
    _block_viewer(current_user)
    try:
        from sqlalchemy import select as sa_select
        from app.db.models import SupplierCertification, SupplierSiteRelation
        from app.features.supplier_relations.service import SupplierRelationService

        # Find all distinct unit IDs that have at least one certification
        stmt = sa_select(SupplierCertification.id_supplier_unit).where(
            SupplierCertification.is_deleted.is_(False)
        ).distinct()
        unit_ids = (await db.execute(stmt)).scalars().all()

        rel_service = SupplierRelationService(db)
        actor = _resolve_actor(current_user)
        total_affected = 0
        synced_units = 0
        for unit_id in unit_ids:
            affected = await rel_service.sync_quality_certification_for_unit(
                unit_id,
                triggered_by=actor,
                change="update",
            )
            total_affected += len(affected)
            synced_units += 1

        return {
            "status": "success",
            "message": f"Synced {synced_units} supplier unit(s). {total_affected} evaluation(s) recomputed.",
            "synced_units": synced_units,
            "recomputed_evaluations": total_affected,
        }
    except Exception:
        raise


@router.get("/certifications/summary", response_model=dict)
async def get_certifications_summary(
    standard_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return counts by validity status for the KPI strip (unfiltered by status)."""
    try:
        service = SupplierService(db)
        result = await service.get_certifications_summary(standard_type=standard_type, q=q)
        return {"status": "success", "data": result}
    except Exception:
        raise


@router.get("/certifications", response_model=dict)
async def list_all_certifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    standard_type: Optional[str] = Query(None),
    expired_only: bool = Query(False),
    expiring_days: Optional[int] = Query(None, ge=1, le=365),
    valid_only: bool = Query(False),
    q: Optional[str] = Query(None, description="Search by cert type, name, supplier code or group name"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """List all certifications across all supplier units for centralized tracking."""
    try:
        service = SupplierService(db)
        result = await service.list_all_certifications(
            skip=skip,
            limit=limit,
            standard_type=standard_type,
            expired_only=expired_only,
            expiring_days=expiring_days,
            valid_only=valid_only,
            q=q,
        )
        return {
            "status": "success",
            "data": {
                "items": result["items"],
                "total": result["total"],
                "skip": result["skip"],
                "limit": result["limit"],
            },
            "message": f"Found {result['total']} certification records",
        }
    except Exception:
        raise


# ============================================================================
# Legacy/Backward Compatible Endpoints
# ============================================================================


@router.get("", response_model=dict)
async def list_suppliers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Legacy endpoint: List all suppliers (redirects to groups)."""
    return await list_supplier_groups(
        skip=skip, limit=limit, db=db, current_user=current_user
    )


@router.get("/{supplier_id}", response_model=dict)
async def get_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Legacy endpoint: Get supplier by ID (redirects to group)."""
    return await get_supplier_group(
        group_id=supplier_id, db=db, current_user=current_user
    )


@router.put("/{supplier_id}", response_model=dict)
async def update_supplier(
    supplier_id: int,
    data: schemas.SupplierGroupUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Legacy endpoint: Update supplier (redirects to group)."""
    return await update_supplier_group(
        group_id=supplier_id, data=data, db=db, current_user=current_user
    )


@router.delete("/{supplier_id}", response_model=dict)
async def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Legacy endpoint: Delete supplier (redirects to group)."""
    return await delete_supplier_group(
        group_id=supplier_id, db=db, current_user=current_user
    )








