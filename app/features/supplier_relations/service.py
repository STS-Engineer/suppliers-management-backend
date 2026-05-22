"""Supplier relations service layer."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.models import (
    Classification,
    Document,
    EvaluationCycle,
    ImpactEvaluationInput,
    OperationalEvaluationInput,
    PldClassEvaluationInput,
    PldScoringRules,
    ScoreCard,
    SupplierCertification,
    SupplierSiteRelation,
    SupplierStatusHistory,
)
from app.features.supplier_relations import schemas
from app.shared.utils.blob_storage import get_fresh_doc_url, upload_evaluation_document
from app.shared.utils.blob_storage import delete_blob

CLASS_CRITERIA_FIELDS = (
    "top",
    "lta",
    "productivity",
    "quality_certification",
    "prod_lia_ins",
    "competitiveness",
    "sqma",
    "family_coverage",
    "geo_coverage",
    "cons_or_wd",
    "financial_health",
)

OPERATIONAL_SCORE_FIELDS = (
    "management_system",
    "customer_communication",
    "development_design",
    "production_manufacturing",
    "quality_audits",
    "suppliers_subcontractors",
    "deliveries",
    "environment_ethic_rules",
)

STATUS_CAN_QUOTE_AND_BE_AWARDED = "Can Quote and Be Awarded"
STATUS_CAN_QUOTE_NOT_BE_AWARDED = "Can Quote but Not be Awarded"
STATUS_NEW_BUSINESS_ON_HOLD = "New business on Hold"
STATUS_OVERRIDE_MARKER = "[STATUS_OVERRIDE]"

CRITERIA_VALUE_NORMALIZATION = {
    "top": {
        "60 days eom or +": "60 days end of month or +",
    },
    "lta": {
        "3 years /+": "3 years/+",
        "None": "None/Invalid",
    },
    "competitiveness": {
        "Less Avg (Not Comp.)": "Less Avg",
        "Not Competitive": "Not Comp.",
    },
    "sqma": {
        "Signed M.Res/not sent": "Signed M/Res/not sent",
    },
    "family_coverage": {
        "1 ref": "Supplier can make 1 family requirements",
        "100% Cov.": "Supplier can make all the family requirements",
        "1 sub-F or refs Cov.": "Supplier can make only of few family requirements",
        "Main sub-Fam Cov.": "Supplier can make the main family requirements",
    },
    "geo_coverage": {
        "50% or +": "More than 50% plants are covered",
    },
    "cons_or_wd": {
        "Cons. or WD": "Cons. Or Daily Deliveries",
        "Cons. or WD Inter. User": "DDP or Weekly Del.",
        "None": "Other",
    },
    "quality_certification": {
        "IATF 16949:2016": "IATF / ISO9001 (cat BCD)",
        "ISO9001 (cat BCD)": "IATF / ISO9001 (cat BCD)",
        "Distributor": "None",
    },
}

FINANCIAL_HEALTH_VALIDITY_YEARS = {
    "Good": 2,
    "To Monitor": 1,
    "At Risk": 1,
}

class SupplierRelationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._criteria_detail_has_document_column_cache: Optional[bool] = None

    async def get_relation(self, relation_id: int) -> SupplierSiteRelation:
        relation = await self.db.get(SupplierSiteRelation, relation_id)
        if not relation:
            raise AppException(
                f"Supplier relation with ID {relation_id} not found",
                status_code=404,
            )
        return relation

    async def get_relation_evaluation_workspace(
        self,
        relation_id: int,
    ) -> dict[str, Any]:
        relation = await self.get_relation(relation_id)
        class_input = await self._get_latest_class_input(relation_id)
        classification = await self._get_latest_classification(relation_id)
        operational_input = await self._get_latest_operational_input(relation_id)
        impact_input = await self._get_latest_impact_input(relation_id)
        status_history = await self._get_status_history(relation_id)
        criteria_details = await self._get_latest_criteria_details(relation_id)
        quality_certification = self._normalize_criteria_value(
            "quality_certification",
            self._pluck(class_input, "quality_certification")
            or await self._get_relation_quality_certification(relation),
        )

        return {
            "relation": relation,
            "evaluation_date": relation.last_evaluation_date,
            "status_history": status_history,
            "computed_supplier_status": self._derive_supplier_status(
                relation.final_grade
            ),
            "effective_supplier_status": relation.supplier_status
            or self._derive_supplier_status(relation.final_grade),
            "status_override": self._build_status_override_payload(
                relation=relation,
                status_history=status_history,
            ),
            "class_criteria_details": criteria_details,
            "comments": relation.evaluation_comments,
            "impact_score": self._pluck(classification, "impact_score"),
            "class_value": relation.class_value,
            "class_score": self._pluck(classification, "classification_score"),
            "operational_grade": relation.operational_grade,
            "operational_score": self._pluck(classification, "operational_score"),
            "strategic_mention": relation.strategic_mention,
            "panel_decision": relation.panel_decision,
            "top": self._pluck(class_input, "top"),
            "lta": self._pluck(class_input, "lta"),
            "sqma": self._pluck(class_input, "sqma"),
            "quality_certification": quality_certification,
            "family_coverage": self._pluck(class_input, "family_coverage"),
            "competitiveness": self._normalize_criteria_value(
                "competitiveness",
                self._pluck(class_input, "competitiveness"),
            ),
            "geo_coverage": self._normalize_criteria_value(
                "geo_coverage",
                self._pluck(class_input, "geo_coverage"),
            ),
            "cons_or_wd": self._pluck(class_input, "cons_or_wd"),
            "financial_health": self._pluck(class_input, "financial_health"),
            "prod_lia_ins": self._pluck(class_input, "prod_lia_ins"),
            "prod": self._pluck(class_input, "productivity"),
            "management_system": self._pluck(operational_input, "management_system"),
            "customer_communication": self._pluck(operational_input, "customer_communication"),
            "development_design": self._pluck(operational_input, "development_design"),
            "production_manufacturing": self._pluck(operational_input, "production_manufacturing"),
            "quality_audits": self._pluck(operational_input, "quality_audits"),
            "suppliers_subcontractors": self._pluck(operational_input, "suppliers_subcontractors"),
            "deliveries": self._pluck(operational_input, "deliveries"),
            "environment_ethic_rules": self._pluck(operational_input, "environment_ethic_rules"),
            "impact_question_1": self._pluck(impact_input, "question_1"),
            "impact_question_2": self._pluck(impact_input, "question_2"),
            "impact_question_3": self._pluck(impact_input, "question_3"),
            "impact_question_4": self._pluck(impact_input, "question_4"),
            "impact_question_5": self._pluck(impact_input, "question_5"),
            "impact_question_6": self._pluck(impact_input, "question_6"),
        }

    async def upload_criteria_document(
        self,
        relation_id: int,
        criteria_type: str,
        file: Any,
        uploaded_by: Optional[str],
        comments: Optional[str] = None,
    ) -> Document:
        relation = await self.get_relation(relation_id)
        normalized_criteria_type = self._to_criteria_detail_key(criteria_type)
        if normalized_criteria_type not in CLASS_CRITERIA_FIELDS:
            raise AppException(
                f"Unsupported criteria type '{criteria_type}'",
                status_code=400,
            )

        upload = await upload_evaluation_document(
            file=file,
            relation_id=relation_id,
            criteria_type=normalized_criteria_type,
        )
        document = Document(
            id_relation=relation_id,
            id_supplier_unit=relation.id_supplier_unit,
            document_type="evaluation_criterion_evidence",
            document_name=f"{normalized_criteria_type.replace('_', ' ').title()} Evidence",
            original_file_name=upload["filename"],
            file_path=upload["blob_name"],
            file_url=upload["file_url"],
            mime_type=upload["mimetype"],
            file_size=Decimal(str(upload["size"])),
            uploaded_by=uploaded_by or "SYSTEM",
            comments=comments or f"Evidence uploaded for {normalized_criteria_type}.",
            document_owner=uploaded_by or "SYSTEM",
            controlled_document=False,
            storage_provider="azure_blob",
            storage_object_key=upload["blob_name"],
        )
        self.db.add(document)
        await self.db.flush()
        return document

    async def delete_criteria_document(
        self,
        relation_id: int,
        criteria_type: str,
    ) -> dict[str, Any]:
        await self.get_relation(relation_id)
        normalized_criteria_type = self._to_criteria_detail_key(criteria_type)
        if normalized_criteria_type not in CLASS_CRITERIA_FIELDS:
            raise AppException(
                f"Unsupported criteria type '{criteria_type}'",
                status_code=400,
            )

        has_document_column = await self._criteria_detail_has_document_column()
        if not has_document_column:
            raise AppException(
                "Criteria document deletion is not available because id_document is missing from pld_class_criteria_detail.",
                status_code=400,
            )

        latest_detail_stmt = text(
            """
            SELECT id_detail, id_document
            FROM pld_class_criteria_detail
            WHERE id_relation = :relation_id
              AND criteria_type = :criteria_type
            ORDER BY id_detail DESC
            LIMIT 1
            """
        )
        latest_detail_result = await self.db.execute(
            latest_detail_stmt,
            {"relation_id": relation_id, "criteria_type": normalized_criteria_type},
        )
        latest_detail = latest_detail_result.mappings().first()
        if not latest_detail or not latest_detail.get("id_document"):
            return {
                "relation_id": relation_id,
                "criteria_type": normalized_criteria_type,
                "deleted_document_id": None,
            }

        document_id = int(latest_detail["id_document"])
        document = await self.db.get(Document, document_id)

        clear_stmt = text(
            """
            UPDATE pld_class_criteria_detail
            SET id_document = NULL,
                evidence_file_name = NULL
            WHERE id_detail = :id_detail
            """
        )
        await self.db.execute(clear_stmt, {"id_detail": latest_detail["id_detail"]})

        if document:
            if document.storage_object_key:
                try:
                    await delete_blob(document.storage_object_key)
                except Exception:
                    logger = __import__("logging").getLogger(__name__)
                    logger.warning(
                        "Failed to delete Azure blob for document %s",
                        document.id_document,
                        exc_info=True,
                    )
            await self.db.delete(document)

        await self.db.commit()
        return {
            "relation_id": relation_id,
            "criteria_type": normalized_criteria_type,
            "deleted_document_id": document_id,
        }

    async def update_class_evaluation(
        self,
        relation_id: int,
        data: schemas.ClassEvaluationUpdateRequest,
    ) -> dict[str, Any]:
        relation = await self.get_relation(relation_id)
        previous_input = await self._get_latest_class_input(relation_id)
        current_classification = await self._get_latest_classification(relation_id)
        previous_impact_input = await self._get_latest_impact_input(relation_id)
        evaluation_date = data.evaluation_date or datetime.now().date()

        quality_certification = await self._resolve_quality_certification(
            relation=relation,
            selected_value=data.quality_certification,
            previous_input=previous_input,
        )

        merged_values = {
            "top": self._normalize_criteria_value("top", data.top if data.top is not None else self._pluck(previous_input, "top")),
            "lta": self._normalize_criteria_value("lta", data.lta if data.lta is not None else self._pluck(previous_input, "lta")),
            "productivity": self._normalize_criteria_value("productivity", data.productivity if data.productivity is not None else self._pluck(previous_input, "productivity")),
            "quality_certification": quality_certification,
            "prod_lia_ins": self._normalize_criteria_value("prod_lia_ins", data.prod_lia_ins if data.prod_lia_ins is not None else self._pluck(previous_input, "prod_lia_ins")),
            "competitiveness": self._normalize_criteria_value("competitiveness", data.competitiveness if data.competitiveness is not None else self._pluck(previous_input, "competitiveness")),
            "sqma": self._normalize_criteria_value("sqma", data.sqma if data.sqma is not None else self._pluck(previous_input, "sqma")),
            "family_coverage": self._normalize_criteria_value("family_coverage", data.family_coverage if data.family_coverage is not None else self._pluck(previous_input, "family_coverage")),
            "geo_coverage": self._normalize_criteria_value("geo_coverage", data.geo_coverage if data.geo_coverage is not None else self._pluck(previous_input, "geo_coverage")),
            "cons_or_wd": self._normalize_criteria_value("cons_or_wd", data.cons_or_wd if data.cons_or_wd is not None else self._pluck(previous_input, "cons_or_wd")),
            "financial_health": self._normalize_criteria_value("financial_health", data.financial_health if data.financial_health is not None else self._pluck(previous_input, "financial_health")),
        }
        strategic_mention = (
            data.strategic_mention
            or self._pluck(current_classification, "strategic_mention")
            or relation.strategic_mention
        )
        panel_decision = (
            data.panel_decision
            or self._pluck(current_classification, "panel_decision")
            or relation.panel_decision
        )
        impact_score = (
            data.impact_score
            if data.impact_score is not None
            else self._pluck(current_classification, "impact_score")
        )
        evaluation_changed = self._class_evaluation_changed(
            previous_input=previous_input,
            merged_values=merged_values,
            current_classification=current_classification,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            previous_impact_input=previous_impact_input,
            data=data,
        )
        cycle = None
        if evaluation_changed:
            cycle = await self._create_cycle(
                relation_id=relation_id,
                cycle_type=data.cycle_type,
                comments=data.comments or "Class evaluation criteria updated.",
                evaluation_date=evaluation_date,
            )

        class_score = self._prefer_decimal(
            await self._try_calculate_class_score(merged_values),
            self._pluck(current_classification, "classification_score"),
        )
        class_value = (
            self._derive_class_value_from_score(class_score)
            if class_score is not None
            else self._pluck(current_classification, "class_value")
        )

        operational_grade = relation.operational_grade
        operational_score = self._pluck(current_classification, "operational_score")
        final_grade = self._compose_final_grade(operational_grade, class_value)
        computed_status = self._derive_supplier_status(final_grade)
        effective_status = self._resolve_effective_supplier_status(
            relation=relation,
            computed_status=computed_status,
        )

        class_input = PldClassEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle if cycle else self._resolve_existing_cycle_id(
                previous_input,
                current_classification,
                previous_impact_input,
            ),
            top=merged_values["top"],
            lta=merged_values["lta"],
            productivity=merged_values["productivity"],
            quality_certification=merged_values["quality_certification"],
            prod_lia_ins=merged_values["prod_lia_ins"],
            competitiveness=merged_values["competitiveness"],
            sqma=merged_values["sqma"],
            family_coverage=merged_values["family_coverage"],
            geo_coverage=merged_values["geo_coverage"],
            cons_or_wd=merged_values["cons_or_wd"],
            financial_health=merged_values["financial_health"],
            class_score=class_score,
            class_value=class_value,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=data.comments,
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(class_input)
        await self._store_criteria_details(
            relation_id=relation_id,
            cycle_id=cycle.id_cycle if cycle else self._resolve_existing_cycle_id(
                previous_input,
                current_classification,
                previous_impact_input,
            ),
            merged_values=merged_values,
            submitted_details=data.class_criteria_details,
            changed_by=data.changed_by or "SYSTEM",
        )
        latest_criteria_details = await self._get_latest_criteria_details(relation_id)
        impact_input = ImpactEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle if cycle else self._resolve_existing_cycle_id(
                previous_input,
                current_classification,
                previous_impact_input,
            ),
            question_1=data.impact_question_1 if data.impact_question_1 is not None else self._pluck(previous_impact_input, "question_1"),
            question_2=data.impact_question_2 if data.impact_question_2 is not None else self._pluck(previous_impact_input, "question_2"),
            question_3=data.impact_question_3 if data.impact_question_3 is not None else self._pluck(previous_impact_input, "question_3"),
            question_4=data.impact_question_4 if data.impact_question_4 is not None else self._pluck(previous_impact_input, "question_4"),
            question_5=data.impact_question_5 if data.impact_question_5 is not None else self._pluck(previous_impact_input, "question_5"),
            question_6=data.impact_question_6 if data.impact_question_6 is not None else self._pluck(previous_impact_input, "question_6"),
            impact_score=impact_score,
            comments=data.comments,
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(impact_input)
        classification = None
        history = None
        if evaluation_changed and cycle:
            classification = Classification(
                id_relation=relation_id,
                id_cycle=cycle.id_cycle,
                classification_date=evaluation_date,
                classification_score=class_score,
                class_value=class_value,
                operational_score=operational_score,
                operational_grade=operational_grade,
                final_grade=final_grade,
                impact_score=impact_score,
                strategic_mention=strategic_mention,
                panel_decision=panel_decision,
                comments=data.comments or "Class evaluation recalculated from PLD criteria update.",
                entered_by=data.changed_by or "SYSTEM",
            )
            self.db.add(classification)
            await self.db.flush()

            history = await self._record_transition(
                relation=relation,
                changed_by=data.changed_by or "SYSTEM",
                reason=data.comments or "Class evaluation criteria updated.",
                changed_at=datetime.combine(evaluation_date, datetime.now().time()),
                new_status=effective_status,
                new_class=class_value,
                new_grade=operational_grade,
                new_final_grade=final_grade,
                new_strategic_mention=strategic_mention,
                new_panel_decision=panel_decision,
            )

        relation.class_value = class_value
        relation.final_grade = final_grade
        relation.strategic_mention = strategic_mention
        relation.panel_decision = panel_decision
        relation.supplier_status = effective_status
        relation.evaluation_suggestion = panel_decision or relation.evaluation_suggestion
        if history and history.old_status != history.new_status:
            relation.last_status_change = history.changed_at
        if evaluation_changed:
            relation.last_evaluation_date = evaluation_date
        relation.next_evaluation_date = self._extract_next_evaluation_date(
            latest_criteria_details,
        )
        if data.comments:
            relation.evaluation_comments = data.comments

        await self.db.commit()
        await self.db.refresh(relation)

        return {
            "relation": relation,
            "cycle": cycle,
            "classification": classification,
            "score_card": None,
            "status_history": history,
        }

    async def update_operational_evaluation(
        self,
        relation_id: int,
        data: schemas.OperationalEvaluationUpdateRequest,
    ) -> dict[str, Any]:
        relation = await self.get_relation(relation_id)
        current_classification = await self._get_latest_classification(relation_id)
        evaluation_date = data.evaluation_date or datetime.now().date()

        previous_operational_input = await self._get_latest_operational_input(relation_id)
        merged_operational_values = self._merge_operational_values(
            previous_operational_input=previous_operational_input,
            data=data,
        )
        operational_score = self._calculate_operational_score(
            merged_operational_values,
        )
        if operational_score is None:
            operational_score = self._prefer_decimal(
                data.operational_score,
                self._pluck(current_classification, "operational_score"),
            )
        operational_grade = self._derive_operational_grade(operational_score) or (
            relation.operational_grade
        )
        evaluation_changed = self._operational_evaluation_changed(
            previous_operational_input=previous_operational_input,
            merged_operational_values=merged_operational_values,
            operational_score=operational_score,
            operational_grade=operational_grade,
        )
        cycle = await self._create_cycle(
            relation_id=relation_id,
            cycle_type=data.cycle_type or self._default_operational_cycle_type(data.source_type),
            comments=data.comments or f"Operational evaluation refreshed from {data.source_type}.",
            evaluation_date=evaluation_date,
        )
        class_score = self._pluck(current_classification, "classification_score")
        class_value = self._pluck(current_classification, "class_value") or relation.class_value
        strategic_mention = self._pluck(current_classification, "strategic_mention") or relation.strategic_mention
        panel_decision = self._pluck(current_classification, "panel_decision") or relation.panel_decision
        impact_score = self._pluck(current_classification, "impact_score")
        final_grade = self._compose_final_grade(operational_grade, class_value)
        computed_status = self._derive_supplier_status(final_grade)
        effective_status = self._resolve_effective_supplier_status(
            relation=relation,
            computed_status=computed_status,
        )

        operational_input = OperationalEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            source_type=data.source_type,
            management_system=merged_operational_values["management_system"],
            customer_communication=merged_operational_values["customer_communication"],
            development_design=merged_operational_values["development_design"],
            production_manufacturing=merged_operational_values["production_manufacturing"],
            quality_audits=merged_operational_values["quality_audits"],
            suppliers_subcontractors=merged_operational_values["suppliers_subcontractors"],
            deliveries=merged_operational_values["deliveries"],
            environment_ethic_rules=merged_operational_values["environment_ethic_rules"],
            average_score=operational_score,
            operational_grade=operational_grade,
            comments=data.comments,
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(operational_input)

        score_card = ScoreCard(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            scorecard_date=evaluation_date,
            score=operational_score,
            grade=operational_grade,
            comments=data.comments or f"Operational evaluation updated from {data.source_type}.",
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(score_card)
        await self.db.flush()

        classification = Classification(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            classification_date=evaluation_date,
            classification_score=class_score,
            class_value=class_value,
            operational_score=operational_score,
            operational_grade=operational_grade,
            final_grade=final_grade,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=data.comments or "Operational evaluation refreshed while keeping latest class evaluation.",
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(classification)

        history = await self._record_transition(
            relation=relation,
            changed_by=data.changed_by or "SYSTEM",
            reason=data.comments or f"Operational evaluation updated from {data.source_type}.",
            changed_at=datetime.combine(evaluation_date, datetime.now().time()),
            new_status=effective_status,
            new_class=class_value,
            new_grade=operational_grade,
            new_final_grade=final_grade,
            new_strategic_mention=strategic_mention,
            new_panel_decision=panel_decision,
        )

        relation.operational_grade = operational_grade
        relation.final_grade = final_grade
        relation.supplier_status = effective_status
        relation.evaluation_suggestion = panel_decision or relation.evaluation_suggestion
        if history and history.old_status != history.new_status:
            relation.last_status_change = history.changed_at
        if evaluation_changed:
            relation.last_evaluation_date = evaluation_date
        if data.comments:
            relation.evaluation_comments = data.comments

        await self.db.commit()
        await self.db.refresh(relation)

        return {
            "relation": relation,
            "cycle": cycle,
            "classification": classification,
            "score_card": score_card,
            "status_history": history,
        }

    async def create_initial_evaluation(
        self,
        relation_id: int,
        data: schemas.InitialRelationEvaluationRequest,
    ) -> dict[str, Any]:
        relation = await self.get_relation(relation_id)
        evaluation_date = data.evaluation_date or datetime.now().date()

        certification_stmt = (
            select(SupplierCertification)
            .where(SupplierCertification.id_supplier_unit == relation.id_supplier_unit)
            .order_by(SupplierCertification.id_certification.asc())
        )
        certification_result = await self.db.execute(certification_stmt)
        certifications = certification_result.scalars().all()
        certification_type = self._normalize_criteria_value(
            "quality_certification",
            data.quality_certification
            or (certifications[0].certification_type if certifications else None),
        )

        cycle = await self._create_cycle(
            relation_id=relation_id,
            cycle_type="Initial Relation Evaluation",
            comments=data.comments or "Initial evaluation recorded for the supplier-site relation.",
            evaluation_date=evaluation_date,
        )

        merged_values = {
            "top": self._normalize_criteria_value("top", data.top),
            "lta": self._normalize_criteria_value("lta", data.lta),
            "productivity": self._normalize_criteria_value("productivity", data.prod),
            "quality_certification": certification_type,
            "prod_lia_ins": self._normalize_criteria_value("prod_lia_ins", data.prod_lia_ins),
            "competitiveness": self._normalize_criteria_value("competitiveness", data.competitiveness),
            "sqma": self._normalize_criteria_value("sqma", data.sqma),
            "family_coverage": self._normalize_criteria_value("family_coverage", data.family_coverage),
            "geo_coverage": self._normalize_criteria_value("geo_coverage", data.geo_coverage),
            "cons_or_wd": self._normalize_criteria_value("cons_or_wd", data.cons_or_wd),
            "financial_health": self._normalize_criteria_value("financial_health", data.financial_health),
        }
        class_score = self._prefer_decimal(await self._try_calculate_class_score(merged_values))
        class_value = (
            self._derive_class_value_from_score(class_score)
            if class_score is not None
            else self._prefer_int(data.class_value, data.impact)
        )

        merged_operational_values = self._merge_operational_values(
            previous_operational_input=None,
            data=data,
        )
        operational_score = self._calculate_operational_score(
            merged_operational_values,
        ) or self._prefer_decimal(data.operational_score)
        operational_grade = self._derive_operational_grade(operational_score) or (
            data.operational_grade.upper()
            if data.operational_grade
            else data.operational_class.upper() if data.operational_class else None
        )
        impact_score = data.impact_score
        strategic_mention = data.strategic_mention.lower() if data.strategic_mention else None
        panel_decision = (
            data.panel_decision.lower()
            if data.panel_decision
            else self._map_legacy_suggestion(data.suggestion)
        )
        final_grade = self._compose_final_grade(operational_grade, class_value)

        class_input = PldClassEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            top=merged_values["top"],
            lta=merged_values["lta"],
            productivity=merged_values["productivity"],
            quality_certification=certification_type,
            prod_lia_ins=merged_values["prod_lia_ins"],
            competitiveness=merged_values["competitiveness"],
            sqma=merged_values["sqma"],
            family_coverage=merged_values["family_coverage"],
            geo_coverage=merged_values["geo_coverage"],
            cons_or_wd=merged_values["cons_or_wd"],
            financial_health=merged_values["financial_health"],
            class_score=class_score,
            class_value=class_value,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=data.comments,
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(class_input)
        await self._store_criteria_details(
            relation_id=relation_id,
            cycle_id=cycle.id_cycle,
            merged_values=merged_values,
            submitted_details=data.class_criteria_details,
            changed_by=data.changed_by or "SYSTEM",
        )
        latest_criteria_details = await self._get_latest_criteria_details(relation_id)

        operational_input = OperationalEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            source_type="self_assessment",
            management_system=merged_operational_values["management_system"],
            customer_communication=merged_operational_values["customer_communication"],
            development_design=merged_operational_values["development_design"],
            production_manufacturing=merged_operational_values["production_manufacturing"],
            quality_audits=merged_operational_values["quality_audits"],
            suppliers_subcontractors=merged_operational_values["suppliers_subcontractors"],
            deliveries=merged_operational_values["deliveries"],
            environment_ethic_rules=merged_operational_values["environment_ethic_rules"],
            average_score=operational_score,
            operational_grade=operational_grade,
            comments=data.comments,
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(operational_input)

        impact_input = ImpactEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            question_1=data.impact_question_1,
            question_2=data.impact_question_2,
            question_3=data.impact_question_3,
            question_4=data.impact_question_4,
            question_5=data.impact_question_5,
            question_6=data.impact_question_6,
            impact_score=impact_score,
            comments=data.comments,
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(impact_input)

        score_card = ScoreCard(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            scorecard_date=evaluation_date,
            score=operational_score,
            grade=operational_grade,
            comments=data.comments or "Initial operational self-assessment baseline.",
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(score_card)
        await self.db.flush()

        classification = Classification(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            classification_date=evaluation_date,
            classification_score=class_score,
            class_value=class_value,
            operational_score=operational_score,
            operational_grade=operational_grade,
            final_grade=final_grade,
            impact_score=impact_score,
            strategic_mention=strategic_mention,
            panel_decision=panel_decision,
            comments=data.comments or "Initial relation evaluation saved.",
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(classification)
        await self.db.flush()
        computed_status = self._derive_supplier_status(final_grade)
        effective_status = self._resolve_effective_supplier_status(
            relation=relation,
            computed_status=computed_status,
        )

        history = await self._record_transition(
            relation=relation,
            changed_by=data.changed_by or "SYSTEM",
            reason=data.comments or "Initial relation evaluation saved.",
            changed_at=datetime.combine(evaluation_date, datetime.now().time()),
            new_status=effective_status,
            new_class=class_value,
            new_grade=operational_grade,
            new_final_grade=final_grade,
            new_strategic_mention=strategic_mention,
            new_panel_decision=panel_decision,
        )

        relation.class_value = class_value
        relation.operational_grade = operational_grade
        relation.final_grade = final_grade
        relation.strategic_mention = strategic_mention
        relation.panel_decision = panel_decision
        relation.supplier_status = effective_status
        relation.evaluation_suggestion = panel_decision or relation.evaluation_suggestion
        if history and history.old_status != history.new_status:
            relation.last_status_change = history.changed_at
        relation.last_evaluation_date = evaluation_date
        relation.next_evaluation_date = self._extract_next_evaluation_date(
            latest_criteria_details,
        )
        if data.comments:
            relation.evaluation_comments = data.comments

        await self.db.commit()
        await self.db.refresh(relation)

        return {
            "relation": relation,
            "cycle": cycle,
            "classification": classification,
            "score_card": score_card,
            "status_history": history,
        }

    async def override_supplier_status(
        self,
        relation_id: int,
        data: schemas.SupplierStatusOverrideRequest,
    ) -> dict[str, Any]:
        relation = await self.get_relation(relation_id)
        override_date = data.override_date or datetime.now()
        computed_status = self._derive_supplier_status(relation.final_grade)
        current_status = relation.supplier_status or computed_status

        if current_status == data.supplier_status:
            raise AppException(
                "Supplier status is already set to the requested value.",
                status_code=400,
            )

        history = SupplierStatusHistory(
            id_relation=relation.id_relation,
            old_status=current_status,
            new_status=data.supplier_status,
            old_class=relation.class_value,
            new_class=relation.class_value,
            old_grade=relation.operational_grade,
            new_grade=relation.operational_grade,
            old_final_grade=relation.final_grade,
            new_final_grade=relation.final_grade,
            old_strategic_mention=relation.strategic_mention,
            new_strategic_mention=relation.strategic_mention,
            old_panel_decision=relation.panel_decision,
            new_panel_decision=relation.panel_decision,
            change_reason=f"{STATUS_OVERRIDE_MARKER} {data.reason}",
            changed_by=data.changed_by or "SYSTEM",
            changed_at=override_date,
        )
        self.db.add(history)

        relation.supplier_status = data.supplier_status
        relation.last_status_change = override_date
        if data.reason:
            relation.evaluation_comments = data.reason

        await self.db.commit()
        await self.db.refresh(relation)
        await self.db.refresh(history)

        return {
            "relation": relation,
            "status_history": history,
            "computed_supplier_status": computed_status,
        }

    async def _create_cycle(
        self,
        relation_id: int,
        cycle_type: str,
        comments: str,
        evaluation_date: date,
    ) -> EvaluationCycle:
        cycle = EvaluationCycle(
            id_relation=relation_id,
            cycle_type=cycle_type,
            frequency="Ad hoc",
            period_start=evaluation_date,
            period_end=evaluation_date,
            due_date=evaluation_date,
            cycle_status="Completed",
            launched_by="SYSTEM",
            launched_at=datetime.now(),
            completed_at=datetime.now(),
            comments=comments,
        )
        self.db.add(cycle)
        await self.db.flush()
        return cycle

    async def _get_latest_class_input(
        self,
        relation_id: int,
    ) -> Optional[PldClassEvaluationInput]:
        stmt = (
            select(PldClassEvaluationInput)
            .where(PldClassEvaluationInput.id_relation == relation_id)
            .order_by(PldClassEvaluationInput.id_pld_input.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _get_latest_criteria_details(
        self,
        relation_id: int,
    ) -> dict[str, dict[str, Any]]:
        has_document_column = await self._criteria_detail_has_document_column()
        select_columns = [
            "id_detail",
            "id_relation",
            "id_cycle",
            "criteria_type",
            "selected_value",
            "score",
            "evidence_file_name",
            "validity_start_date",
            "validity_end_date",
            "signature_date",
            "last_update_date",
            "amount_value",
            "amount_currency",
            "auto_validity_end_date",
            "comments",
        ]
        if has_document_column:
            select_columns.insert(3, "id_document")
        stmt = text(
            f"""
            SELECT {", ".join(select_columns)}
            FROM pld_class_criteria_detail
            WHERE id_relation = :relation_id
            ORDER BY criteria_type ASC, id_detail DESC
            """
        )
        result = await self.db.execute(stmt, {"relation_id": relation_id})
        entries = result.mappings().all()
        document_ids = [entry["id_document"] for entry in entries if has_document_column and entry.get("id_document")]
        documents_by_id: dict[int, Document] = {}
        if document_ids:
            documents_result = await self.db.execute(
                select(Document).where(Document.id_document.in_(document_ids))
            )
            documents_by_id = {
                doc.id_document: doc for doc in documents_result.scalars().all()
            }
        latest_by_criteria: dict[str, dict[str, Any]] = {}
        for entry in entries:
            criteria_type = entry["criteria_type"]
            if criteria_type in latest_by_criteria:
                continue
            document_id = entry.get("id_document") if has_document_column else None
            document = documents_by_id.get(document_id) if document_id else None
            latest_by_criteria[criteria_type] = {
                "document_id": document_id,
                "document_name": document.document_name if document else None,
                "document_url": get_fresh_doc_url(document.file_url) if document and document.file_url else None,
                "document_mime_type": document.mime_type if document else None,
                "document_size": document.file_size if document else None,
                "evidence_file_name": entry["evidence_file_name"],
                "validity_start_date": entry["validity_start_date"],
                "validity_end_date": entry["validity_end_date"],
                "signature_date": entry["signature_date"],
                "last_update_date": entry["last_update_date"],
                "amount_value": entry["amount_value"],
                "amount_currency": entry["amount_currency"],
                "auto_validity_end_date": entry["auto_validity_end_date"],
                "comments": entry["comments"],
                "score": entry["score"],
            }
        return latest_by_criteria

    async def _get_relation_quality_certification(
        self,
        relation: SupplierSiteRelation,
    ) -> Optional[str]:
        stmt = (
            select(SupplierCertification)
            .where(SupplierCertification.id_supplier_unit == relation.id_supplier_unit)
            .order_by(SupplierCertification.id_certification.asc())
        )
        result = await self.db.execute(stmt)
        certification = result.scalars().first()
        return certification.certification_type if certification else None

    async def _get_latest_classification(
        self,
        relation_id: int,
    ) -> Optional[Classification]:
        stmt = (
            select(Classification)
            .where(Classification.id_relation == relation_id)
            .order_by(Classification.id_classification.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _get_latest_operational_input(
        self,
        relation_id: int,
    ) -> Optional[OperationalEvaluationInput]:
        stmt = (
            select(OperationalEvaluationInput)
            .where(OperationalEvaluationInput.id_relation == relation_id)
            .order_by(OperationalEvaluationInput.id_operational_input.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _get_latest_impact_input(
        self,
        relation_id: int,
    ) -> Optional[ImpactEvaluationInput]:
        stmt = (
            select(ImpactEvaluationInput)
            .where(ImpactEvaluationInput.id_relation == relation_id)
            .order_by(ImpactEvaluationInput.id_impact_input.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _get_status_history(
        self,
        relation_id: int,
    ) -> list[SupplierStatusHistory]:
        stmt = (
            select(SupplierStatusHistory)
            .where(SupplierStatusHistory.id_relation == relation_id)
            .order_by(SupplierStatusHistory.changed_at.desc(), SupplierStatusHistory.id_history.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _record_transition(
        self,
        relation: SupplierSiteRelation,
        changed_by: str,
        reason: str,
        changed_at: datetime,
        new_status: Optional[str],
        new_class: Optional[int],
        new_grade: Optional[str],
        new_final_grade: Optional[str],
        new_strategic_mention: Optional[str],
        new_panel_decision: Optional[str],
    ) -> Optional[SupplierStatusHistory]:
        if (
            relation.supplier_status == new_status
            and
            relation.class_value == new_class
            and relation.operational_grade == new_grade
            and relation.final_grade == new_final_grade
            and relation.strategic_mention == new_strategic_mention
            and relation.panel_decision == new_panel_decision
        ):
            return None

        history = SupplierStatusHistory(
            id_relation=relation.id_relation,
            old_status=relation.supplier_status,
            new_status=new_status,
            old_class=relation.class_value,
            new_class=new_class,
            old_grade=relation.operational_grade,
            new_grade=new_grade,
            old_final_grade=relation.final_grade,
            new_final_grade=new_final_grade,
            old_strategic_mention=relation.strategic_mention,
            new_strategic_mention=new_strategic_mention,
            old_panel_decision=relation.panel_decision,
            new_panel_decision=new_panel_decision,
            change_reason=reason,
            changed_by=changed_by,
            changed_at=changed_at,
        )
        self.db.add(history)
        await self.db.flush()
        return history

    async def _resolve_quality_certification(
        self,
        relation: SupplierSiteRelation,
        selected_value: Optional[str],
        previous_input: Optional[PldClassEvaluationInput],
    ) -> Optional[str]:
        if selected_value is not None:
            return self._normalize_criteria_value("quality_certification", selected_value)
        previous_value = self._pluck(previous_input, "quality_certification")
        if previous_value:
            return self._normalize_criteria_value("quality_certification", previous_value)
        return self._normalize_criteria_value(
            "quality_certification",
            await self._get_relation_quality_certification(relation),
        )

    async def _store_criteria_details(
        self,
        relation_id: int,
        cycle_id: Optional[int],
        merged_values: dict[str, Optional[str]],
        submitted_details: dict[str, Any],
        changed_by: str,
    ) -> None:
        if submitted_details is None:
            submitted_details = {}

        latest_details = await self._get_latest_criteria_details(relation_id)
        has_document_column = await self._criteria_detail_has_document_column()
        for criteria_type in CLASS_CRITERIA_FIELDS:
            payload = submitted_details.get(criteria_type) or latest_details.get(criteria_type) or {}
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(exclude_none=True)
            detail = self._normalize_detail_payload(
                criteria_type=criteria_type,
                selected_value=merged_values.get(criteria_type),
                payload=payload,
            )
            if has_document_column:
                detail["document_id"] = await self._resolve_valid_document_id(
                    relation_id=relation_id,
                    document_id=detail.get("document_id"),
                )
            if (
                not merged_values.get(criteria_type)
                and not any(
                    detail.get(key)
                    for key in (
                        "evidence_file_name",
                        "validity_start_date",
                        "validity_end_date",
                        "signature_date",
                        "last_update_date",
                        "amount_value",
                        "amount_currency",
                        "comments",
                    )
                )
            ):
                continue

            insert_columns = [
                "id_relation",
                "id_cycle",
                "criteria_type",
                "selected_value",
                "score",
                "evidence_file_name",
                "validity_start_date",
                "validity_end_date",
                "signature_date",
                "last_update_date",
                "amount_value",
                "amount_currency",
                "auto_validity_end_date",
                "entered_by",
                "comments",
            ]
            insert_values = {
                "id_relation": relation_id,
                "id_cycle": cycle_id,
                "criteria_type": criteria_type,
                "selected_value": merged_values.get(criteria_type),
                "score": detail.get("score"),
                "evidence_file_name": detail.get("evidence_file_name") or detail.get("document_name"),
                "validity_start_date": detail.get("validity_start_date"),
                "validity_end_date": detail.get("validity_end_date"),
                "signature_date": detail.get("signature_date"),
                "last_update_date": detail.get("last_update_date"),
                "amount_value": detail.get("amount_value"),
                "amount_currency": detail.get("amount_currency"),
                "auto_validity_end_date": detail.get("auto_validity_end_date", False),
                "entered_by": changed_by,
                "comments": detail.get("comments"),
            }
            if has_document_column:
                insert_columns.insert(2, "id_document")
                insert_values["id_document"] = detail.get("document_id")

            stmt = text(
                f"""
                INSERT INTO pld_class_criteria_detail ({", ".join(insert_columns)})
                VALUES ({", ".join(f":{column}" for column in insert_columns)})
                """
            )
            await self.db.execute(stmt, insert_values)
        await self.db.flush()

    async def _resolve_valid_document_id(
        self,
        relation_id: int,
        document_id: Any,
    ) -> Optional[int]:
        candidate = self._prefer_int(document_id)
        if candidate is None:
            return None

        stmt = (
            select(Document.id_document)
            .where(Document.id_document == candidate)
            .where(Document.id_relation == relation_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _criteria_detail_has_document_column(self) -> bool:
        if self._criteria_detail_has_document_column_cache is not None:
            return self._criteria_detail_has_document_column_cache

        stmt = text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'pld_class_criteria_detail'
              AND column_name = 'id_document'
            """
        )
        result = await self.db.execute(stmt)
        self._criteria_detail_has_document_column_cache = result.first() is not None
        return self._criteria_detail_has_document_column_cache

    async def _try_calculate_class_score(
        self,
        merged_values: dict[str, Optional[str]],
    ) -> Optional[Decimal]:
        selected_scores: list[Decimal] = []
        criteria_map = {
            "top": merged_values.get("top"),
            "lta": merged_values.get("lta"),
            "productivity": merged_values.get("productivity"),
            "quality_certification": merged_values.get("quality_certification"),
            "prod_lia_ins": merged_values.get("prod_lia_ins"),
            "competitiveness": merged_values.get("competitiveness"),
            "sqma": merged_values.get("sqma"),
            "family_coverage": merged_values.get("family_coverage"),
            "geo_coverage": merged_values.get("geo_coverage"),
            "cons_or_wd": merged_values.get("cons_or_wd"),
            "financial_health": merged_values.get("financial_health"),
        }

        for criteria_type, selected_value in criteria_map.items():
            if not selected_value:
                continue
            stmt = (
                select(PldScoringRules)
                .where(PldScoringRules.criteria_type == criteria_type)
                .where(PldScoringRules.is_active.is_(True))
                .where(PldScoringRules.min_value == selected_value)
                .order_by(PldScoringRules.score.desc())
            )
            result = await self.db.execute(stmt)
            rule = result.scalars().first()
            if rule and rule.score is not None:
                selected_scores.append(Decimal(str(rule.score)))

        if not selected_scores:
            return None
        return sum(selected_scores) / Decimal(len(selected_scores))

    @staticmethod
    def _pluck(instance: Any, field_name: str) -> Any:
        if instance is None:
            return None
        return getattr(instance, field_name, None)

    @staticmethod
    def _normalize_criteria_value(criteria_type: str, value: Any) -> Any:
        if value is None:
            return None
        return CRITERIA_VALUE_NORMALIZATION.get(criteria_type, {}).get(value, value)

    @staticmethod
    def _to_criteria_detail_key(criteria_type: str) -> str:
        mapping = {
            "prod": "productivity",
        }
        normalized = str(criteria_type or "").strip().lower()
        return mapping.get(normalized, normalized)

    @classmethod
    def _normalize_detail_payload(
        cls,
        criteria_type: str,
        selected_value: Optional[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        start_date = payload.get("validity_start_date")
        end_date = payload.get("validity_end_date")
        auto_validity_end_date = bool(payload.get("auto_validity_end_date"))
        if criteria_type == "financial_health" and start_date and (
            auto_validity_end_date or end_date is None
        ):
            years = FINANCIAL_HEALTH_VALIDITY_YEARS.get(selected_value or "", 0)
            if years:
                end_date = date(start_date.year + years, start_date.month, start_date.day)
                auto_validity_end_date = True

        return {
            "document_id": payload.get("document_id"),
            "document_name": payload.get("document_name"),
            "document_url": payload.get("document_url"),
            "document_mime_type": payload.get("document_mime_type"),
            "document_size": cls._prefer_decimal(payload.get("document_size")),
            "evidence_file_name": payload.get("evidence_file_name"),
            "validity_start_date": start_date,
            "validity_end_date": end_date,
            "signature_date": payload.get("signature_date"),
            "last_update_date": payload.get("last_update_date"),
            "amount_value": cls._prefer_decimal(payload.get("amount_value")),
            "amount_currency": payload.get("amount_currency"),
            "auto_validity_end_date": auto_validity_end_date,
            "comments": payload.get("comments"),
            "score": cls._prefer_decimal(
                payload.get("score"),
                cls._score_from_selected_value(criteria_type, selected_value),
            ),
        }
 
    @classmethod
    def _score_from_selected_value(
        cls,
        criteria_type: str,
        selected_value: Optional[str],
    ) -> Optional[Decimal]:
        if not selected_value:
            return None
        normalized_value = cls._normalize_criteria_value(criteria_type, selected_value)
        score_map: dict[str, Decimal] = {
            "60 days end of month or +": Decimal("100"),
            "60 days net": Decimal("80"),
            "30 days end of month or +": Decimal("50"),
            "30 days net": Decimal("30"),
            "Cash in Advance": Decimal("0"),
            "3 years/+": Decimal("100"),
            "2 years": Decimal("80"),
            "1 year": Decimal("50"),
            "None/Invalid": Decimal("0"),
            "3% or +": Decimal("100"),
            "2% or +": Decimal("80"),
            "1% or +": Decimal("50"),
            "less than 1%": Decimal("30"),
            "Neg": Decimal("0"),
            "IATF / ISO9001 (cat BCD)": Decimal("100"),
            "ISO9001": Decimal("50"),
            "None": Decimal("0"),
            "2M$ or +": Decimal("100"),
            "1M$ or +": Decimal("50"),
            "Almost Best in Fam.": Decimal("80"),
            "Best in Fam.": Decimal("100"),
            "Ave. in Fam.": Decimal("50"),
            "Less Avg": Decimal("30"),
            "Not Comp.": Decimal("0"),
            "Rejected": Decimal("0"),
            "Signed": Decimal("100"),
            "Signed m.res.": Decimal("80"),
            "Signed M/Res/not sent": Decimal("30"),
            "Supplier can make 1 family requirements": Decimal("0"),
            "Supplier can make all the family requirements": Decimal("100"),
            "Supplier can make only of few family requirements": Decimal("50"),
            "Supplier can make the main family requirements": Decimal("80"),
            "1 plant is covered": Decimal("30"),
            "Main plants covered": Decimal("100"),
            "More than 50% plants are covered": Decimal("50"),
            "Biweekly Del.": Decimal("30"),
            "Cons. Or Daily Deliveries": Decimal("100"),
            "DDP or Weekly Del.": Decimal("50"),
            "Other": Decimal("0"),
            "Good": Decimal("100"),
            "To Monitor": Decimal("50"),
            "At Risk": Decimal("0"),
        }
        return score_map.get(str(normalized_value))

    @staticmethod
    def _extract_next_evaluation_date(
        criteria_details: dict[str, dict[str, Any]],
    ) -> Optional[date]:
        financial_health = criteria_details.get("financial_health") or {}
        return financial_health.get("validity_end_date")

    @staticmethod
    def _prefer_decimal(*values: Any) -> Optional[Decimal]:
        for value in values:
            if value not in (None, ""):
                return Decimal(str(value))
        return None

    @staticmethod
    def _prefer_int(*values: Any) -> Optional[int]:
        for value in values:
            if value not in (None, ""):
                return int(value)
        return None

    @staticmethod
    def _compose_final_grade(
        operational_grade: Optional[str],
        class_value: Optional[int],
    ) -> Optional[str]:
        if not operational_grade or class_value is None:
            return None
        return f"{operational_grade}{class_value}"

    @staticmethod
    def _derive_class_value_from_score(score: Decimal) -> int:
        if score >= Decimal("90"):
            return 1
        if score >= Decimal("75"):
            return 2
        if score >= Decimal("60"):
            return 3
        return 4

    @staticmethod
    def _map_legacy_suggestion(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        mapping = {
            "can_quote_and_award": "panel_add",
            "needs_executive_committee": "panel_add_exec_committee",
            "cannot_be_added": "panel_reject",
        }
        return mapping.get(value)

    @staticmethod
    def _default_operational_cycle_type(source_type: str) -> str:
        if source_type == "self_assessment":
            return "Operational Self-Assessment Refresh"
        return "Operational KPI Refresh"

    @staticmethod
    def _calculate_operational_score(
        values: dict[str, Optional[Decimal]],
    ) -> Optional[Decimal]:
        selected_scores = [value for value in values.values() if value is not None]
        if not selected_scores:
            return None
        return sum(selected_scores) / Decimal(len(selected_scores))

    @staticmethod
    def _derive_operational_grade(score: Optional[Decimal]) -> Optional[str]:
        if score is None:
            return None
        if score >= Decimal("80"):
            return "A"
        if score >= Decimal("60"):
            return "B"
        if score >= Decimal("50"):
            return "C"
        return "D"

    @staticmethod
    def _derive_supplier_status(final_grade: Optional[str]) -> Optional[str]:
        if not final_grade:
            return None
        normalized_grade = str(final_grade).strip().upper()
        if normalized_grade in {"A1", "B1", "A2", "B2"}:
            return STATUS_CAN_QUOTE_AND_BE_AWARDED
        if normalized_grade in {"A3", "B3", "C1", "C2", "C3"}:
            return STATUS_CAN_QUOTE_NOT_BE_AWARDED
        if normalized_grade in {"D1", "D2", "D3", "D4", "A4", "B4", "C4"}:
            return STATUS_NEW_BUSINESS_ON_HOLD
        return None

    def _resolve_effective_supplier_status(
        self,
        relation: SupplierSiteRelation,
        computed_status: Optional[str],
    ) -> Optional[str]:
        active_override = self._extract_active_override_from_relation(
            relation=relation,
            computed_status=computed_status,
        )
        if active_override:
            return relation.supplier_status
        return computed_status

    def _extract_active_override_from_relation(
        self,
        relation: SupplierSiteRelation,
        computed_status: Optional[str],
    ) -> bool:
        return (
            relation.supplier_status not in (None, "")
            and computed_status not in (None, "")
            and relation.supplier_status != computed_status
        )

    def _build_status_override_payload(
        self,
        relation: SupplierSiteRelation,
        status_history: list[SupplierStatusHistory],
    ) -> Optional[dict[str, Any]]:
        computed_status = self._derive_supplier_status(relation.final_grade)
        if not self._extract_active_override_from_relation(relation, computed_status):
            return None
        for entry in status_history:
            reason = entry.change_reason or ""
            if reason.startswith(STATUS_OVERRIDE_MARKER):
                return {
                    "status": relation.supplier_status,
                    "reason": reason.replace(f"{STATUS_OVERRIDE_MARKER} ", "", 1),
                    "changed_at": entry.changed_at,
                    "changed_by": entry.changed_by,
                    "computed_status": computed_status,
                    "active": True,
                }
        return {
            "status": relation.supplier_status,
            "reason": None,
            "changed_at": relation.last_status_change,
            "changed_by": None,
            "computed_status": computed_status,
            "active": True,
        }

    @staticmethod
    def _resolve_existing_cycle_id(*instances: Any) -> Optional[int]:
        for instance in instances:
            cycle_id = getattr(instance, "id_cycle", None) if instance is not None else None
            if cycle_id is not None:
                return cycle_id
        return None

    def _merge_operational_values(
        self,
        previous_operational_input: Optional[OperationalEvaluationInput],
        data: Any,
    ) -> dict[str, Optional[Decimal]]:
        merged: dict[str, Optional[Decimal]] = {}
        for field_name in OPERATIONAL_SCORE_FIELDS:
            merged[field_name] = self._prefer_decimal(
                getattr(data, field_name, None),
                self._pluck(previous_operational_input, field_name),
            )
        return merged

    def _class_evaluation_changed(
        self,
        previous_input: Optional[PldClassEvaluationInput],
        merged_values: dict[str, Optional[str]],
        current_classification: Optional[Classification],
        impact_score: Optional[int],
        strategic_mention: Optional[str],
        panel_decision: Optional[str],
        previous_impact_input: Optional[ImpactEvaluationInput],
        data: schemas.ClassEvaluationUpdateRequest,
    ) -> bool:
        for field_name, value in merged_values.items():
            if self._pluck(previous_input, field_name) != value:
                return True
        if self._pluck(current_classification, "impact_score") != impact_score:
            return True
        if self._pluck(current_classification, "strategic_mention") != strategic_mention:
            return True
        if self._pluck(current_classification, "panel_decision") != panel_decision:
            return True
        for field_name in (
            "impact_question_1",
            "impact_question_2",
            "impact_question_3",
            "impact_question_4",
            "impact_question_5",
            "impact_question_6",
        ):
            new_value = getattr(data, field_name, None)
            previous_field_name = field_name.replace("impact_question_", "question_")
            if new_value is not None and self._pluck(previous_impact_input, previous_field_name) != new_value:
                return True
        return False

    def _operational_evaluation_changed(
        self,
        previous_operational_input: Optional[OperationalEvaluationInput],
        merged_operational_values: dict[str, Optional[Decimal]],
        operational_score: Optional[Decimal],
        operational_grade: Optional[str],
    ) -> bool:
        for field_name, value in merged_operational_values.items():
            if self._prefer_decimal(self._pluck(previous_operational_input, field_name)) != value:
                return True
        if self._prefer_decimal(self._pluck(previous_operational_input, "average_score")) != operational_score:
            return True
        if self._pluck(previous_operational_input, "operational_grade") != operational_grade:
            return True
        return False
