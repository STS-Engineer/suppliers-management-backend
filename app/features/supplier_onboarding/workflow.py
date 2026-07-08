"""Supplier onboarding workflow service."""

from typing import Dict, Any, Optional, List
from datetime import datetime
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models import (
    AvocarbonSite,
    Contact,
    SupplierCertification,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierSpendByYear,
    SupplierUnit,
)
from app.core.exceptions import AppException


# strategic = 3 months, global = 6 months, local = annually
_SCOPE_FREQUENCY: dict[str, str] = {
    "strategic": "Quarterly",
    "global": "Semi-Annual",
    "local": "Annual",
}


class SupplierOnboardingWorkflow:
    """Complete supplier onboarding workflow with classification, assignment, and prequalification."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_supplier_complete_workflow(
        self,
        group_data: Dict[str, Any],
        unit_data: Dict[str, Any],
        contacts: List[Dict[str, Any]],
        certifications: List[Dict[str, Any]],
        site_id: int,
        supplier_scope: str,  # global, strategic, local
        supplier_owner: str,
        template_id: Optional[int] = None,
        evaluation: Optional[Dict[str, Any]] = None,
        unit_contacts: Optional[List[Dict[str, Any]]] = None,
        annual_spend_value: Optional[Decimal] = None,
        fiscal_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Complete supplier onboarding workflow:
        1. Create supplier group + unit
        2. Create supplier-site relation
        3. Assign supplier owner and classification
        4. Send creation email to primary contact
        5. Launch prequalification with assessment template
        6. Send assessment email

        Args:
            group_data: Supplier group creation data
            unit_data: Supplier unit creation data
            contacts: List of contact dictionaries
            certifications: List of certification dictionaries
            site_id: Avocarbon site ID to link supplier to
            supplier_scope: Classification (global/strategic/local)
            supplier_owner: Name or email of the assigned owner
            template_id: Optional assessment template ID to use

        Returns:
            Complete workflow result with all created entities and email status
        """
        try:
            # Step 1: Verify site exists
            site = await self.db.get(AvocarbonSite, site_id)
            if not site:
                raise AppException(f"Site with ID {site_id} not found", status_code=404)

            # Step 2: Create supplier (group + unit + contacts + certs)
            supplier_result = await self._create_supplier_with_relations(
                group_data=group_data,
                unit_data=unit_data,
                contacts=contacts,
                certifications=certifications,
            )

            group = supplier_result["group"]
            unit = supplier_result["unit"]
            contact_list = supplier_result["contacts"]

            # Step 3: Create unit-level contacts if provided
            for uc_data in unit_contacts or []:
                uc_data["id_supplier_unit"] = unit.id_supplier_unit
                uc = Contact(**uc_data)
                self.db.add(uc)
            if unit_contacts:
                await self.db.flush()

            # Step 4: Create supplier-site relation with owner and classification
            relation = await self._create_supplier_site_relation(
                supplier_unit_id=unit.id_supplier_unit,
                site_id=site_id,
                supplier_scope=supplier_scope,
                buyer_owner=supplier_owner,
                certifications=certifications,
                evaluation=evaluation,
                annual_spend_value=annual_spend_value,
                fiscal_year=fiscal_year,
            )

            # Step 4b: Sync quality cert criteria detail from certs just created
            if certifications:
                from app.features.supplier_relations.service import (
                    SupplierRelationService,
                )

                rel_service = SupplierRelationService(self.db)
                await rel_service.sync_quality_certification_for_unit(
                    unit.id_supplier_unit,
                    triggered_by=supplier_owner,
                    change="create",
                )

            # Step 5: Get primary contact for emails
            primary_contact = self._get_primary_contact(contact_list)
            if not primary_contact:
                raise AppException(
                    "No primary contact found for supplier", status_code=400
                )

            # Step 5: Send creation notification email
            # creation_email_sent = await self.email_service.send_supplier_creation_email(
            #     supplier_name=group.nom,
            #     supplier_code=unit.supplier_name,
            #     contact_email=primary_contact.get("email", ""),
            #     contact_name=primary_contact.get("full_name", ""),
            #     supplier_scope=supplier_scope,
            #     group_id=group.id_group,
            # )

            # Step 6: Send supplier owner assignment email (if email provided)
            # owner_email_sent = False
            # if "@" in supplier_owner:  # Assume it's an email if contains @
            #     owner_email_sent = (
            #         await self.email_service.send_supplier_owner_assignment_email(
            #             supplier_name=group.nom,
            #             owner_email=supplier_owner,
            #             owner_name=supplier_owner.split("@")[0],
            #             site_name=site.site_name or "Unknown Site",
            #             supplier_code=unit.supplier_name,
            #         )
            #     )

            # # Step 7: Launch prequalification - create evaluation cycle and assessment
            # assessment_result = await self._launch_prequalification(
            #     relation=relation,
            #     supplier_group=group,
            #     supplier_unit=unit,
            #     template_id=template_id,
            # )

            # baseline_result = await self._create_initial_evaluation_baseline(
            #     relation=relation,
            #     cycle=assessment_result["cycle"],
            #     group_data=unit_data,
            #     evaluation=evaluation,
            #     certifications=certifications,
            # )

            # # Step 8: Send assessment template email
            # template_email_sent = False
            # if assessment_result["template"]:
            #     template_email_sent = (
            #         await self.email_service.send_assessment_template_email(
            #             supplier_name=group.nom,
            #             contact_email=primary_contact.get("email", ""),
            #             contact_name=primary_contact.get("full_name", ""),
            #             template_name=assessment_result["template"].template_name,
            #             deadline=(datetime.now() + timedelta(days=14)).strftime(
            #                 "%Y-%m-%d"
            #             ),
            #         )
            #     )

            # # Step 9: Send comprehensive prequalification launch email
            # prequalification_email_sent = (
            #     await self.email_service.send_prequalification_launch_email(
            #         supplier_name=group.nom,
            #         contact_email=primary_contact.get("email", ""),
            #         contact_name=primary_contact.get("full_name", ""),
            #         supplier_scope=supplier_scope,
            #         owner_name=supplier_owner,
            #     )
            # )

            # Commit all changes
            await self.db.commit()

            return {
                "status": "success",
                "supplier": {
                    "group_id": group.id_group,
                    "group_name": group.nom,
                    "unit_id": unit.id_supplier_unit,
                    "unit_code": unit.supplier_name,
                },
                "relation": {
                    "relation_id": relation.id_relation,
                    "site_id": relation.id_site,
                    "supplier_scope": supplier_scope,
                    "supplier_owner": supplier_owner,
                },
                "contacts": {
                    "primary_contact": {
                        "id": primary_contact.get("id_contact"),
                        "name": primary_contact.get("full_name"),
                        "email": primary_contact.get("email"),
                    },
                    "total_contacts": len(contact_list),
                },
                # "prequalification": {
                #     "cycle_id": assessment_result["cycle"].id_cycle
                #     if assessment_result["cycle"]
                #     else None,
                #     "assessment_id": assessment_result["assessment"].id_assessment
                #     if assessment_result["assessment"]
                #     else None,
                #     "template_id": assessment_result["template"].id_template
                #     if assessment_result["template"]
                #     else None,
                #     "score_card_id": baseline_result["score_card"].id_score_card
                #     if baseline_result["score_card"]
                #     else None,
                #     "classification_id": baseline_result[
                #         "classification"
                #     ].id_classification
                #     if baseline_result["classification"]
                #     else None,
                # },
                # "emails": {
                #     "creation_notification": creation_email_sent,
                #     "owner_assignment": owner_email_sent,
                #     "assessment_template": template_email_sent,
                #     "prequalification_launch": prequalification_email_sent,
                # },
                "message": f"Supplier {group.nom} created successfully and prequalification initiated",
            }

        except AppException:
            await self.db.rollback()
            raise
        except Exception as e:
            await self.db.rollback()
            raise AppException(
                f"Supplier onboarding workflow failed: {str(e)}", status_code=400
            )

    async def _create_supplier_with_relations(
        self,
        group_data: Dict[str, Any],
        unit_data: Dict[str, Any],
        contacts: List[Dict[str, Any]],
        certifications: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create supplier group, unit, contacts, and certifications."""
        group_name = str(group_data.get("nom") or "").strip()
        if group_name:
            existing_group_stmt = select(SupplierGroup).where(
                SupplierGroup.nom == group_name,
                SupplierGroup.is_deleted.is_(False),
            )
            existing_group = (
                await self.db.execute(existing_group_stmt)
            ).scalar_one_or_none()
            if existing_group:
                raise AppException(
                    f"Supplier group with name '{group_name}' already exists",
                    status_code=409,
                )

        supplier_name = str(unit_data.get("supplier_name") or "").strip()
        unit_group_id = unit_data.get("id_group")
        if supplier_name and unit_group_id is not None:
            existing_unit_stmt = select(SupplierUnit).where(
                SupplierUnit.id_group == unit_group_id,
                SupplierUnit.supplier_name == supplier_name,
                SupplierUnit.is_deleted.is_(False),
            )
            existing_unit = (
                await self.db.execute(existing_unit_stmt)
            ).scalar_one_or_none()
            if existing_unit:
                raise AppException(
                    f"Supplier unit with name '{supplier_name}' already exists in this group",
                    status_code=409,
                )

        prepared_group_data = self._prepare_group_payload(group_data)
        prepared_unit_data = self._prepare_unit_payload(unit_data, group_data)

        # Create group — always starts as pending until a purchasing manager approves
        group = SupplierGroup(**prepared_group_data)
        group.validation_status = "pending"
        self.db.add(group)
        await self.db.flush()

        # Create unit
        prepared_unit_data["id_group"] = group.id_group
        unit = SupplierUnit(**prepared_unit_data)
        self.db.add(unit)
        await self.db.flush()

        # Create contacts
        created_contacts = []
        for contact_dict in contacts:
            contact_dict["id_supplier_group"] = group.id_group
            contact = Contact(**contact_dict)
            self.db.add(contact)
            await self.db.flush()
            # Convert to dict for easy access
            created_contacts.append(
                {
                    "id_contact": contact.id_contact,
                    "full_name": contact.full_name,
                    "email": contact.email,
                    "is_primary_contact": contact.is_primary_contact,
                }
            )

        # Create certifications
        created_certs = []
        for cert_dict in certifications:
            cert_dict["id_supplier_unit"] = unit.id_supplier_unit
            cert = SupplierCertification(**cert_dict)
            self.db.add(cert)
            await self.db.flush()
            created_certs.append(cert)

        return {
            "group": group,
            "unit": unit,
            "contacts": created_contacts,
            "certifications": created_certs,
        }

    async def _create_supplier_site_relation(
        self,
        supplier_unit_id: int,
        site_id: int,
        supplier_scope: str,
        buyer_owner: str,
        certifications: Optional[List[Dict[str, Any]]] = None,
        evaluation: Optional[Dict[str, Any]] = None,
        annual_spend_value: Optional[Decimal] = None,
        fiscal_year: Optional[int] = None,
    ) -> SupplierSiteRelation:
        """Create supplier-site relation with owner and classification."""
        existing_stmt = select(SupplierSiteRelation).where(
            SupplierSiteRelation.id_supplier_unit == supplier_unit_id,
            SupplierSiteRelation.id_site == site_id,
        )
        existing_result = await self.db.execute(existing_stmt)
        existing_relation = existing_result.scalars().first()
        if existing_relation:
            raise AppException(
                f"Supplier unit {supplier_unit_id} is already linked to site {site_id}",
                status_code=409,
            )

        relation_data = {
            "id_supplier_unit": supplier_unit_id,
            "id_site": site_id,
            "buyer_owner": buyer_owner,
            "global_status": supplier_scope,
            "supplier_status": "Active",
            "evaluation_frequency": _SCOPE_FREQUENCY.get(
                (supplier_scope or "local").lower(), "Quarterly"
            ),
            "last_status_change": datetime.now(),
        }
        if annual_spend_value is not None:
            relation_data["annual_spend_value"] = annual_spend_value

        # Add evaluation data if provided (store as-is, no conversion)
        if evaluation:
            operational_grade = self._extract_operational_grade(evaluation)
            class_value = self._extract_class_value(evaluation)
            strategic_mention = self._extract_strategic_mention(
                evaluation,
                unit_defaults=None,
            )
            panel_decision = self._extract_panel_decision(evaluation)

            if operational_grade:
                relation_data["operational_grade"] = operational_grade
            if class_value is not None:
                relation_data["class_value"] = class_value
            if class_value is not None and operational_grade:
                relation_data["final_grade"] = self._compose_final_grade(
                    operational_grade,
                    class_value,
                )
            if strategic_mention:
                relation_data["strategic_mention"] = strategic_mention
            if panel_decision:
                relation_data["panel_decision"] = panel_decision

            # Store evaluation comments
            if "comments" in evaluation and evaluation["comments"]:
                relation_data["evaluation_comments"] = evaluation["comments"]
            if panel_decision:
                relation_data["evaluation_suggestion"] = panel_decision

        relation = SupplierSiteRelation(**relation_data)
        self.db.add(relation)
        await self.db.flush()

        # Create the initial spend-by-year entry when both value and year are supplied.
        # The legacy annual_spend_value on the relation is kept for backward compat
        # but SupplierSpendByYear is the authoritative source going forward.
        if annual_spend_value is not None and fiscal_year is not None:
            spend_entry = SupplierSpendByYear(
                id_relation=relation.id_relation,
                fiscal_year=fiscal_year,
                spend_value=annual_spend_value,
                spend_currency="EUR",
                created_by=buyer_owner,
            )
            self.db.add(spend_entry)
            await self.db.flush()

        return relation

    @staticmethod
    def _get_primary_contact(
        contacts: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Get primary contact from list, or first contact if none marked as primary."""
        for contact in contacts:
            if contact.get("is_primary_contact"):
                return contact
        return contacts[0] if contacts else None

    @staticmethod
    def _compose_final_grade(
        operational_grade: Optional[str],
        class_value: Optional[int],
    ) -> Optional[str]:
        if not operational_grade or class_value is None:
            return None
        return f"{operational_grade}{class_value}"

    @staticmethod
    def _extract_operational_grade(
        evaluation: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not evaluation:
            return None
        value = evaluation.get("operational_grade") or evaluation.get(
            "operational_class"
        )
        return str(value).upper() if value else None

    @staticmethod
    def _extract_class_value(evaluation: Optional[Dict[str, Any]]) -> Optional[int]:
        if not evaluation:
            return None
        value = evaluation.get("class_value")
        if value is None:
            # Backward compatibility with the previous onboarding payload shape.
            value = evaluation.get("impact")
        return int(value) if value not in (None, "") else None

    @staticmethod
    def _extract_panel_decision(evaluation: Optional[Dict[str, Any]]) -> Optional[str]:
        if not evaluation:
            return None
        panel_decision = evaluation.get("panel_decision")
        if panel_decision:
            return str(panel_decision).lower()

        legacy_suggestion = evaluation.get("suggestion")
        legacy_map = {
            "can_quote_and_award": "panel_add",
            "can_quote_not_award": "panel_add_exec_committee",
            "new_business_on_hold": "panel_reject",
        }
        return legacy_map.get(str(legacy_suggestion))

    @staticmethod
    def _extract_strategic_mention(
        evaluation: Optional[Dict[str, Any]],
        unit_defaults: Optional[Dict[str, Any]],
    ) -> str:
        if evaluation and evaluation.get("strategic_mention"):
            return str(evaluation["strategic_mention"]).lower()

        unit_defaults = unit_defaults or {}
        if unit_defaults.get("monopolistique"):
            return "monopolistic"
        if unit_defaults.get("strategique"):
            return "strategic"
        if unit_defaults.get("directed"):
            return "directed"
        return "none"

    @staticmethod
    def _prepare_group_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        prepared = dict(data)
        supplier_owner = prepared.pop("supplier_owner", None)
        if supplier_owner is not None:
            prepared["group_supplier_owner_email"] = supplier_owner
        prepared.pop("strategique", None)
        prepared.pop("monopolistique", None)
        prepared.pop("directed", None)
        return prepared

    @staticmethod
    def _prepare_unit_payload(
        unit_data: Dict[str, Any],
        group_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        prepared = dict(unit_data)
        group_data = group_data or {}
        for field_name in ("strategique", "monopolistique", "directed"):
            if (
                prepared.get(field_name) is None
                and group_data.get(field_name) is not None
            ):
                prepared[field_name] = bool(group_data.get(field_name))
        return prepared

