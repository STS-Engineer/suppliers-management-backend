"""Supplier onboarding workflow service."""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select


from app.db.models import (
    AssessmentTemplate,
    SupplierAssessment,
    AvocarbonSite,
    Contact,
    SupplierCategory,
    SupplierCertification,
    SupplierGroup,
    SupplierGroupCategory,
    SupplierSiteRelation,
    SupplierUnit,
    ScoreCard,
    Classification,
    SupplierStatusHistory,
    PldClassEvaluationInput,
    OperationalEvaluationInput,
    ImpactEvaluationInput,
    EvaluationCycle,
)
from app.core.exceptions import AppException


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
        annual_spend_currency: Optional[str] = None,
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
            for uc_data in (unit_contacts or []):
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
                annual_spend_currency=annual_spend_currency,
            )

            # Step 4: Get primary contact for emails
            primary_contact = self._get_primary_contact(contact_list)
            if not primary_contact:
                raise AppException(
                    "No primary contact found for supplier", status_code=400
                )

            # Step 5: Send creation notification email
            # creation_email_sent = await self.email_service.send_supplier_creation_email(
            #     supplier_name=group.nom,
            #     supplier_code=unit.supplier_code,
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
            #             supplier_code=unit.supplier_code,
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
                    "unit_code": unit.supplier_code,
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
        prepared_group_data = self._prepare_group_payload(group_data)
        prepared_unit_data = self._prepare_unit_payload(unit_data, group_data)
        categories = self._extract_categories(group_data.get("supplier_type"))

        # Create group
        group = SupplierGroup(**prepared_group_data)
        self.db.add(group)
        await self.db.flush()
        await self._replace_group_categories(group.id_group, categories)

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
        annual_spend_currency: Optional[str] = None,
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
            "evaluation_frequency": "Quarterly"
            if supplier_scope == "strategic"
            else "Annual",
            "last_status_change": datetime.now(),
        }
        if annual_spend_value is not None:
            relation_data["annual_spend_value"] = annual_spend_value
        if annual_spend_currency:
            relation_data["annual_spend_currency"] = annual_spend_currency

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
        return relation

    async def _launch_prequalification(
        self,
        relation: SupplierSiteRelation,
        supplier_group: SupplierGroup,
        supplier_unit: SupplierUnit,
        template_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Launch prequalification:
        1. Create evaluation cycle
        2. Create supplier assessment from template
        """
        try:
            # Create evaluation cycle
            cycle = EvaluationCycle(
                id_relation=relation.id_relation,
                cycle_type="Prequalification",
                supplier_type=supplier_group.supplier_type or "Standard",
                frequency="One-time",
                period_start=datetime.now().date(),
                period_end=(datetime.now() + timedelta(days=30)).date(),
                due_date=(datetime.now() + timedelta(days=14)).date(),
                cycle_status="Active",
                launched_by="SYSTEM",
                launched_at=datetime.now(),
                comments=f"Prequalification cycle for {supplier_group.nom}",
            )
            self.db.add(cycle)
            await self.db.flush()

            # Fetch assessment template
            template = None
            assessment = None

            if template_id:
                template = await self.db.get(AssessmentTemplate, template_id)
            # else:
            #     Get default prequalification template
            #     stmt = select(AssessmentTemplate).where(
            #         AssessmentTemplate.template_type == "SELF_ASSESSMENT"
            #     ).where(
            #         AssessmentTemplate.status == "Active"
            #     )
            #     result = await self.db.execute(stmt)
            #     template = result.scalars().first()

            # Create supplier assessment
            if template:
                assessment = SupplierAssessment(
                    id_relation=relation.id_relation,
                    id_template=template.id_template,
                    id_cycle=cycle.id_cycle,
                    assessment_date=datetime.now().date(),
                    status="Pending",
                    comments=f"Prequalification assessment for {supplier_group.nom}",
                )
                self.db.add(assessment)
                await self.db.flush()

            return {
                "cycle": cycle,
                "assessment": assessment,
                "template": template,
            }

        except Exception as e:
            raise AppException(
                f"Failed to launch prequalification: {str(e)}", status_code=400
            )

    @staticmethod
    def _get_primary_contact(
        contacts: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Get primary contact from list, or first contact if none marked as primary."""
        for contact in contacts:
            if contact.get("is_primary_contact"):
                return contact
        return contacts[0] if contacts else None

    async def _create_initial_evaluation_baseline(
        self,
        relation: SupplierSiteRelation,
        cycle: Optional[EvaluationCycle],
        group_data: Dict[str, Any],
        evaluation: Optional[Dict[str, Any]],
        certifications: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create the initial theoretical evaluation baseline for onboarding."""
        if not cycle or not evaluation:
            return {
                "score_card": None,
                "classification": None,
                "class_input": None,
                "operational_input": None,
                "impact_input": None,
                "status_history": None,
            }

        certification_type = (
            certifications[0].get("certification_type") if certifications else None
        )
        quality_certification = (
            evaluation.get("quality_certification") or certification_type
        )
        strategic_mention = self._extract_strategic_mention(
            evaluation,
            unit_defaults=group_data,
        )
        panel_decision = self._extract_panel_decision(evaluation)
        class_value = self._extract_class_value(evaluation)
        operational_grade = self._extract_operational_grade(evaluation)
        class_score = self._to_decimal(evaluation.get("class_score"))
        operational_score = self._to_decimal(evaluation.get("operational_score"))
        impact_score = self._extract_impact_score(evaluation)
        final_grade = self._compose_final_grade(operational_grade, class_value)

        class_input = PldClassEvaluationInput(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            top=evaluation.get("top"),
            lta=evaluation.get("lta"),
            productivity=evaluation.get("prod"),
            quality_certification=quality_certification,
            prod_lia_ins=evaluation.get("prod_lia_ins"),
            competitiveness=evaluation.get("competitiveness"),
            sqma=evaluation.get("sqma"),
            family_coverage=evaluation.get("family_coverage"),
            geo_coverage=evaluation.get("geo_coverage"),
            cons_or_wd=evaluation.get("cons_or_wd"),
            financial_health=evaluation.get("financial_health"),
            class_score=class_score,
            class_value=class_value,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=evaluation.get("comments"),
            entered_by="SYSTEM",
        )
        self.db.add(class_input)

        operational_input = OperationalEvaluationInput(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            source_type="self_assessment",
            management_system=self._to_decimal(evaluation.get("management_system")),
            customer_communication=self._to_decimal(
                evaluation.get("customer_communication")
            ),
            development_design=self._to_decimal(evaluation.get("development_design")),
            production_manufacturing=self._to_decimal(
                evaluation.get("production_manufacturing")
            ),
            quality_audits=self._to_decimal(evaluation.get("quality_audits")),
            suppliers_subcontractors=self._to_decimal(
                evaluation.get("suppliers_subcontractors")
            ),
            deliveries=self._to_decimal(evaluation.get("deliveries")),
            environment_ethic_rules=self._to_decimal(
                evaluation.get("environment_ethic_rules")
            ),
            average_score=operational_score,
            operational_grade=operational_grade,
            comments=evaluation.get("comments"),
            entered_by="SYSTEM",
        )
        self.db.add(operational_input)

        impact_input = ImpactEvaluationInput(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            question_1=evaluation.get("impact_question_1"),
            question_2=evaluation.get("impact_question_2"),
            question_3=evaluation.get("impact_question_3"),
            question_4=evaluation.get("impact_question_4"),
            question_5=evaluation.get("impact_question_5"),
            question_6=evaluation.get("impact_question_6"),
            impact_score=impact_score,
            comments=evaluation.get("comments"),
            entered_by="SYSTEM",
        )
        self.db.add(impact_input)

        score_card = ScoreCard(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            scorecard_date=datetime.now().date(),
            score=operational_score,
            grade=operational_grade,
            comments="Initial theoretical operational evaluation from onboarding self-assessment.",
            entered_by="SYSTEM",
        )
        self.db.add(score_card)
        await self.db.flush()

        classification = Classification(
            id_relation=relation.id_relation,
            id_cycle=cycle.id_cycle,
            classification_date=datetime.now().date(),
            classification_score=class_score,
            class_value=class_value,
            operational_score=operational_score,
            operational_grade=operational_grade,
            final_grade=final_grade,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments="Initial onboarding class evaluation baseline.",
            entered_by="SYSTEM",
        )
        self.db.add(classification)

        status_history = SupplierStatusHistory(
            id_relation=relation.id_relation,
            old_status=None,
            new_status=relation.supplier_status,
            old_class=None,
            new_class=class_value,
            old_grade=None,
            new_grade=operational_grade,
            old_final_grade=None,
            new_final_grade=final_grade,
            old_strategic_mention=None,
            new_strategic_mention=strategic_mention,
            old_panel_decision=None,
            new_panel_decision=panel_decision,
            change_reason="Initial onboarding theoretical baseline created.",
            changed_by="SYSTEM",
        )
        self.db.add(status_history)

        relation.class_value = class_value
        relation.operational_grade = operational_grade
        relation.final_grade = final_grade
        relation.strategic_mention = strategic_mention
        relation.panel_decision = panel_decision
        relation.evaluation_suggestion = (
            panel_decision or relation.evaluation_suggestion
        )
        relation.last_evaluation_date = datetime.now().date()

        await self.db.flush()

        return {
            "score_card": score_card,
            "classification": classification,
            "class_input": class_input,
            "operational_input": operational_input,
            "impact_input": impact_input,
            "status_history": status_history,
        }

    @staticmethod
    def _to_decimal(value: Any) -> Optional[Decimal]:
        if value in (None, ""):
            return None
        return Decimal(str(value))

    @staticmethod
    def _compose_final_grade(
        operational_grade: Optional[str],
        class_value: Optional[int],
    ) -> Optional[str]:
        if not operational_grade or class_value is None:
            return None
        return f"{operational_grade}{class_value}"

    @staticmethod
    def _score_impact_answer(value: Optional[str]) -> int:
        mapping = {
            "major +": 5,
            "major -": -5,
            "minor +": 3,
            "minor -": -3,
            "none": 0,
        }
        if value is None:
            return 0
        return mapping.get(str(value).strip().lower(), 0)

    def _extract_impact_score(
        self, evaluation: Optional[Dict[str, Any]]
    ) -> Optional[int]:
        if not evaluation:
            return None

        answers = [
            evaluation.get("impact_question_1"),
            evaluation.get("impact_question_2"),
            evaluation.get("impact_question_3"),
            evaluation.get("impact_question_4"),
            evaluation.get("impact_question_5"),
            evaluation.get("impact_question_6"),
        ]
        if any(answer not in (None, "") for answer in answers):
            return sum(self._score_impact_answer(answer) for answer in answers)

        raw_score = evaluation.get("impact_score")
        return int(raw_score) if raw_score not in (None, "") else None

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

    async def _replace_group_categories(
        self,
        group_id: int,
        categories: List[tuple[str, str]],
    ) -> None:
        for category_key, category_label in categories:
            stmt = select(SupplierCategory).where(
                SupplierCategory.category_key == category_key
            )
            result = await self.db.execute(stmt)
            category = result.scalar_one_or_none()
            if not category:
                category = SupplierCategory(
                    category_key=category_key,
                    category_label=category_label,
                )
                self.db.add(category)
                await self.db.flush()

            self.db.add(
                SupplierGroupCategory(
                    id_group=group_id,
                    id_category=category.id_category,
                )
            )
        await self.db.flush()

    @staticmethod
    def _prepare_group_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        prepared = dict(data)
        supplier_owner = prepared.pop("supplier_owner", None)
        if supplier_owner is not None:
            prepared["group_supplier_owner_email"] = supplier_owner
        prepared.pop("supplier_type", None)
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

    @staticmethod
    def _extract_categories(raw_value: Any) -> List[tuple[str, str]]:
        if raw_value in (None, ""):
            return []
        if isinstance(raw_value, str):
            raw_items = [item.strip() for item in raw_value.split(",")]
        elif isinstance(raw_value, list):
            raw_items = [str(item).strip() for item in raw_value]
        else:
            raw_items = [str(raw_value).strip()]

        categories: List[tuple[str, str]] = []
        seen: set[str] = set()
        for item in raw_items:
            if not item:
                continue
            category_key = "".join(
                character if character.isalnum() else "_"
                for character in item.lower().replace("&", "and")
            ).strip("_")
            if not category_key or category_key in seen:
                continue
            seen.add(category_key)
            categories.append((category_key, item))
        return categories
