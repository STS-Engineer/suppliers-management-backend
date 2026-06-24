"""Suppliers service layer."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.suppliers.models import (
    AvocarbonSite,
    Contact,
    SupplierCertification,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierStatusHistory,
    SupplierUnit,
)
from app.db.models import AuditEvent
from app.features.suppliers.repository import SupplierRepository
from app.db.models import (
    Classification,
    Document,
    EvaluationCycle,
    ImpactEvaluationInput,
    OperationalEvaluationInput,
    PldClassEvaluationInput,
    ScoreCard,
    SupplierCarbonFootprint,
)
from app.features.suppliers import schemas
from app.core.exceptions import AppException
from app.shared.utils.blob_storage import get_fresh_doc_url


class SupplierService:
    """Service for supplier operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SupplierRepository(db)
    
    # ========================================================================
    # SupplierGroup Operations
    # ========================================================================
    
    async def list_supplier_groups(self, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
        """List all supplier groups with pagination."""
        groups = await self.repo.find_all_groups(skip=skip, limit=limit)
        total = await self.repo.count_groups()
        return {
            "items": groups,
            "total": total,
            "skip": skip,
            "limit": limit
        }
    
    async def get_supplier_group(self, group_id: int) -> Optional[SupplierGroup]:
        """Get supplier group by ID."""
        try:
            group = await self.repo.find_group_with_documents(group_id)

        except ProgrammingError as exc:
            message = str(exc).lower()
            orig_name = getattr(getattr(exc, "orig", None), "__class__", type(None)).__name__
            if orig_name == "UndefinedTableError" and "document" in message:
                raise AppException(
                    "Supplier group details cannot be loaded because the database is missing the 'document' table. Apply the pending Alembic migrations with 'alembic upgrade head'.",
                    status_code=500,
                    error_code="DATABASE_SCHEMA_OUT_OF_DATE",
                ) from exc
            raise

        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)
        return group
    
    async def create_supplier_group(self, data: schemas.SupplierGroupCreate) -> SupplierGroup:
        """Create a new supplier group."""
        # Check if group with same name already exists
        existing = await self.repo.find_group_by_name(data.nom)
        if existing:
            raise AppException(f"Supplier group with name '{data.nom}' already exists", status_code=409)

        group_data = self._prepare_group_payload(data.model_dump(exclude_unset=True))
        categories = self._extract_categories(group_data.pop("supplier_type", None))

        group = await self.repo.create_group(group_data)

        await self.repo.replace_group_categories(
            group,
            categories,
        )

        await self.db.commit()

        group_for_response = await self.repo.find_group_for_response(group.id_group)
        return group_for_response
    
    async def update_supplier_group(self, group_id: int, data: schemas.SupplierGroupUpdate) -> SupplierGroup:
        """Update a supplier group."""
        group = await self.repo.find_group_by_id(group_id)
        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)
        
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return group
        
        # Check if new name conflicts with another group
        if "nom" in update_data and update_data["nom"] != group.nom:
            existing = await self.repo.find_group_by_name(update_data["nom"])
            if existing:
                raise AppException(f"Supplier group with name '{update_data['nom']}' already exists", status_code=409)

        prepared_update_data = self._prepare_group_payload(update_data)
        categories = None
        if "supplier_type" in prepared_update_data:
            categories = self._extract_categories(
                prepared_update_data.pop("supplier_type", None)
            )

        updated_group = await self.repo.update_group(group_id, prepared_update_data)
        if updated_group and categories is not None:
            await self.repo.replace_group_categories(updated_group, categories)
        await self.db.commit()
        return updated_group
    
    async def delete_supplier_group(self, group_id: int) -> bool:
        """Delete a supplier group."""
        group = await self.repo.find_group_by_id(group_id)
        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)
        
        success = await self.repo.delete_group(group_id)
        if success:
            await self.db.commit()
        return success
    
    # ========================================================================
    # SupplierUnit Operations
    # ========================================================================
    
    async def list_supplier_units(self, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
        """List all supplier units with pagination."""
        units = await self.repo.find_all_units(skip=skip, limit=limit)
        total = await self.repo.count_units()
        return {
            "items": units,
            "total": total,
            "skip": skip,
            "limit": limit
        }
    
    async def get_supplier_unit(self, unit_id: int) -> Optional[SupplierUnit]:
        """Get supplier unit by ID."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        return unit

    async def get_unit_evaluation_summary(self, unit_id: int) -> Dict[str, Any]:
        """Return the latest known evaluation snapshot for a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)

        relations_stmt = (
            select(SupplierSiteRelation)
            .where(SupplierSiteRelation.id_supplier_unit == unit_id)
            .order_by(
                SupplierSiteRelation.last_evaluation_date.desc().nullslast(),
                SupplierSiteRelation.id_relation.desc(),
            )
        )
        relations_result = await self.db.execute(relations_stmt)
        relations = relations_result.scalars().all()
        latest_relation = relations[0] if relations else None

        latest_classification = None
        if latest_relation:
            classification_stmt = (
                select(Classification)
                .where(Classification.id_relation == latest_relation.id_relation)
                .order_by(Classification.id_classification.desc())
            )
            classification_result = await self.db.execute(classification_stmt)
            latest_classification = classification_result.scalars().first()

        return {
            "unit_id": unit_id,
            "relation_id": latest_relation.id_relation if latest_relation else None,
            "class_value": (
                latest_classification.class_value
                if latest_classification and latest_classification.class_value is not None
                else latest_relation.class_value if latest_relation else None
            ),
            "class_score": (
                latest_classification.classification_score
                if latest_classification
                else None
            ),
            "operational_grade": (
                latest_classification.operational_grade
                if latest_classification and latest_classification.operational_grade
                else latest_relation.operational_grade if latest_relation else None
            ),
            "operational_score": (
                latest_classification.operational_score
                if latest_classification
                else None
            ),
            "final_grade": (
                latest_classification.final_grade
                if latest_classification and latest_classification.final_grade
                else latest_relation.final_grade if latest_relation else None
            ),
            "strategic_mention": (
                latest_classification.strategic_mention
                if latest_classification and latest_classification.strategic_mention
                else latest_relation.strategic_mention if latest_relation else None
            ),
            "panel_decision": (
                latest_classification.panel_decision
                if latest_classification and latest_classification.panel_decision
                else latest_relation.panel_decision if latest_relation else None
            ),
            "impact_score": latest_classification.impact_score if latest_classification else None,
            "last_evaluation_date": latest_relation.last_evaluation_date if latest_relation else None,
            "evaluation_comments": latest_relation.evaluation_comments if latest_relation else None,
            "site_relations_count": len(relations),
        }
    
    async def list_units_by_group(self, group_id: int) -> List[SupplierUnit]:
        """List all units for a specific supplier group."""
        group = await self.repo.find_group_by_id(group_id)
        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)
        return await self.repo.find_units_by_group(group_id)
    
    async def create_supplier_unit(
        self,
        data: schemas.SupplierUnitCreate,
        changed_by: Optional[str] = None,
    ) -> SupplierUnit:
        """Create a new supplier unit."""
        # Check if supplier code already exists in the same group
        existing = await self.repo.find_unit_by_code(data.supplier_code, data.id_group)
        if existing:
            raise AppException(f"Supplier unit with code '{data.supplier_code}' already exists in this group", status_code=409)
        
        # Check if group exists (if provided)
        if data.id_group:
            group = await self.repo.find_group_by_id(data.id_group)
            if not group:
                raise AppException(f"Supplier group with ID {data.id_group} not found", status_code=404)
        
        unit = await self.repo.create_unit(
            self._prepare_unit_payload(data.model_dump(exclude_unset=True))
        )
        await self._record_audit_event(
            table_name="supplier_unit",
            record_pk=unit.id_supplier_unit,
            action="CREATE",
            changed_by=changed_by,
            new_values=self._unit_audit_snapshot(unit),
        )
        await self.db.commit()
        return unit
    
    async def update_supplier_unit(
        self,
        unit_id: int,
        data: schemas.SupplierUnitUpdate,
        changed_by: Optional[str] = None,
    ) -> SupplierUnit:
        """Update a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        
        update_data = data.model_dump(exclude_unset=True)
        if not update_data:
            return unit
        
        # Check if new code conflicts with another unit in the same group
        if "supplier_code" in update_data and update_data["supplier_code"] != unit.supplier_code:
            existing = await self.repo.find_unit_by_code(update_data["supplier_code"], unit.id_group)
            if existing:
                raise AppException(f"Supplier unit with code '{update_data['supplier_code']}' already exists in this group", status_code=409)
        
        previous_snapshot = self._unit_audit_snapshot(unit)
        updated_unit = await self.repo.update_unit(
            unit_id, self._prepare_unit_payload(update_data)
        )
        await self._record_audit_event(
            table_name="supplier_unit",
            record_pk=unit_id,
            action="UPDATE",
            changed_by=changed_by,
            old_values=previous_snapshot,
            new_values=self._unit_audit_snapshot(updated_unit),
        )
        await self.db.commit()
        return updated_unit
    
    async def delete_supplier_unit(
        self,
        unit_id: int,
        changed_by: Optional[str] = None,
    ) -> bool:
        """Delete a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        
        previous_snapshot = self._unit_audit_snapshot(unit)
        success = await self.repo.delete_unit(unit_id)
        if success:
            await self._record_audit_event(
                table_name="supplier_unit",
                record_pk=unit_id,
                action="DELETE",
                changed_by=changed_by,
                old_values=previous_snapshot,
            )
            await self.db.commit()
        return success
    
    async def create_complete_supplier(
        self,
        group_data: dict = None,
        unit_data: dict = None,
        contacts: list = None,
        certifications: list = None,
        data: schemas.CreateSupplierRequest = None,
    ) -> Dict[str, Any]:
        """
        Create a complete supplier with group, unit, contacts, and certifications.
        This is a transaction that creates all related entities.
        
        Supports both dict-based and schema-based input for flexibility.
        """
        try:
            # Handle both input types
            if data is not None:
                group_data = data.group.model_dump(exclude_unset=True)
                unit_data = data.unit.model_dump(exclude_unset=True)
                contacts = [c.model_dump(exclude_unset=True) for c in data.contacts]
                certifications = [c.model_dump(exclude_unset=True) for c in data.certifications]
            
            # if not group_data or not unit_data:
            #     raise AppException("Group and unit data are required", status_code=400)
            
            contacts = contacts or []
            certifications = certifications or []

            group_name = str((group_data or {}).get("nom") or "").strip()
            if group_name:
                existing_group = await self.repo.find_group_by_name(group_name)
                if existing_group:
                    raise AppException(
                        f"Supplier group with name '{group_name}' already exists",
                        status_code=409,
                    )

            supplier_code = str((unit_data or {}).get("supplier_code") or "").strip()
            unit_group_id = (unit_data or {}).get("id_group")
            if supplier_code and unit_group_id is not None:
                existing_unit = await self.repo.find_unit_by_code(supplier_code, unit_group_id)
                if existing_unit:
                    raise AppException(
                        f"Supplier unit with code '{supplier_code}' already exists in this group",
                        status_code=409,
                    )

            group_data = self._prepare_group_payload(group_data or {})
            unit_data = self._prepare_unit_payload(unit_data or {}, group_data)
            unit_data = await self._apply_legacy_unit_schema_compatibility(unit_data)
            categories = self._extract_categories(group_data.pop("supplier_type", None))
            
            # Create supplier group
            group = await self.repo.create_group(group_data)
            await self.repo.replace_group_categories(group, categories)
            
            # Create supplier unit linked to the group
            unit_data["id_group"] = group.id_group
            unit = await self.repo.create_unit(unit_data)
            
            # Create contacts linked to the group
            created_contacts = []
            for contact_dict in contacts:
                contact_dict["id_supplier_group"] = group.id_group
                contact = await self.repo.create_contact(contact_dict)
                created_contacts.append(contact)
            
            # Create certifications linked to the unit
            created_certifications = []
            for cert_dict in certifications:
                cert_dict["id_supplier_unit"] = unit.id_supplier_unit
                cert = await self.repo.create_certification(cert_dict)
                created_certifications.append(cert)
            
            # Commit all changes
            await self.db.commit()
            group_for_response = await self.repo.find_group_for_response(group.id_group)
            return {
                "group": group_for_response,
                "unit": unit,
                "contacts": created_contacts,
                "certifications": created_certifications
            }
        except AppException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            raise AppException(f"Failed to create supplier: {str(e)}", status_code=400)
    
    # ========================================================================
    # Contact Operations
    # ========================================================================
    
    async def create_contact_for_group(self, group_id: int, data: schemas.ContactCreate) -> Contact:
        """Create a contact for a supplier group."""
        group = await self.repo.find_group_by_id(group_id)
        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)
        
        contact_data = data.model_dump(exclude_unset=True)
        contact_data["id_supplier_group"] = group_id
        contact = await self.repo.create_contact(contact_data)
        await self.db.commit()
        return contact
    
    async def create_contact_for_unit(self, unit_id: int, data: schemas.ContactCreate) -> Contact:
        """Create a contact for a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        
        contact_data = data.model_dump(exclude_unset=True)
        contact_data["id_supplier_unit"] = unit_id
        contact = await self.repo.create_contact(contact_data)
        await self.db.commit()
        return contact

    async def list_contacts_for_group(self, group_id: int) -> List[Contact]:
        """List contacts for a supplier group."""
        group = await self.repo.find_group_by_id(group_id)
        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)
        return await self.repo.find_contacts_by_group(group_id)

    async def list_contacts_for_unit(self, unit_id: int) -> List[Contact]:
        """List contacts for a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        return await self.repo.find_contacts_by_unit(unit_id)
    
    # ========================================================================
    # Certification Operations
    # ========================================================================
    
    async def create_certification_for_unit(self, unit_id: int, data: schemas.SupplierCertificationCreate) -> SupplierCertification:
        """Create a certification for a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        
        cert_data = data.model_dump(exclude_unset=True)
        cert_data["id_supplier_unit"] = unit_id
        cert = await self.repo.create_certification(cert_data)
        await self.db.commit()
        return cert

    async def patch_certification(
        self, unit_id: int, cert_id: int, data: schemas.SupplierCertificationUpdate
    ) -> SupplierCertification:
        cert = await self.repo.find_certification_by_id(cert_id)
        if not cert or cert.id_supplier_unit != unit_id:
            raise AppException(f"Certification {cert_id} not found for unit {unit_id}", status_code=404)
        update_data = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
        if not update_data:
            return cert
        cert = await self.repo.update_certification(cert_id, update_data)
        await self.db.commit()
        return cert

    async def list_certifications_for_unit(self, unit_id: int) -> List[SupplierCertification]:
        """List certifications for a supplier unit."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        return await self.repo.find_certifications_by_unit(unit_id)
    
    # ========================================================================
    # Supplier-Site Relation Operations
    # ========================================================================
    
    async def create_supplier_site_relation(
        self,
        unit_id: int,
        site_id: int,
        data: Optional[Dict[str, Any]] = None,
        changed_by: Optional[str] = None,
    ) -> "SupplierSiteRelation":
        """
        Create a link between a supplier unit and an Avocarbon site.
        This creates an enriched M2M relation with evaluation data.
        """
        # Verify unit exists
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)

        group = None
        if unit.id_group:
            group = await self.repo.find_group_by_id(unit.id_group)
        
        # Verify site exists
        site = await self.db.get(AvocarbonSite, site_id)
        if not site:
            raise AppException(f"Avocarbon site with ID {site_id} not found", status_code=404)
        
        # Check if relation already exists
        stmt = select(SupplierSiteRelation).where(
            SupplierSiteRelation.id_supplier_unit == unit_id,
            SupplierSiteRelation.id_site == site_id
        )
        result = await self.db.execute(stmt)
        existing = result.scalars().first()
        if existing:
            raise AppException(
                f"Unit {unit_id} is already linked to site {site_id}",
                status_code=409
            )
        
        # Create new relation as an assignment record only.
        # SBA qualification/evaluation is owned at unit level, not relation level.
        input_data = data or {}
        resolved_scope = (
            input_data.get("supplier_scope")
            or (group.supplier_scope if group and group.supplier_scope else None)
            or "local"
        )
        resolved_owner = input_data.get("supplier_owner")

        if resolved_scope == "global" and not resolved_owner and group and group.supplier_owner:
            resolved_owner = group.supplier_owner

        if not resolved_owner:
            if resolved_scope == "global":
                raise AppException(
                    "A global supplier owner email is required before creating this relation",
                    status_code=400,
                )
            raise AppException(
                "Supplier owner email is required for local or regional site assignments",
                status_code=400,
            )

        allowed_assignment_fields = {
            "evaluation_frequency",
            "supplier_status",
            "alias_1",
            "evaluation_comments",
            "evaluation_suggestion",
        }
        relation_data = {
            key: value
            for key, value in input_data.items()
            if key in allowed_assignment_fields
        }
        relation_data["global_status"] = resolved_scope
        relation_data["buyer_owner"] = resolved_owner
        relation_data["id_supplier_unit"] = unit_id
        relation_data["id_site"] = site_id
        
        relation = SupplierSiteRelation(**relation_data)
        self.db.add(relation)
        await self.db.flush()
        await self._record_audit_event(
            table_name="supplier_site_relation",
            record_pk=relation.id_relation,
            action="CREATE",
            changed_by=changed_by,
            new_values=self._relation_audit_snapshot(relation),
        )
        await self.db.commit()
        
        return relation
    
    async def list_unit_site_relations(self, unit_id: int) -> List["SupplierSiteRelation"]:
        """List all site relations for a specific supplier unit."""
        from app.db.models import SupplierSiteRelation
        from sqlalchemy import select
        
        # Verify unit exists
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        
        stmt = select(SupplierSiteRelation).where(
            SupplierSiteRelation.id_supplier_unit == unit_id
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def delete_supplier_site_relation(
        self,
        unit_id: int,
        site_id: int,
        changed_by: Optional[str] = None,
    ) -> bool:
        """Delete a supplier-site relation."""
        from app.db.models import SupplierSiteRelation
        from sqlalchemy import select
        
        # Verify unit and site exist
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)
        
        # Find the relation
        stmt = select(SupplierSiteRelation).where(
            SupplierSiteRelation.id_supplier_unit == unit_id,
            SupplierSiteRelation.id_site == site_id
        )
        result = await self.db.execute(stmt)
        relation = result.scalars().first()
        
        if not relation:
            raise AppException(
                f"No relation found between unit {unit_id} and site {site_id}",
                status_code=404
            )
        
        previous_snapshot = self._relation_audit_snapshot(relation)
        await self.db.execute(
            update(Document)
            .where(Document.id_relation == relation.id_relation)
            .values(id_relation=None)
        )
        # Delete the relation
        await self.db.delete(relation)
        await self._record_audit_event(
            table_name="supplier_site_relation",
            record_pk=relation.id_relation,
            action="DELETE",
            changed_by=changed_by,
            old_values=previous_snapshot,
        )
        await self.db.commit()
        return True

    async def create_initial_unit_evaluation(
        self,
        unit_id: int,
        data: schemas.InitialUnitEvaluationRequest,
        changed_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create the initial SBA evaluation for a unit using its first relation context."""
        unit = await self.repo.find_unit_by_id(unit_id)
        if not unit:
            raise AppException(f"Supplier unit with ID {unit_id} not found", status_code=404)

        relation_stmt = (
            select(SupplierSiteRelation)
            .where(SupplierSiteRelation.id_supplier_unit == unit_id)
            .order_by(SupplierSiteRelation.id_relation.asc())
        )
        relation_result = await self.db.execute(relation_stmt)
        relation = relation_result.scalars().first()
        if not relation:
            raise AppException(
                "Assign this unit to at least one Avocarbon site before creating its initial evaluation",
                status_code=400,
            )

        certification_stmt = (
            select(SupplierCertification)
            .where(SupplierCertification.id_supplier_unit == unit_id)
            .order_by(SupplierCertification.id_certification.asc())
        )
        certification_result = await self.db.execute(certification_stmt)
        certifications = certification_result.scalars().all()
        certification_type = (
            certifications[0].certification_type if certifications else None
        )
        quality_certification = data.quality_certification or certification_type

        operational_grade = self._extract_operational_grade(data)
        class_value = self._extract_class_value(data)
        class_score = self._to_decimal(data.class_score)
        operational_score = self._to_decimal(data.operational_score)
        impact_score = data.impact_score
        strategic_mention = self._extract_strategic_mention(data)
        panel_decision = self._extract_panel_decision(data)
        final_grade = self._compose_final_grade(operational_grade, class_value)
        changed_by = data.changed_by or "SYSTEM"
        now = datetime.now()

        cycle = EvaluationCycle(
            id_relation=relation.id_relation,
            cycle_type="Initial Supplier Evaluation",
            supplier_type="Unit Baseline",
            frequency="Ad hoc",
            period_start=now.date(),
            period_end=now.date(),
            due_date=now.date(),
            cycle_status="Completed",
            launched_by=changed_by,
            launched_at=now,
            completed_at=now,
            comments=data.comments or "Initial unit evaluation recorded from supplier management.",
        )
        self.db.add(cycle)
        await self.db.flush()

        class_input = PldClassEvaluationInput(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            top=data.top,
            lta=data.lta,
            productivity=data.prod,
            quality_certification=quality_certification,
            prod_lia_ins=data.prod_lia_ins,
            competitiveness=data.competitiveness,
            sqma=data.sqma,
            family_coverage=data.family_coverage,
            geo_coverage=data.geo_coverage,
            cons_or_wd=data.cons_or_wd,
            financial_health=data.financial_health,
            class_score=class_score,
            class_value=class_value,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=data.comments,
            entered_by=changed_by,
        )
        self.db.add(class_input)

        operational_input = OperationalEvaluationInput(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            source_type="self_assessment",
            management_system=self._to_decimal(data.management_system),
            customer_communication=self._to_decimal(data.customer_communication),
            development_design=self._to_decimal(data.development_design),
            production_manufacturing=self._to_decimal(data.production_manufacturing),
            quality_audits=self._to_decimal(data.quality_audits),
            suppliers_subcontractors=self._to_decimal(data.suppliers_subcontractors),
            deliveries=self._to_decimal(data.deliveries),
            environment_ethic_rules=self._to_decimal(data.environment_ethic_rules),
            average_score=operational_score,
            operational_grade=operational_grade,
            comments=data.comments,
            entered_by=changed_by,
        )
        self.db.add(operational_input)

        impact_input = ImpactEvaluationInput(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            question_1=data.impact_question_1,
            question_2=data.impact_question_2,
            question_3=data.impact_question_3,
            question_4=data.impact_question_4,
            question_5=data.impact_question_5,
            question_6=data.impact_question_6,
            impact_score=impact_score,
            comments=data.comments,
            entered_by=changed_by,
        )
        self.db.add(impact_input)

        score_card = ScoreCard(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            scorecard_date=now.date(),
            score=operational_score,
            grade=operational_grade,
            comments=data.comments or "Initial operational self-assessment baseline.",
            entered_by=changed_by,
        )
        self.db.add(score_card)
        await self.db.flush()

        classification = Classification(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            classification_date=now.date(),
            classification_score=class_score,
            class_value=class_value,
            operational_score=operational_score,
            operational_grade=operational_grade,
            final_grade=final_grade,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=data.comments or "Initial supplier evaluation baseline saved.",
            entered_by=changed_by,
        )
        self.db.add(classification)
        await self.db.flush()

        history = None
        relation_snapshot = self._relation_audit_snapshot(relation)
        if (
            relation.class_value != class_value
            or relation.operational_grade != operational_grade
            or relation.final_grade != final_grade
            or relation.strategic_mention != strategic_mention
            or relation.panel_decision != panel_decision
        ):
            history = SupplierStatusHistory(
                id_relation=relation.id_relation,
                old_status=relation.supplier_status,
                new_status=relation.supplier_status,
                old_class=relation.class_value,
                new_class=class_value,
                old_grade=relation.operational_grade,
                new_grade=operational_grade,
                old_final_grade=relation.final_grade,
                new_final_grade=final_grade,
                old_strategic_mention=relation.strategic_mention,
                new_strategic_mention=strategic_mention,
                old_panel_decision=relation.panel_decision,
                new_panel_decision=panel_decision,
                change_reason=data.comments or "Initial unit evaluation saved.",
                changed_by=changed_by,
                changed_at=now,
            )
            self.db.add(history)

        relation.operational_grade = operational_grade
        relation.class_value = class_value
        relation.final_grade = final_grade
        relation.strategic_mention = strategic_mention
        relation.panel_decision = panel_decision
        relation.evaluation_suggestion = panel_decision or relation.evaluation_suggestion
        relation.last_evaluation_date = now.date()
        if data.comments:
            relation.evaluation_comments = data.comments

        await self._record_audit_event(
            table_name="supplier_site_relation",
            record_pk=relation.id_relation,
            action="EVALUATE",
            changed_by=changed_by or data.changed_by,
            old_values=relation_snapshot,
            new_values=self._relation_audit_snapshot(relation),
            reason_comment=data.comments,
        )

        await self.db.commit()
        await self.db.refresh(relation)

        return {
            "relation": relation,
            "cycle": cycle,
            "score_card": score_card,
            "classification": classification,
            "status_history": history,
        }

    async def get_group_audit_trail(
        self,
        group_id: int,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        group = await self.repo.find_group_by_id(group_id)
        if not group:
            raise AppException(f"Supplier group with ID {group_id} not found", status_code=404)

        unit_ids = [unit.id_supplier_unit for unit in group.units]
        relation_ids: list[int] = []
        if unit_ids:
            relation_stmt = select(SupplierSiteRelation.id_relation).where(
                SupplierSiteRelation.id_supplier_unit.in_(unit_ids)
            )
            relation_result = await self.db.execute(relation_stmt)
            relation_ids = [row[0] for row in relation_result.all()]

        record_ids = {str(group_id), *(str(unit_id) for unit_id in unit_ids), *(str(relation_id) for relation_id in relation_ids)}
        if not record_ids:
            return []

        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.table_name.in_(
                    ["supplier_group", "supplier_unit", "supplier_site_relation", "pld_class_criteria_detail", "document"]
                ),
                AuditEvent.record_pk.in_(record_ids),
            )
            .order_by(AuditEvent.changed_at.desc(), AuditEvent.id_audit_event.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._audit_event_to_dict(event) for event in result.scalars().all()]

    @staticmethod
    def _prepare_group_payload(data: dict) -> dict:
        prepared = dict(data)
        supplier_owner = prepared.pop("supplier_owner", None)
        if supplier_owner is not None:
            prepared["group_supplier_owner_email"] = supplier_owner
        prepared.pop("strategique", None)
        prepared.pop("monopolistique", None)
        prepared.pop("directed", None)
        return prepared

    @staticmethod
    def _prepare_unit_payload(unit_data: dict, group_data: Optional[dict] = None) -> dict:
        prepared = dict(unit_data)
        group_data = group_data or {}
        for field_name in ("strategique", "monopolistique", "directed"):
            if prepared.get(field_name) is None and group_data.get(field_name) is not None:
                prepared[field_name] = bool(group_data.get(field_name))
        return prepared

    async def _get_column_max_length(
        self, table_name: str, column_name: str
    ) -> Optional[int]:
        result = await self.db.execute(
            text(
                """
                SELECT character_maximum_length
                FROM information_schema.columns
                WHERE table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    async def _apply_legacy_unit_schema_compatibility(self, unit_data: dict) -> dict:
        prepared = dict(unit_data)
        category = prepared.get("category")
        if not category:
            return prepared

        max_length = await self._get_column_max_length("supplier_unit", "category")
        if max_length is not None and len(str(category)) > max_length:
            # Some local databases still expose the legacy short category column.
            # Preserve the submitted value in the older product_category field and
            # omit category so supplier creation can still succeed.
            prepared.setdefault("product_category", str(category)[:255])
            prepared.pop("category", None)

        return prepared

    @staticmethod
    def _extract_categories(raw_value: Any) -> list[tuple[str, str]]:
        if raw_value in (None, ""):
            return []

        if isinstance(raw_value, str):
            raw_items = [item.strip() for item in raw_value.split(",")]
        elif isinstance(raw_value, list):
            raw_items = [str(item).strip() for item in raw_value]
        else:
            raw_items = [str(raw_value).strip()]

        categories: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in raw_items:
            if not item:
                continue
            category_key = item.lower().replace("&", "and")
            category_key = "".join(
                character if character.isalnum() else "_"
                for character in category_key
            ).strip("_")
            if not category_key or category_key in seen:
                continue
            seen.add(category_key)
            categories.append((category_key, item))
        return categories

    @staticmethod
    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @staticmethod
    def _extract_class_value(data: Any) -> Optional[int]:
        if getattr(data, "class_value", None) is not None:
            return data.class_value
        if getattr(data, "impact", None) is not None:
            return data.impact
        return None

    @staticmethod
    def _extract_operational_grade(data: Any) -> Optional[str]:
        if getattr(data, "operational_grade", None):
            return data.operational_grade.upper()
        if getattr(data, "operational_class", None):
            return data.operational_class.upper()
        return None

    @staticmethod
    def _extract_strategic_mention(data: Any) -> Optional[str]:
        value = getattr(data, "strategic_mention", None)
        return value.lower() if isinstance(value, str) else value

    @staticmethod
    def _extract_panel_decision(data: Any) -> Optional[str]:
        if getattr(data, "panel_decision", None):
            return data.panel_decision.lower()
        suggestion = getattr(data, "suggestion", None)
        mapping = {
            "can_quote_and_award": "panel_add",
            "needs_executive_committee": "panel_add_exec_committee",
            "cannot_be_added": "panel_reject",
        }
        return mapping.get(suggestion)

    @staticmethod
    def _compose_final_grade(
        operational_grade: Optional[str],
        class_value: Optional[int],
    ) -> Optional[str]:
        if not operational_grade or class_value is None:
            return None
        return f"{operational_grade}{class_value}"

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
            try:
                return value.isoformat()
            except Exception:
                return str(value)
        if isinstance(value, dict):
            return {key: SupplierService._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [SupplierService._json_safe(item) for item in value]
        return value

    def _unit_audit_snapshot(self, unit: SupplierUnit) -> Dict[str, Any]:
        return {
            "id_supplier_unit": unit.id_supplier_unit,
            "id_group": unit.id_group,
            "supplier_code": unit.supplier_code,
            "address_line": unit.address_line,
            "city": unit.city,
            "country": unit.country,
            "product_type": unit.product_type,
            "product_category": unit.product_category,
            "amount_value": self._json_safe(unit.amount_value),
            "amount_currency": unit.amount_currency,
            "strategique": unit.strategique,
            "monopolistique": unit.monopolistique,
            "directed": unit.directed,
        }

    def _relation_audit_snapshot(self, relation: SupplierSiteRelation) -> Dict[str, Any]:
        return {
            "id_relation": relation.id_relation,
            "id_site": relation.id_site,
            "id_supplier_unit": relation.id_supplier_unit,
            "supplier_scope": relation.global_status,
            "supplier_owner": relation.buyer_owner,
            "operational_grade": relation.operational_grade,
            "class_value": relation.class_value,
            "evaluation_frequency": relation.evaluation_frequency,
            "final_grade": relation.final_grade,
            "strategic_mention": relation.strategic_mention,
            "panel_decision": relation.panel_decision,
            "supplier_status": relation.supplier_status,
            "alias_1": relation.alias_1,
            "global_status": relation.global_status,
            "last_evaluation_date": self._json_safe(relation.last_evaluation_date),
            "next_evaluation_date": self._json_safe(relation.next_evaluation_date),
            "evaluation_comments": relation.evaluation_comments,
            "evaluation_suggestion": relation.evaluation_suggestion,
        }

    # ========================================================================
    # Carbon Footprint Operations (SB8)
    # ========================================================================

    async def list_carbon_footprints(
        self,
        skip: int = 0,
        limit: int = 100,
        unit_id: Optional[int] = None,
        relation_id: Optional[int] = None,
        year: Optional[int] = None,
        continent: Optional[str] = None,
        origin: Optional[str] = None,
        site_location: Optional[str] = None,
        supplier_unit_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List carbon footprint records with optional filters."""
        from app.db.models import SupplierUnit as _SupplierUnit
        stmt = (
            select(SupplierCarbonFootprint)
            .options(selectinload(SupplierCarbonFootprint.supplier_unit))
            .where(SupplierCarbonFootprint.is_deleted.is_(False))
        )
        if unit_id is not None:
            stmt = stmt.where(SupplierCarbonFootprint.id_supplier_unit == unit_id)
        if relation_id is not None:
            stmt = stmt.where(SupplierCarbonFootprint.id_relation == relation_id)
        if year is not None:
            stmt = stmt.where(SupplierCarbonFootprint.year == year)
        if continent:
            stmt = stmt.where(SupplierCarbonFootprint.supplier_continent.ilike(f"%{continent}%"))
        if origin:
            stmt = stmt.where(SupplierCarbonFootprint.supplier_origin.ilike(f"%{origin}%"))
        if site_location:
            stmt = stmt.where(SupplierCarbonFootprint.site_location.ilike(f"%{site_location}%"))
        if supplier_unit_code:
            stmt = stmt.join(_SupplierUnit, SupplierCarbonFootprint.id_supplier_unit == _SupplierUnit.id_supplier_unit).where(
                _SupplierUnit.supplier_code.ilike(f"%{supplier_unit_code}%")
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        all_count_result = await self.db.execute(
            select(func.count()).select_from(
                select(SupplierCarbonFootprint.id_carbon_footprint).subquery()
            )
        )
        total_all = all_count_result.scalar() or 0

        stmt = stmt.order_by(
            SupplierCarbonFootprint.year.desc().nullslast(),
            SupplierCarbonFootprint.id_carbon_footprint,
        ).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = result.scalars().all()
        return {"items": items, "total": total, "total_all": total_all, "skip": skip, "limit": limit}

    async def update_carbon_footprint(self, fp_id: int, data: dict) -> Optional[SupplierCarbonFootprint]:
        from datetime import datetime as _dt
        stmt = (
            select(SupplierCarbonFootprint)
            .options(selectinload(SupplierCarbonFootprint.supplier_unit))
            .where(
                SupplierCarbonFootprint.id_carbon_footprint == fp_id,
                SupplierCarbonFootprint.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        fp = result.scalar_one_or_none()
        if not fp:
            return None
        for key, value in data.items():
            setattr(fp, key, value)
        fp.updated_at = _dt.utcnow()
        await self.db.commit()
        await self.db.refresh(fp)
        return fp

    async def create_carbon_footprint(self, data: dict) -> SupplierCarbonFootprint:
        fp = SupplierCarbonFootprint(**data)
        self.db.add(fp)
        await self.db.commit()
        await self.db.refresh(fp)
        return fp

    async def list_all_certifications(
        self,
        skip: int = 0,
        limit: int = 100,
        standard_type: Optional[str] = None,
        expired_only: bool = False,
        expiring_days: Optional[int] = None,
        q: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List all certifications across all supplier units with optional filters."""
        from datetime import date, timedelta
        from app.db.models import SupplierUnit as SupplierUnitModel, SupplierGroup as SupplierGroupModel
        base = (
            select(SupplierCertification, SupplierUnitModel.supplier_code, SupplierGroupModel.nom)
            .join(
                SupplierUnitModel,
                SupplierCertification.id_supplier_unit == SupplierUnitModel.id_supplier_unit,
                isouter=True,
            )
            .join(
                SupplierGroupModel,
                SupplierUnitModel.id_group == SupplierGroupModel.id_group,
                isouter=True,
            )
            .where(SupplierCertification.is_deleted.is_(False))
        )
        if standard_type:
            base = base.where(SupplierCertification.standard_type == standard_type)
        if expired_only:
            today = date.today()
            base = base.where(SupplierCertification.end_date < today)
        elif expiring_days is not None:
            today = date.today()
            threshold = today + timedelta(days=expiring_days)
            base = base.where(
                SupplierCertification.end_date >= today,
                SupplierCertification.end_date <= threshold,
            )
        if q:
            pattern = f"%{q}%"
            base = base.where(
                or_(
                    SupplierCertification.certification_type.ilike(pattern),
                    SupplierCertification.certificate_name.ilike(pattern),
                    SupplierUnitModel.supplier_code.ilike(pattern),
                    SupplierGroupModel.nom.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar() or 0

        rows = (
            await self.db.execute(
                base.order_by(
                    SupplierGroupModel.nom.asc().nullslast(),
                    SupplierUnitModel.supplier_code.asc().nullslast(),
                    SupplierCertification.end_date.asc().nullslast(),
                ).offset(skip).limit(limit)
            )
        ).all()

        items = []
        for cert, supplier_code, group_nom in rows:
            d = schemas.SupplierCertificationResponse.model_validate(cert).model_dump()
            d["file_url"] = (
                get_fresh_doc_url(cert.file_url)
                if cert.file_url
                else None
            )
            d["supplier_code"] = supplier_code
            d["group_nom"] = group_nom
            items.append(d)
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    def _audit_event_to_dict(self, event: AuditEvent) -> Dict[str, Any]:
        return {
            "id_audit_event": event.id_audit_event,
            "event_uuid": event.event_uuid,
            "table_name": event.table_name,
            "record_pk": event.record_pk,
            "action": event.action,
            "changed_by": event.changed_by,
            "changed_at": self._json_safe(event.changed_at),
            "old_values": self._json_safe(event.old_values),
            "new_values": self._json_safe(event.new_values),
            "reason_code": event.reason_code,
            "reason_comment": event.reason_comment,
            "source_system": event.source_system,
            "source_ip": event.source_ip,
            "correlation_id": event.correlation_id,
            "batch_id": event.batch_id,
            "is_system_event": event.is_system_event,
        }

    async def _record_audit_event(
        self,
        table_name: str,
        record_pk: int | str,
        action: str,
        changed_by: Optional[str] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        reason_code: Optional[str] = None,
        reason_comment: Optional[str] = None,
        source_system: Optional[str] = None,
        source_ip: Optional[str] = None,
        correlation_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        is_system_event: bool = False,
    ) -> AuditEvent:
        event = AuditEvent(
            table_name=table_name,
            record_pk=str(record_pk),
            action=action,
            changed_by=changed_by,
            old_values=self._json_safe(old_values),
            new_values=self._json_safe(new_values),
            reason_code=reason_code,
            reason_comment=reason_comment,
            source_system=source_system or "supplier-management-backend",
            source_ip=source_ip,
            correlation_id=correlation_id,
            batch_id=batch_id,
            is_system_event=is_system_event,
        )
        self.db.add(event)
        await self.db.flush()
        return event



