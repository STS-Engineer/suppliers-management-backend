"""Suppliers router."""

from typing import Optional
from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.dependencies.db import get_db
from app.shared.dependencies.auth import get_current_user
from app.features.suppliers.service import SupplierService
from app.features.suppliers import schemas
from app.features.supplier_onboarding.workflow import SupplierOnboardingWorkflow
from app.core.exceptions import AppException

from sqlalchemy.orm import Session

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


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

        return {
            "status": "success",
            "data": result,
        }
    except AppException:
        raise
    except Exception:
        raise


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
    try:
        service = SupplierService(db)
        success = await service.delete_supplier_group(group_id)
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
    from app.db.models import Contact, SupplierCertification, SupplierUnit
    from sqlalchemy import select

    # Verify group exists
    from app.db.models import SupplierGroup
    group = await db.get(SupplierGroup, group_id)
    if not group:
        raise AppException(f"Supplier group {group_id} not found", status_code=404)

    # Enforce unique supplier_code
    existing_unit_stmt = select(SupplierUnit).where(
        SupplierUnit.supplier_code == data.unit.supplier_code
    )
    existing_unit = (await db.execute(existing_unit_stmt)).scalar_one_or_none()
    if existing_unit:
        raise AppException(
            f"A supplier unit with name '{data.unit.supplier_code}' already exists. "
            "Each unit must have a unique name.",
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
    except Exception:
        await db.rollback()
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
    try:
        service = SupplierService(db)
        result = await service.create_complete_supplier(data=data)

        return {
            "status": "success",
            "data": {
                "group_id": result["group"].id_group,
                "group_code": result["group"].group_code,
                "group_name": result["group"].nom,
                "unit_id": result["unit"].id_supplier_unit,
                "unit_code": result["unit"].supplier_code,
                "unit_reference_code": result["unit"].unit_code,
                "contacts_count": len(result["contacts"]),
                "certifications_count": len(result["certifications"]),
            },
            "message": f"Supplier '{data.group.nom}' created successfully with unit '{data.unit.supplier_code}'",
            "details": {
                "group": schemas.SupplierGroupResponse.model_validate(result["group"]),
                "unit": schemas.SupplierUnitResponse.model_validate(result["unit"]),
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
    try:
        service = SupplierService(db)
        cert = await service.create_certification_for_unit(unit_id, data)
        return {
            "status": "success",
            "data": schemas.SupplierCertificationResponse.model_validate(cert),
            "message": f"Certification added to unit {unit_id}",
            "id": cert.id_certification,
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


@router.get("/certifications", response_model=dict)
async def list_all_certifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    standard_type: Optional[str] = Query(None, description="Filter by standard type (quality, environmental, safety, energy, other)"),
    expired_only: bool = Query(False, description="Return only expired certifications"),
    expiring_days: Optional[int] = Query(None, ge=1, le=365, description="Return certs expiring within N days"),
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
        )
        return {
            "status": "success",
            "data": {
                "items": [
                    schemas.SupplierCertificationResponse.model_validate(cert)
                    for cert in result["items"]
                ],
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





