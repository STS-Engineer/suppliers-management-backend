"""Supplier relations service layer."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PANEL_ACTIVE_DECISIONS
from app.core.exceptions import AppException
from app.db.models import (
    Classification,
    Contact,
    Document,
    EvaluationCycle,
    ImpactEvaluationInput,
    OperationalEvaluationInput,
    PldClassCriteriaDetail,
    PldClassEvaluationInput,
    PldScoringRules,
    ScoreCard,
    SupplierGroup,
    SupplierCertification,
    SupplierDevelopmentPlan,
    SupplierUnit,
    AvocarbonSite,
    SupplierSiteRelation,
    SupplierStatusHistory,
)
from app.features.supplier_relations import schemas
from app.shared.utils.blob_storage import (
    get_fresh_doc_url,
    get_recovered_blob_url,
    upload_development_plan_document,
    upload_evaluation_document,
)
from app.shared.utils.email.email_service import send_email
from app.shared.utils.blob_storage import delete_blob

logger = logging.getLogger(__name__)

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

# cycle_type values representing an ad-hoc correction rather than a genuine
# periodic scorecard review -- these must not advance next_evaluation_date.
AD_HOC_CYCLE_TYPES = {"Criteria Change Review", "Expired Criteria Reset"}

STATUS_CAN_QUOTE_AND_BE_AWARDED = "Can Quote and Be Awarded"
STATUS_CAN_QUOTE_NOT_BE_AWARDED = "Can Quote but Not be Awarded"
STATUS_NEW_BUSINESS_ON_HOLD = "New business on Hold"
STATUS_OVERRIDE_MARKER = "[STATUS_OVERRIDE]"
DEVELOPMENT_PLAN_MARKER = "[DEVELOPMENT_PLAN]"
PLAN_STATUS_MUST_BE_SEND = "Must be send"
PLAN_STATUS_REQUEST_SENT = "Request sent"

CRITERIA_VALUE_NORMALIZATION = {
    "top": {
        # Canonical 29-tier table (see migration 20260707_0077) uses plain
        # "X days end of month" labels, no "or +"/"eom" suffix. Map every
        # historical spelling onto the matching canonical tier.
        "60 days end of month or +": "60 days end of month",
        "60 days eom or +": "60 days end of month",
        "60 days eom or+": "60 days end of month",
        "30 days end of month or +": "30 days end of month",
        "45 days end of month or +": "45 days end of month",
        "Cash in Advance": "Cash in advance",
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
        # Old slash variant → Monday canonical (period after M)
        "Signed M/Res/not sent": "Signed M.Res/not sent",
        "signed m/res/not sent": "Signed M.Res/not sent",
    },
    "family_coverage": {
        # Old long English → Monday short codes
        "Supplier can make all the family requirements": "100% Cov.",
        "Supplier can make the main family requirements": "Main sub-Fam Cov.",
        "Supplier can make only of few family requirements": "1 sub-F or refs Cov.",
        "Supplier can make 1 family requirements": "1 ref",
        # Old short aliases used during data loading
        "100% cov.": "100% Cov.",
        "Main Fam.": "Main sub-Fam Cov.",
        "main fam.": "Main sub-Fam Cov.",
        "1 Family": "1 ref",
        "1 family": "1 ref",
        "Few Fam.": "1 sub-F or refs Cov.",
        "few fam.": "1 sub-F or refs Cov.",
    },
    "geo_coverage": {
        "100% Cov.": "Main plants covered",
        "50% or +": "More than 50% plants are covered",
        "1 plant cov.": "1 plant is covered",
        "None": "None",
    },
    "cons_or_wd": {
        # Old canonical long form → Monday short code
        "Cons. Or Daily Deliveries": "Cons. or WD",
        "Cons. or daily deliveries": "Cons. or WD",
        "cons. or wd": "Cons. or WD",
        # Normalize lowercase d variant
        "Biweekly del.": "Biweekly Del.",
        "biweekly del.": "Biweekly Del.",
    },
    "quality_certification": {
        # Canonical scoring tiers (pld_scoring_rules.min_value):
        #   "IATF / ISO9001 (cat BCD)" = 100 | "ISO9001" = 50 | "None" = 0
        # Map every cert string the frontend can emit (CERT_TYPES_BY_STANDARD.quality)
        # to one of these tiers, or scoring silently treats it as 0.
        "IATF 16949:2016": "IATF / ISO9001 (cat BCD)",
        "ISO 9001 (cat BCD)": "IATF / ISO9001 (cat BCD)",
        "ISO9001 (cat BCD)": "IATF / ISO9001 (cat BCD)",
        "IS09001 (cat BCD)": "IATF / ISO9001 (cat BCD)",  # legacy: digit-zero typo
        "ISO 9001": "ISO9001",
        "ISO9001": "ISO9001",
        # ISO 13485 (medical-devices QMS) is treated as an ISO9001-equivalent tier.
        # Adjust if the business wants it scored differently.
        "ISO 13485": "ISO9001",
        "Distributor": "None",
        "None": "None",
    },
    "prod_lia_ins": {
        # Canonical tiers (see migration 20260707_0076): None / 500k€ or less /
        # 1M€ or less / 1,5M€ or less / 1,5M€ or more. Map every historical
        # spelling (old $ rows, old 2-tier € rows) onto the closest new tier.
        "2M€ or +": "1,5M€ or more",
        "1M€ or +": "1M€ or less",
        "2M$ or +": "1,5M€ or more",
        "1M$ or +": "1M€ or less",
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

    async def get_criteria_validity_bulk(self) -> list[dict[str, Any]]:
        """
        Returns criteria values + validity details for all relations in 4 DB queries.
        Powers the Criteria Validity Tracker page (replaces N individual workspace calls).
        """
        has_doc_col = await self._criteria_detail_has_document_column()

        # Q1 — latest PldClassEvaluationInput per relation, only panel-active approved relations
        approved_rel_ids_subq = (
            select(SupplierSiteRelation.id_relation)
            .where(SupplierSiteRelation.validation_status == "approved")
            .where(SupplierSiteRelation.panel_decision.in_(PANEL_ACTIVE_DECISIONS))
            .where(SupplierSiteRelation.is_active.is_(True))
            .where(SupplierSiteRelation.is_deleted.is_(False))
            .scalar_subquery()
        )
        latest_input_subq = (
            select(func.max(PldClassEvaluationInput.id_pld_input))
            .where(PldClassEvaluationInput.id_relation.in_(approved_rel_ids_subq))
            .group_by(PldClassEvaluationInput.id_relation)
            .scalar_subquery()
        )
        inputs_result = await self.db.execute(
            select(PldClassEvaluationInput).where(
                PldClassEvaluationInput.id_pld_input.in_(latest_input_subq)
            )
        )
        inputs_by_rel: dict[int, PldClassEvaluationInput] = {
            inp.id_relation: inp for inp in inputs_result.scalars().all()
        }

        # Q2 — latest pld_class_criteria_detail per (id_relation, criteria_type)
        base_cols = [
            "id_detail", "id_relation", "criteria_type",
            "evidence_file_name", "validity_start_date", "validity_end_date", "signature_date",
        ]
        if has_doc_col:
            base_cols.append("id_document")
        details_stmt = text(
            f"""
            SELECT {", ".join(base_cols)}
            FROM pld_class_criteria_detail
            WHERE id_detail IN (
                SELECT MAX(id_detail)
                FROM pld_class_criteria_detail
                GROUP BY id_relation, criteria_type
            )
            """
        )
        details_rows = (await self.db.execute(details_stmt)).mappings().all()

        # Q3 — batch-load documents for all detail rows that reference one
        doc_ids = [
            row["id_document"]
            for row in details_rows
            if has_doc_col and row.get("id_document")
        ]
        docs_by_id: dict[int, Document] = {}
        if doc_ids:
            docs_result = await self.db.execute(
                select(Document).where(Document.id_document.in_(doc_ids))
            )
            docs_by_id = {d.id_document: d for d in docs_result.scalars().all()}

        # Group criteria details by relation
        details_by_rel: dict[int, dict[str, dict[str, Any]]] = {}
        for row in details_rows:
            rel_id   = row["id_relation"]
            ctype    = row["criteria_type"]
            doc_id   = row.get("id_document") if has_doc_col else None
            doc      = docs_by_id.get(doc_id) if doc_id else None
            if not doc:
                doc = await self._find_criteria_document_fallback(
                    relation_id=rel_id,
                    criteria_type=ctype,
                    evidence_file_name=row.get("evidence_file_name"),
                )
                if doc:
                    doc_id = doc.id_document

            vsd = row["validity_start_date"]
            ved = row["validity_end_date"]
            sd  = row["signature_date"]

            recovered_url = None
            if not doc or not doc.file_url:
                recovered_url = self._recover_criteria_blob_url(
                    relation_id=rel_id,
                    criteria_type=ctype,
                    evidence_file_name=row.get("evidence_file_name"),
                )

            details_by_rel.setdefault(rel_id, {})[ctype] = {
                "validity_start_date": vsd.isoformat() if vsd else None,
                "validity_end_date":   ved.isoformat() if ved else None,
                "signature_date":      sd.isoformat()  if sd  else None,
                "evidence_file_name":  row["evidence_file_name"],
                "document_url":  get_fresh_doc_url(doc.file_url) if doc and doc.file_url else recovered_url,
                "document_name": doc.document_name if doc else row["evidence_file_name"],
            }

        # Build response — only relations that have at least one eval input.
        # Relations with no eval yet (no PldClassEvaluationInput) are excluded.
        all_rel_ids = sorted(inputs_by_rel.keys())

        # quality_certification is the one criterion backed by an independent,
        # separately-editable record (SupplierCertification). Never trust a stored/
        # copied value for it here -- always derive live from the relation's unit,
        # batched in one query, so a unit with N relations reports its cert's
        # expiry consistently instead of via N stale copies (the original bug).
        unit_by_rel: dict[int, int] = {}
        if all_rel_ids:
            unit_rows = await self.db.execute(
                select(SupplierSiteRelation.id_relation, SupplierSiteRelation.id_supplier_unit)
                .where(SupplierSiteRelation.id_relation.in_(all_rel_ids))
            )
            unit_by_rel = {rel_id: unit_id for rel_id, unit_id in unit_rows.all()}
        certs_by_unit = await self._get_best_certs_for_units(list(set(unit_by_rel.values())))

        result: list[dict[str, Any]] = []
        for rel_id in all_rel_ids:
            inp = inputs_by_rel.get(rel_id)
            unit_id = unit_by_rel.get(rel_id)
            scoring_cert, display_cert = certs_by_unit.get(unit_id, (None, None))
            quality_certification_detail = dict(details_by_rel.get(rel_id, {}))
            quality_certification_detail["quality_certification"] = {
                **quality_certification_detail.get("quality_certification", {}),
                "validity_start_date": display_cert.start_date.isoformat() if display_cert and display_cert.start_date else None,
                "validity_end_date": display_cert.end_date.isoformat() if display_cert and display_cert.end_date else None,
            }
            result.append({
                "rel_id": rel_id,
                "criteria_values": {
                    "top":                   self._pluck(inp, "top"),
                    "lta":                   self._pluck(inp, "lta"),
                    "productivity":          self._pluck(inp, "productivity"),
                    "quality_certification": self._certification_label(scoring_cert),
                    "prod_lia_ins":          self._normalize_criteria_value("prod_lia_ins",          self._pluck(inp, "prod_lia_ins")),
                    "competitiveness":       self._normalize_criteria_value("competitiveness",       self._pluck(inp, "competitiveness")),
                    "sqma":                  self._normalize_criteria_value("sqma",                  self._pluck(inp, "sqma")),
                    "family_coverage":       self._normalize_criteria_value("family_coverage",       self._pluck(inp, "family_coverage")),
                    "geo_coverage":          self._normalize_criteria_value("geo_coverage",          self._pluck(inp, "geo_coverage")),
                    "cons_or_wd":            self._normalize_criteria_value("cons_or_wd",            self._pluck(inp, "cons_or_wd")),
                    "financial_health":      self._pluck(inp, "financial_health"),
                },
                "class_criteria_details": quality_certification_detail,
            })
        return result

    async def get_relation_evaluation_workspace(
        self,
        relation_id: int,
    ) -> dict[str, Any]:
        relation = await self.get_relation(relation_id)
        class_input = await self._get_latest_class_input(relation_id)
        classification = await self._get_latest_classification(relation_id)
        operational_input = await self._get_latest_operational_input(relation_id)
        baseline_input = await self.get_self_assessment_baseline(relation_id)
        impact_input = await self._get_latest_impact_input(relation_id)
        status_history = await self._get_status_history(relation_id)
        criteria_details = await self._get_latest_criteria_details(relation_id)
        development_plans = await self.list_development_plans(relation_id)
        quality_certification = await self._get_relation_quality_certification(relation)

        # Unit certifications for quality cert pre-fill hint
        unit_certs_stmt = (
            select(SupplierCertification)
            .where(SupplierCertification.id_supplier_unit == relation.id_supplier_unit)
            .where(SupplierCertification.is_deleted.is_(False))
        )
        unit_certifications = list((await self.db.execute(unit_certs_stmt)).scalars().all())

        # Documents attached to this relation (eval reference + LTA + criterion evidence)
        eval_docs = await self.list_relation_documents_by_type(
            relation_id, ["evaluation_reference", "lta_agreement", "evaluation_criterion_evidence"]
        )

        # Per-criterion scores for live display. Raw (un-normalized) values are
        # fine here -- get_criteria_scores_breakdown() normalizes internally.
        class_values_for_scores = {
            "top": self._pluck(class_input, "top") or self._pluck(relation, "top"),
            "lta": self._pluck(class_input, "lta") or self._pluck(relation, "lta"),
            "productivity": self._pluck(class_input, "productivity") or self._pluck(relation, "productivity"),
            "quality_certification": quality_certification,
            "prod_lia_ins": self._pluck(class_input, "prod_lia_ins") or self._pluck(relation, "prod_lia_ins"),
            "competitiveness": self._pluck(class_input, "competitiveness") or self._pluck(relation, "competitiveness"),
            "sqma": self._pluck(class_input, "sqma") or self._pluck(relation, "sqma"),
            "family_coverage": self._pluck(class_input, "family_coverage") or self._pluck(relation, "family_coverage"),
            "geo_coverage": self._pluck(class_input, "geo_coverage") or self._pluck(relation, "geo_coverage"),
            "cons_or_wd": self._pluck(class_input, "cons_or_wd") or self._pluck(relation, "cons_or_wd"),
            "financial_health": self._pluck(class_input, "financial_health") or self._pluck(relation, "financial_health"),
        }
        criteria_scores = await self.get_criteria_scores_breakdown(class_values_for_scores)

        # Load the unit to get its supplier_name (readable name)
        unit = await self.db.get(SupplierUnit, relation.id_supplier_unit)

        # Determine if inactivity requires an initial or preliminary re-evaluation
        reevaluation_type: str | None = None
        if unit and not unit.is_active and unit.inactivated_at:
            inactivity_days = (datetime.utcnow() - unit.inactivated_at).days
            if inactivity_days >= 3 * 365:
                reevaluation_type = "initial"      # 3+ years → full initial re-eval
            elif inactivity_days >= 365:
                reevaluation_type = "preliminary"  # 1–3 years → preliminary re-eval

        # When re-evaluation is required the baseline is considered unlocked so the
        # operator can submit a fresh initial/preliminary self-assessment.
        effective_baseline_locked = (baseline_input is not None) and (reevaluation_type is None)

        return {
            "relation": relation,
            "unit_supplier_name": unit.supplier_name if unit else None,
            "unit_is_active": unit.is_active if unit else True,
            "unit_inactivated_at": unit.inactivated_at.isoformat() if unit and unit.inactivated_at else None,
            "reevaluation_type": reevaluation_type,
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
            "development_plans": development_plans,
            "class_criteria_details": criteria_details,
            "comments": relation.evaluation_comments,
            # Baseline lock — False when reevaluation is needed regardless of prior history
            "baseline_locked": effective_baseline_locked,
            "baseline_data": {
                "management_system": float(baseline_input.management_system) if baseline_input and baseline_input.management_system is not None else None,
                "customer_communication": float(baseline_input.customer_communication) if baseline_input and baseline_input.customer_communication is not None else None,
                "development_design": float(baseline_input.development_design) if baseline_input and baseline_input.development_design is not None else None,
                "production_manufacturing": float(baseline_input.production_manufacturing) if baseline_input and baseline_input.production_manufacturing is not None else None,
                "quality_audits": float(baseline_input.quality_audits) if baseline_input and baseline_input.quality_audits is not None else None,
                "suppliers_subcontractors": float(baseline_input.suppliers_subcontractors) if baseline_input and baseline_input.suppliers_subcontractors is not None else None,
                "deliveries": float(baseline_input.deliveries) if baseline_input and baseline_input.deliveries is not None else None,
                "environment_ethic_rules": float(baseline_input.environment_ethic_rules) if baseline_input and baseline_input.environment_ethic_rules is not None else None,
                "average_score": float(baseline_input.average_score) if baseline_input and baseline_input.average_score is not None else None,
                "operational_grade": baseline_input.operational_grade if baseline_input else None,
            } if baseline_input else None,
            # Unit certifications (for quality cert pre-fill hint)
            "unit_certifications": [
                {
                    "id_certification": c.id_certification,
                    "standard_type": c.standard_type,
                    "certification_type": c.certification_type,
                    "certificate_name": c.certificate_name,
                    "start_date": c.start_date.isoformat() if c.start_date else None,
                    "end_date": c.end_date.isoformat() if c.end_date else None,
                }
                for c in unit_certifications
            ],
            # Documents (evaluation reference + LTA)
            "evaluation_documents": [
                {
                    "id_document": d.id_document,
                    "document_type": d.document_type,
                    "document_name": d.document_name,
                    "file_url": get_fresh_doc_url(d.file_url) if d.file_url else None,
                    "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                    "uploaded_by": d.uploaded_by,
                }
                for d in eval_docs
            ],
            # Per-criterion scores for live class scoring display
            "criteria_scores": criteria_scores,
            "impact_score": self._pluck(classification, "impact_score"),
            "class_value": relation.class_value,
            "class_score": self._pluck(classification, "classification_score"),
            "operational_grade": relation.operational_grade,
            "operational_score": self._pluck(classification, "operational_score"),
            "strategic_mention": relation.strategic_mention,
            "panel_decision": relation.panel_decision,
            "top": self._pluck(class_input, "top") or self._pluck(relation, "top"),
            "lta": self._pluck(class_input, "lta") or self._pluck(relation, "lta"),
            "sqma": self._pluck(class_input, "sqma") or self._normalize_criteria_value("sqma", self._pluck(relation, "sqma")),
            "quality_certification": quality_certification,
            "family_coverage": self._pluck(class_input, "family_coverage") or self._normalize_criteria_value("family_coverage", self._pluck(relation, "family_coverage")),
            "competitiveness": self._normalize_criteria_value(
                "competitiveness",
                self._pluck(class_input, "competitiveness") or self._pluck(relation, "competitiveness"),
            ),
            "geo_coverage": self._normalize_criteria_value(
                "geo_coverage",
                self._pluck(class_input, "geo_coverage") or self._pluck(relation, "geo_coverage"),
            ),
            "cons_or_wd": self._pluck(class_input, "cons_or_wd") or self._normalize_criteria_value("cons_or_wd", self._pluck(relation, "cons_or_wd")),
            "financial_health": self._pluck(class_input, "financial_health") or self._pluck(relation, "financial_health"),
            "prod_lia_ins": self._pluck(class_input, "prod_lia_ins") or self._normalize_criteria_value("prod_lia_ins", self._pluck(relation, "prod_lia_ins")),
            "prod": self._pluck(class_input, "productivity") or self._pluck(relation, "productivity"),
            "management_system": self._pluck(operational_input, "management_system"),
            "customer_communication": self._pluck(
                operational_input, "customer_communication"
            ),
            "development_design": self._pluck(operational_input, "development_design"),
            "production_manufacturing": self._pluck(
                operational_input, "production_manufacturing"
            ),
            "quality_audits": self._pluck(operational_input, "quality_audits"),
            "suppliers_subcontractors": self._pluck(
                operational_input, "suppliers_subcontractors"
            ),
            "deliveries": self._pluck(operational_input, "deliveries"),
            "environment_ethic_rules": self._pluck(
                operational_input, "environment_ethic_rules"
            ),
            "impact_question_1": self._pluck(impact_input, "question_1"),
            "impact_question_2": self._pluck(impact_input, "question_2"),
            "impact_question_3": self._pluck(impact_input, "question_3"),
            "impact_question_4": self._pluck(impact_input, "question_4"),
            "impact_question_5": self._pluck(impact_input, "question_5"),
            "impact_question_6": self._pluck(impact_input, "question_6"),
            "evaluation_draft": relation.evaluation_draft,
            "relation_validation_status": relation.validation_status or "draft",
            "review_comment": relation.review_comment,
        }

    async def submit_relation_for_review(self, relation_id: int) -> None:
        relation = await self.get_relation(relation_id)
        if relation.validation_status == "approved":
            raise AppException("This relation is already approved.", status_code=400)
        if relation.validation_status == "pending_review":
            raise AppException("Already submitted for review.", status_code=400)
        relation.validation_status = "pending_review"
        await self.db.flush()  # defer commit to router so submitter stamp + notifications are atomic

    async def approve_relation_review(self, relation_id: int) -> None:
        relation = await self.get_relation(relation_id)
        # "draft" is allowed too: this endpoint is already restricted to vp_conversion
        # callers at the router level (see router.py's access_profile check), so a VP
        # Conversion approval is a direct, self-contained decision that doesn't need
        # to pass through the ordinary pending_review submission step first.
        if relation.validation_status not in ("draft", "pending_review"):
            raise AppException(
                f"Relation cannot be approved from '{relation.validation_status}' status.",
                status_code=400,
            )
        relation.validation_status = "approved"

    async def reject_relation_review(self, relation_id: int, comment: str | None) -> None:
        relation = await self.get_relation(relation_id)
        if relation.validation_status != "pending_review":
            raise AppException(
                f"Relation cannot be rejected from '{relation.validation_status}' status. "
                "Only relations under review (pending_review) can be rejected. "
                "To revise an already-approved relation, reset it to draft first.",
                status_code=400,
            )
        relation.validation_status = "rejected"

    async def save_evaluation_draft(
        self,
        relation_id: int,
        payload: dict | None,
    ) -> None:
        """Store raw evaluation form data as a draft — no business logic runs."""
        relation = await self.get_relation(relation_id)
        relation.evaluation_draft = payload
        await self.db.commit()

    async def get_evaluation_cycle_history(self, relation_id: int) -> list[dict]:
        """Full audit trail: all cycles with snapshots and field diffs."""
        cycles = list((await self.db.execute(
            select(EvaluationCycle)
            .where(EvaluationCycle.id_relation == relation_id)
            .where(EvaluationCycle.is_deleted.is_(False))
            .order_by(EvaluationCycle.launched_at.desc().nullslast(), EvaluationCycle.created_at.desc().nullslast())
        )).scalars().all())
        if not cycles:
            return []

        cycle_ids = [c.id_cycle for c in cycles]

        all_cls = list((await self.db.execute(
            select(Classification)
            .where(Classification.id_cycle.in_(cycle_ids))
            .where(Classification.is_deleted.is_(False))
            .order_by(Classification.entered_at.desc())
        )).scalars().all())
        cls_by_cycle: dict = {}
        for c in all_cls:
            if c.id_cycle and c.id_cycle not in cls_by_cycle:
                cls_by_cycle[c.id_cycle] = c

        all_pld = list((await self.db.execute(
            select(PldClassEvaluationInput)
            .where(PldClassEvaluationInput.id_cycle.in_(cycle_ids))
            .where(PldClassEvaluationInput.is_deleted.is_(False))
            .order_by(PldClassEvaluationInput.entered_at.desc())
            .options(selectinload(PldClassEvaluationInput.certification))
        )).scalars().all())
        pld_by_cycle: dict = {}
        for p in all_pld:
            if p.id_cycle and p.id_cycle not in pld_by_cycle:
                pld_by_cycle[p.id_cycle] = p

        all_op = list((await self.db.execute(
            select(OperationalEvaluationInput)
            .where(OperationalEvaluationInput.id_cycle.in_(cycle_ids))
            .where(OperationalEvaluationInput.is_deleted.is_(False))
            .order_by(OperationalEvaluationInput.entered_at.desc())
        )).scalars().all())
        op_by_cycle: dict = {}
        for o in all_op:
            if o.id_cycle and o.id_cycle not in op_by_cycle:
                op_by_cycle[o.id_cycle] = o

        CLASS_KEYS = ["top","lta","productivity","quality_certification","prod_lia_ins",
                      "competitiveness","sqma","family_coverage","geo_coverage","cons_or_wd","financial_health"]

        entries = []
        for cycle in cycles:
            cls = cls_by_cycle.get(cycle.id_cycle)
            pld = pld_by_cycle.get(cycle.id_cycle)
            op = op_by_cycle.get(cycle.id_cycle)

            class_criteria = {
                "top": pld.top, "lta": pld.lta, "productivity": pld.productivity,
                # Historical label as recorded at this cycle -- frozen, unlike the
                # "current status" views which always re-derive live from the unit.
                "quality_certification": self._certification_label(pld.certification), "prod_lia_ins": pld.prod_lia_ins,
                "competitiveness": pld.competitiveness, "sqma": pld.sqma,
                "family_coverage": pld.family_coverage, "geo_coverage": pld.geo_coverage,
                "cons_or_wd": pld.cons_or_wd, "financial_health": pld.financial_health,
                "class_score": float(pld.class_score) if pld.class_score is not None else None,
                "class_value": pld.class_value,
            } if pld else None

            op_scores = {
                "management_system": float(op.management_system) if op.management_system is not None else None,
                "customer_communication": float(op.customer_communication) if op.customer_communication is not None else None,
                "development_design": float(op.development_design) if op.development_design is not None else None,
                "production_manufacturing": float(op.production_manufacturing) if op.production_manufacturing is not None else None,
                "quality_audits": float(op.quality_audits) if op.quality_audits is not None else None,
                "suppliers_subcontractors": float(op.suppliers_subcontractors) if op.suppliers_subcontractors is not None else None,
                "deliveries": float(op.deliveries) if op.deliveries is not None else None,
                "environment_ethic_rules": float(op.environment_ethic_rules) if op.environment_ethic_rules is not None else None,
                "average_score": float(op.average_score) if op.average_score is not None else None,
                "operational_grade": op.operational_grade,
                "source_type": op.source_type,
            } if op else None

            cycle_date = (cycle.launched_at.isoformat() if cycle.launched_at
                          else cycle.created_at.isoformat() if cycle.created_at else None)
            submitted_by = (cycle.launched_by or (pld.entered_by if pld else None)
                            or (op.entered_by if op else None))

            entries.append({
                "cycle_id": cycle.id_cycle,
                "cycle_type": cycle.cycle_type,
                "cycle_date": cycle_date,
                "submitted_by": submitted_by,
                "class_value": cls.class_value if cls else (pld.class_value if pld else None),
                "operational_grade": cls.operational_grade if cls else (op.operational_grade if op else None),
                "final_grade": cls.final_grade if cls else None,
                "impact_score": cls.impact_score if cls else None,
                "panel_decision": cls.panel_decision if cls else None,
                "strategic_mention": cls.strategic_mention if cls else None,
                "class_criteria": class_criteria,
                "operational_scores": op_scores,
            })

        for i, entry in enumerate(entries):
            diffs: dict = {}
            curr_c = entry.get("class_criteria")
            previous_comparable = None
            if curr_c:
                for older_entry in entries[i + 1:]:
                    older_criteria = older_entry.get("class_criteria")
                    if older_criteria:
                        previous_comparable = older_criteria
                        break

            if curr_c and previous_comparable:
                for field in CLASS_KEYS:
                    cv, pv = curr_c.get(field), previous_comparable.get(field)
                    if cv != pv and (cv or pv):
                        diffs[field] = {"from": pv, "to": cv}
            entry["class_criteria_diffs"] = diffs

        return entries

    async def list_development_plans(
        self,
        relation_id: int,
    ) -> list[dict[str, Any]]:
        await self.get_relation(relation_id)
        stmt = (
            select(SupplierDevelopmentPlan)
            .where(SupplierDevelopmentPlan.id_relation == relation_id)
            .options(selectinload(SupplierDevelopmentPlan.document))
            .order_by(
                SupplierDevelopmentPlan.issue_date.desc().nullslast(),
                SupplierDevelopmentPlan.id_development_plan.desc(),
            )
        )
        result = await self.db.execute(stmt)
        return [
            self._serialize_development_plan(plan) for plan in result.scalars().all()
        ]

    async def create_development_plan(
        self,
        relation_id: int,
        data: schemas.SupplierDevelopmentPlanCreateRequest,
    ) -> SupplierDevelopmentPlan:
        relation = await self.get_relation(relation_id)
        payload = data.model_dump(
            exclude={"sync_relation_hold_status", "changed_by"},
            exclude_unset=True,
        )
        plan = SupplierDevelopmentPlan(
            id_relation=relation_id,
            **payload,
        )
        self.db.add(plan)
        await self.db.flush()

        if data.sync_relation_hold_status and data.business_hold_active is not None:
            await self._apply_development_plan_hold_status(
                relation=relation,
                plan=plan,
                changed_by=data.changed_by,
            )

        await self.db.commit()
        await self.db.refresh(plan)
        return plan

    async def upload_development_plan_file(
        self,
        relation_id: int,
        plan_id: int,
        file: Any,
        uploaded_by: Optional[str],
        comments: Optional[str] = None,
    ) -> Document:
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )

        upload = await upload_development_plan_document(
            file=file,
            relation_id=relation_id,
            plan_id=plan_id,
        )

        document = Document(
            id_relation=relation_id,
            id_supplier_unit=relation.id_supplier_unit,
            id_development_plan=plan_id,
            document_type="supplier_development_plan",
            document_name=plan.plan_title or f"Development Plan {plan_id}",
            original_file_name=upload["filename"],
            file_url=upload["file_url"],
            mime_type=upload["mimetype"],
            file_size=Decimal(str(upload["size"])),
            uploaded_by=uploaded_by or "SYSTEM",
            comments=comments or "Development plan document uploaded.",
            storage_provider="azure_blob",
            storage_object_key=upload["blob_name"],
        )
        self.db.add(document)
        await self.db.flush()

        # Keep plan's primary reference pointing to the latest upload.
        # Previous documents are retained (not deleted) so the full history
        # is accessible via get_plan_documents().
        plan.id_document = document.id_document
        plan.file_name = upload["filename"]
        plan.file_url = upload["file_url"]
        if comments:
            plan.file_notes = comments
        plan.updated_at = datetime.now()
        plan.updated_by = uploaded_by or "SYSTEM"

        await self.db.commit()
        await self.db.refresh(document)
        return document

    async def get_plan_documents(
        self,
        relation_id: int,
        plan_id: int,
    ) -> list[Document]:
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )
        stmt = (
            select(Document)
            .where(Document.id_development_plan == plan_id)
            .order_by(Document.uploaded_at.asc().nullslast(), Document.id_document.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def delete_plan_document(
        self,
        relation_id: int,
        plan_id: int,
        document_id: int,
    ) -> None:
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )
        document = await self.db.get(Document, document_id)
        if not document or document.id_development_plan != plan_id:
            raise AppException(
                f"Document {document_id} not found on this plan.",
                status_code=404,
            )
        if document.storage_object_key:
            try:
                await delete_blob(document.storage_object_key)
            except Exception:
                pass
        # If this was the primary document on the plan, reset the pointer.
        if plan.id_document == document_id:
            remaining = await self.get_plan_documents(relation_id, plan_id)
            remaining = [d for d in remaining if d.id_document != document_id]
            if remaining:
                latest = remaining[-1]
                plan.id_document = latest.id_document
                plan.file_name = latest.original_file_name
                plan.file_url = latest.file_url
            else:
                plan.id_document = None
                plan.file_name = None
                plan.file_url = None
            plan.updated_at = datetime.now()
        await self.db.delete(document)
        await self.db.commit()

    async def send_plan_received_notification(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanReceivedNotificationRequest,
    ) -> None:
        from app.shared.utils.email.email_service import send_email_with_attachments

        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )

        to_recipients = [e.strip() for e in data.to_emails if e.strip() and "@" in e]
        if not to_recipients:
            raise AppException("At least one valid recipient email is required.", status_code=400)
        cc_recipients = [e.strip() for e in (data.extra_cc_emails or []) if e.strip() and "@" in e]

        # Fetch site / supplier info
        site_stmt = select(AvocarbonSite).where(AvocarbonSite.id_site == relation.id_site)
        site = (await self.db.execute(site_stmt)).scalars().first()
        site_name = site.site_name if site and site.site_name else f"Site #{relation.id_site}"

        unit_stmt = select(SupplierUnit).where(SupplierUnit.id_supplier_unit == relation.id_supplier_unit)
        unit = (await self.db.execute(unit_stmt)).scalars().first()
        group_name: Optional[str] = None
        if unit and unit.id_group:
            group_stmt = select(SupplierGroup).where(SupplierGroup.id_group == unit.id_group)
            group = (await self.db.execute(group_stmt)).scalars().first()
            if group:
                group_name = group.nom
        supplier_display = group_name or (unit.supplier_name if unit else None) or f"Unit #{relation.id_supplier_unit}"

        relation_code = relation.relation_code or f"REL-{relation.id_relation:06d}"
        plan_title = plan.plan_title or f"Development Plan #{plan_id}"
        received_date = (plan.submission_date or date.today()).strftime("%d %B %Y")
        custom_message = (data.custom_message or "").strip()

        # Build document rows for email body
        documents = await self.get_plan_documents(relation_id, plan_id)
        doc_rows_html = ""
        for doc in documents:
            fresh_url = get_fresh_doc_url(doc.file_url) if doc.file_url else None
            if fresh_url:
                label = doc.original_file_name or f"Document #{doc.id_document}"
                note = f" <span style='color:#94a3b8;font-size:12px;'>— {doc.comments}</span>" if doc.comments and doc.comments != "Development plan document uploaded." else ""
                doc_rows_html += f"""
                <tr>
                  <td style="padding:8px 16px;border-bottom:1px solid #f1f5f9;">
                    <a href="{fresh_url}" style="color:#062B49;font-weight:600;font-size:13px;text-decoration:none;">
                      &#128196; {label}
                    </a>{note}
                  </td>
                </tr>"""

        docs_section = f"""
        <tr>
          <td style="padding:0 32px 24px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">
              <thead>
                <tr style="background:#f8fafc;">
                  <th style="padding:10px 16px;text-align:left;font-size:10px;font-weight:700;
                             letter-spacing:0.14em;text-transform:uppercase;color:#64748b;
                             border-bottom:1px solid #e2e8f0;">
                    Attached Documents ({len(documents)})
                  </th>
                </tr>
              </thead>
              <tbody>{doc_rows_html}</tbody>
            </table>
          </td>
        </tr>""" if documents else ""

        supplier_desc_section = ""
        if plan.supplier_comments:
            supplier_desc_section = f"""
        <tr>
          <td style="padding:0 32px 24px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;">
              <tr>
                <td style="padding:16px 20px;">
                  <p style="margin:0 0 6px;font-size:11px;font-weight:700;
                            letter-spacing:0.08em;text-transform:uppercase;color:#166534;">
                    Supplier's Action Description
                  </p>
                  <p style="margin:0;font-size:14px;line-height:1.7;color:#14532d;">
                    {plan.supplier_comments}
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

        custom_block = f"""
        <tr>
          <td style="padding:0 32px 24px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
              <tr>
                <td style="padding:16px 20px;">
                  <p style="margin:0 0 4px;font-size:11px;font-weight:700;
                            letter-spacing:0.08em;text-transform:uppercase;color:#92400e;">
                    Note
                  </p>
                  <p style="margin:0;font-size:14px;line-height:1.6;color:#78350f;">{custom_message}</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>""" if custom_message else ""

        subject = f"[Received] Supplier Action Plan — {supplier_display} · {site_name}"
        body_html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/><title>Action Plan Received</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f1f5f9;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;width:100%;background:#fff;border-radius:16px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(15,39,68,0.10);">

        <tr>
          <td style="background:linear-gradient(135deg,#065f46 0%,#059669 100%);padding:32px 32px 28px;">
            <p style="margin:0;font-size:13px;font-weight:700;letter-spacing:0.18em;
                       text-transform:uppercase;color:rgba(255,255,255,0.55);">
              Avocarbon · Supplier Management
            </p>
            <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;color:#fff;line-height:1.3;">
              {supplier_display}
            </h1>
            <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.70);">
              Action Plan Received &nbsp;·&nbsp; {site_name}
            </p>
          </td>
        </tr>

        <tr>
          <td style="background:#059669;padding:10px 32px;">
            <p style="margin:0;font-size:12px;font-weight:700;letter-spacing:0.12em;
                       text-transform:uppercase;color:#fff;">
              &#10003;&nbsp; Action Plan Received &nbsp;·&nbsp; {received_date} &nbsp;·&nbsp; {relation_code}
            </p>
          </td>
        </tr>

        <tr>
          <td style="padding:28px 32px 20px;">
            <p style="margin:0;font-size:15px;line-height:1.7;color:#334155;">
              The supplier <strong style="color:#065f46;">{supplier_display}</strong> has submitted
              their action plan for the development plan
              <strong style="color:#062B49;">{plan_title}</strong>
              at the <strong>{site_name}</strong> plant.
            </p>
          </td>
        </tr>

        {docs_section}
        {supplier_desc_section}
        {custom_block}

        <tr>
          <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 32px;">
            <p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;text-align:center;">
              Sent automatically by the Avocarbon Supplier Management platform.
            </p>
            <p style="margin:8px 0 0;font-size:11px;color:#cbd5e1;text-align:center;letter-spacing:0.06em;">
              AVOCARBON &nbsp;·&nbsp; Supplier Management
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body></html>"""

        # Download and attach all plan documents (best effort)
        import os
        import tempfile
        import urllib.request as _urllib_req

        def _download(url: str, fname: str) -> Optional[str]:
            try:
                suffix = ("." + fname.rsplit(".", 1)[-1]) if fname and "." in fname else ""
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                with _urllib_req.urlopen(url, timeout=20) as resp:
                    tmp.write(resp.read())
                tmp.close()
                return tmp.name
            except Exception:
                return None

        attachment_list: list[dict] = []
        temp_paths: list[str] = []
        for doc in documents:
            fresh_url = get_fresh_doc_url(doc.file_url) if doc.file_url else None
            if fresh_url:
                fname = doc.original_file_name or f"document_{doc.id_document}"
                path = await asyncio.to_thread(_download, fresh_url, fname)
                if path:
                    attachment_list.append({"path": path, "filename": fname})
                    temp_paths.append(path)

        try:
            if attachment_list:
                await send_email_with_attachments(
                    subject=subject,
                    recipients=to_recipients,
                    cc=cc_recipients or None,
                    body_html=body_html,
                    attachments=attachment_list,
                    db=None,
                )
            else:
                from app.shared.utils.email.email_service import send_email as _send
                await _send(subject=subject, recipients=to_recipients, cc=cc_recipients or None, body_html=body_html, db=None)
        finally:
            for p in temp_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def list_development_plan_register(
        self,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(
                SupplierDevelopmentPlan,
                SupplierSiteRelation,
                SupplierUnit,
                SupplierGroup,
                AvocarbonSite,
            )
            .join(
                SupplierSiteRelation,
                SupplierSiteRelation.id_relation == SupplierDevelopmentPlan.id_relation,
            )
            .join(
                SupplierUnit,
                SupplierUnit.id_supplier_unit == SupplierSiteRelation.id_supplier_unit,
            )
            .join(
                SupplierGroup,
                SupplierGroup.id_group == SupplierUnit.id_group,
                isouter=True,
            )
            .join(
                AvocarbonSite,
                AvocarbonSite.id_site == SupplierSiteRelation.id_site,
            )
            .where(SupplierSiteRelation.panel_decision.in_(PANEL_ACTIVE_DECISIONS))
            .where(SupplierSiteRelation.validation_status == "approved")
            .where(SupplierSiteRelation.is_active.is_(True))
            .where(SupplierSiteRelation.is_deleted.is_(False))
            .options(selectinload(SupplierDevelopmentPlan.document))
            .order_by(
                SupplierDevelopmentPlan.due_date.asc().nullslast(),
                SupplierDevelopmentPlan.id_development_plan.desc(),
            )
        )
        result = await self.db.execute(stmt)
        raw_rows = result.all()

        # Batch-load all documents for the found plans in a single query.
        plan_ids = [plan.id_development_plan for plan, *_ in raw_rows]
        docs_by_plan: dict[int, list[Document]] = {}
        if plan_ids:
            doc_stmt = (
                select(Document)
                .where(Document.id_development_plan.in_(plan_ids))
                .order_by(Document.uploaded_at.asc().nullslast(), Document.id_document.asc())
            )
            doc_result = await self.db.execute(doc_stmt)
            for doc in doc_result.scalars().all():
                docs_by_plan.setdefault(doc.id_development_plan, []).append(doc)

        rows = []
        for plan, relation, unit, group, site in raw_rows:
            plan_docs = docs_by_plan.get(plan.id_development_plan, [])
            serialized_docs = [
                {
                    "id_document": d.id_document,
                    "file_name": d.original_file_name,
                    "file_url": get_fresh_doc_url(d.file_url) if d.file_url else None,
                    "file_notes": d.comments if d.comments != "Development plan document uploaded." else None,
                    "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                }
                for d in plan_docs
            ]
            rows.append(
                {
                    "relation": relation,
                    "development_plan": self._serialize_development_plan(plan),
                    "documents": serialized_docs,
                    "site_name": site.site_name,
                    "site_city": site.city,
                    "site_country": site.country,
                    "unit_supplier_name": unit.supplier_name,
                    "unit_code": unit.unit_code,
                    "group_id": group.id_group if group else None,
                    "group_name": group.nom if group else None,
                    "group_code": group.group_code if group else None,
                }
            )
        return rows

    async def send_development_plan_request(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanSendRequest,
    ) -> SupplierDevelopmentPlan:
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )
        if not plan.due_date:
            raise AppException(
                "A due date is required before sending the development plan request email.",
                status_code=400,
            )

        if data.to_emails:
            to_recipients = [
                e.strip() for e in data.to_emails if e.strip() and "@" in e
            ]
            cc_recipients = [
                e.strip()
                for e in (data.extra_cc_emails or [])
                if e.strip() and "@" in e
            ]
        else:
            (
                to_recipients,
                cc_recipients,
            ) = await self._resolve_development_plan_email_targets(relation)
            for extra in data.extra_cc_emails or []:
                extra = extra.strip()
                if (
                    extra
                    and "@" in extra
                    and extra not in to_recipients
                    and extra not in cc_recipients
                ):
                    cc_recipients.append(extra)
        if not to_recipients:
            raise AppException(
                "No supplier recipient email was found for this relation.",
                status_code=400,
            )

        site_stmt = select(AvocarbonSite).where(
            AvocarbonSite.id_site == relation.id_site
        )
        site_result = await self.db.execute(site_stmt)
        site = site_result.scalars().first()
        site_name = (
            site.site_name if site and site.site_name else f"Site #{relation.id_site}"
        )
        site_location = ""
        if site:
            parts = [p for p in (site.city, site.country) if p]
            if parts:
                site_location = ", ".join(parts)

        unit_stmt = select(SupplierUnit).where(
            SupplierUnit.id_supplier_unit == relation.id_supplier_unit
        )
        unit_result = await self.db.execute(unit_stmt)
        unit = unit_result.scalars().first()
        group_name: Optional[str] = None
        if unit and unit.id_group:
            group_stmt = select(SupplierGroup).where(
                SupplierGroup.id_group == unit.id_group
            )
            group_result = await self.db.execute(group_stmt)
            group = group_result.scalars().first()
            if group:
                group_name = group.nom
        supplier_display = (
            group_name
            or (unit.supplier_name if unit else None)
            or f"Unit #{relation.id_supplier_unit}"
        )

        relation_code = relation.relation_code or f"REL-{relation.id_relation:06d}"
        plan_title = plan.plan_title or f"Development Plan #{plan.id_development_plan}"
        due_date_str = plan.due_date.strftime("%d %B %Y")
        issue_date_str = (plan.issue_date or date.today()).strftime("%d %B %Y")
        subject = f"[Action Required] Supplier Development Plan — {supplier_display} · {site_name}"
        custom_message = (data.custom_message or "").strip()
        custom_message_block = (
            f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 4px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#92400e;">
                        Message from Avocarbon Purchasing
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.6;color:#78350f;">
                        {custom_message}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            """
            if custom_message
            else ""
        )
        body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Supplier Development Plan Request</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">

  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#f1f5f9;padding:32px 16px;">
    <tr>
      <td align="center">

        <!-- Card -->
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;background:#ffffff;
                      border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(15,39,68,0.10);">

          <!-- ── Header ── -->
          <tr>
            <td style="background:linear-gradient(135deg,#062B49 0%,#0c4a6e 100%);
                        padding:32px 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:13px;font-weight:700;
                               letter-spacing:0.18em;text-transform:uppercase;
                               color:rgba(255,255,255,0.55);">
                      Avocarbon · Supplier Management
                    </p>
                    <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;
                                color:#ffffff;line-height:1.3;">
                      {supplier_display}
                    </h1>
                    <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.70);">
                      Supplier Development Plan Request &nbsp;·&nbsp; {site_name}
                    </p>
                  </td>
                  <td align="right" valign="top" style="padding-top:4px;white-space:nowrap;">
                    <span style="display:inline-block;background:rgba(255,255,255,0.12);
                                  border:1px solid rgba(255,255,255,0.20);
                                  border-radius:6px;padding:6px 14px;
                                  font-size:12px;font-weight:700;
                                  color:#ffffff;letter-spacing:0.04em;">
                      {site_name}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── Action-required banner ── -->
          <tr>
            <td style="background:#f59e0b;padding:10px 32px;">
              <p style="margin:0;font-size:12px;font-weight:700;
                         letter-spacing:0.12em;text-transform:uppercase;
                         color:#ffffff;">
                &#9888;&#xFE0F;&nbsp; Action Required &nbsp;·&nbsp; {supplier_display} &nbsp;&#8594;&nbsp; {site_name} &nbsp;·&nbsp; Due {due_date_str}
              </p>
            </td>
          </tr>

          <!-- ── Greeting ── -->
          <tr>
            <td style="padding:28px 32px 8px;">
              <p style="margin:0;font-size:15px;line-height:1.7;color:#1e293b;">
                Dear <strong style="color:#062B49;">{supplier_display}</strong>,
              </p>
              <p style="margin:12px 0 0;font-size:15px;line-height:1.7;color:#334155;">
                Following a recent evaluation of your collaboration with the
                <strong style="color:#062B49;">{site_name}</strong> plant,
                a supplier development plan is required. Please review the details below
                and submit your completed plan before the due date indicated.
              </p>
            </td>
          </tr>

          <!-- ── Details table ── -->
          <tr>
            <td style="padding:20px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="border:1px solid #e2e8f0;border-radius:10px;
                             overflow:hidden;font-size:13px;">
                <thead>
                  <tr style="background:#f8fafc;">
                    <th colspan="2"
                        style="padding:10px 16px;text-align:left;
                               font-size:10px;font-weight:700;
                               letter-spacing:0.14em;text-transform:uppercase;
                               color:#64748b;border-bottom:1px solid #e2e8f0;">
                      Plan Details
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;
                                width:38%;white-space:nowrap;">
                      Supplier
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;font-weight:700;">
                      {supplier_display}
                    </td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Avocarbon Plant
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;font-weight:600;">
                      {site_name}{f' &nbsp;<span style="color:#94a3b8;font-weight:400;">({site_location})</span>' if site_location else ""}
                    </td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Plan Title
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;">
                      {plan_title}
                    </td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Reference
                    </td>
                    <td style="padding:11px 16px;color:#64748b;font-family:monospace;
                                font-size:12px;">
                      {relation_code}
                    </td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Issue Date
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;">
                      {issue_date_str}
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Submission Due
                    </td>
                    <td style="padding:11px 16px;">
                      <strong style="color:#dc2626;font-size:14px;">{due_date_str}</strong>
                    </td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>

          <!-- ── Custom message (conditional) ── -->
          {custom_message_block}

          <!-- ── What to do ── -->
          <tr>
            <td style="padding:0 32px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#eff6ff;border:1px solid #bfdbfe;
                             border-radius:10px;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 10px;font-size:13px;font-weight:700;
                               color:#1e40af;">
                      What you need to do
                    </p>
                    <table cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td valign="top"
                            style="padding:3px 10px 3px 0;font-size:14px;color:#3b82f6;">
                          &#10003;
                        </td>
                        <td style="padding:3px 0;font-size:13px;
                                    line-height:1.6;color:#1e3a8a;">
                          Prepare your supplier development plan document
                        </td>
                      </tr>
                      <tr>
                        <td valign="top"
                            style="padding:3px 10px 3px 0;font-size:14px;color:#3b82f6;">
                          &#10003;
                        </td>
                        <td style="padding:3px 0;font-size:13px;
                                    line-height:1.6;color:#1e3a8a;">
                          Submit it to your Avocarbon purchasing contact before
                          <strong>{due_date_str}</strong>
                        </td>
                      </tr>
                     
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── Deadline callout ── -->
          <tr>
            <td style="padding:0 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:linear-gradient(135deg,#062B49,#0c4a6e);
                             border-radius:10px;">
                <tr>
                  <td style="padding:18px 24px;" align="center">
                    <p style="margin:0;font-size:11px;font-weight:700;
                               letter-spacing:0.14em;text-transform:uppercase;
                               color:rgba(255,255,255,0.65);">
                      Submission Deadline
                    </p>
                    <p style="margin:4px 0 0;font-size:22px;font-weight:700;
                               color:#ffffff;">
                      {due_date_str}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- ── Footer ── -->
          <tr>
            <td style="background:#f8fafc;border-top:1px solid #e2e8f0;
                        padding:20px 32px;">
              <p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;
                          text-align:center;">
                This message was sent automatically by the Avocarbon Supplier Management
                platform.<br/>
                Please do not reply directly to this email — contact your Avocarbon
                purchasing representative for any questions.
              </p>
              <p style="margin:12px 0 0;font-size:11px;color:#cbd5e1;text-align:center;
                          letter-spacing:0.06em;">
                AVOCARBON &nbsp;·&nbsp; Supplier Management
              </p>
            </td>
          </tr>

        </table>
        <!-- /Card -->

      </td>
    </tr>
  </table>

</body>
</html>"""

        await send_email(
            subject=subject,
            recipients=to_recipients,
            cc=cc_recipients or None,
            body_html=body_html,
            db=None,
        )

        plan.plan_status = PLAN_STATUS_REQUEST_SENT
        if not plan.issue_date:
            plan.issue_date = date.today()
        plan.updated_at = datetime.now()
        plan.updated_by = data.changed_by or "SYSTEM"
        await self.db.commit()
        await self.db.refresh(plan)
        return plan

    async def send_development_plan_reminder(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanSendReminder,
    ) -> SupplierDevelopmentPlan:
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )
        if not plan.due_date:
            raise AppException(
                "A due date is required before sending a reminder.",
                status_code=400,
            )

        if data.to_emails:
            to_recipients = [e.strip() for e in data.to_emails if e.strip() and "@" in e]
            cc_recipients = [e.strip() for e in (data.extra_cc_emails or []) if e.strip() and "@" in e]
        else:
            to_recipients, cc_recipients = await self._resolve_development_plan_email_targets(relation)
            for extra in data.extra_cc_emails or []:
                extra = extra.strip()
                if extra and "@" in extra and extra not in to_recipients and extra not in cc_recipients:
                    cc_recipients.append(extra)
        if not to_recipients:
            raise AppException(
                "No supplier recipient email was found for this relation.",
                status_code=400,
            )

        site_stmt = select(AvocarbonSite).where(AvocarbonSite.id_site == relation.id_site)
        site = (await self.db.execute(site_stmt)).scalars().first()
        site_name = site.site_name if site and site.site_name else f"Site #{relation.id_site}"

        unit_stmt = select(SupplierUnit).where(SupplierUnit.id_supplier_unit == relation.id_supplier_unit)
        unit = (await self.db.execute(unit_stmt)).scalars().first()
        group_name: Optional[str] = None
        if unit and unit.id_group:
            group_stmt = select(SupplierGroup).where(SupplierGroup.id_group == unit.id_group)
            group = (await self.db.execute(group_stmt)).scalars().first()
            if group:
                group_name = group.nom
        supplier_display = (
            group_name
            or (unit.supplier_name if unit else None)
            or f"Unit #{relation.id_supplier_unit}"
        )

        today = date.today()
        due_date_str = plan.due_date.strftime("%d %B %Y")
        days_overdue = (today - plan.due_date).days if plan.due_date < today else 0
        plan_title = plan.plan_title or f"Development Plan #{plan.id_development_plan}"
        custom_message = (data.custom_message or "").strip()

        overdue_banner = ""
        if days_overdue > 0:
            overdue_banner = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;">
                  <tr>
                    <td style="padding:14px 20px;">
                      <p style="margin:0;font-size:13px;font-weight:700;color:#991b1b;">
                        ⚠ Overdue by {days_overdue} day{"s" if days_overdue != 1 else ""}
                      </p>
                      <p style="margin:4px 0 0;font-size:13px;color:#b91c1c;">
                        The deadline for submitting your action plan was {due_date_str}.
                        Immediate submission is required.
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        custom_message_block = ""
        if custom_message:
            custom_message_block = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 4px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#92400e;">
                        Message from Avocarbon Purchasing
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.6;color:#78350f;">
                        {custom_message}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        subject = f"[Reminder] Development Plan Submission — {supplier_display} · {site_name}"
        body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Development Plan Reminder</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#f1f5f9;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;background:#ffffff;
                      border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(15,39,68,0.10);">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#7c2d12 0%,#c2410c 100%);
                        padding:32px 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:13px;font-weight:700;
                               letter-spacing:0.18em;text-transform:uppercase;
                               color:rgba(255,255,255,0.55);">
                      Avocarbon · Supplier Management
                    </p>
                    <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;
                                color:#ffffff;line-height:1.3;">
                      {supplier_display}
                    </h1>
                    <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.70);">
                      Reminder: Development Plan Submission &nbsp;·&nbsp; {site_name}
                    </p>
                  </td>
                  <td align="right" valign="top" style="padding-top:4px;white-space:nowrap;">
                    <span style="display:inline-block;background:rgba(255,255,255,0.15);
                                  border:1px solid rgba(255,255,255,0.25);
                                  border-radius:6px;padding:6px 14px;
                                  font-size:12px;font-weight:700;
                                  color:#ffffff;letter-spacing:0.04em;">
                      REMINDER
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:28px 32px 8px;">
              <p style="margin:0;font-size:15px;line-height:1.7;color:#1e293b;">
                This is a reminder that your development action plan for
                <strong>{site_name}</strong> is awaiting submission.
                Avocarbon Purchasing has not yet received your plan.
              </p>
            </td>
          </tr>
          {overdue_banner}
          {custom_message_block}
          <!-- Plan details -->
          <tr>
            <td style="padding:0 32px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
                <tr>
                  <td style="padding:12px 20px;border-bottom:1px solid #e2e8f0;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td style="font-size:12px;color:#64748b;font-weight:600;">Plan</td>
                        <td align="right" style="font-size:13px;font-weight:600;color:#1e293b;">{plan_title}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px 20px;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td style="font-size:12px;color:#64748b;font-weight:600;">Deadline</td>
                        <td align="right">
                          <span style="font-size:13px;font-weight:700;
                                        color:{'#dc2626' if days_overdue > 0 else '#0f172a'};">
                            {due_date_str}{'  (overdue)' if days_overdue > 0 else ''}
                          </span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Call to action -->
          <tr>
            <td style="padding:0 32px 32px;">
              <p style="margin:0;font-size:14px;line-height:1.7;color:#475569;">
                Please submit your action plan as soon as possible.
                Contact Avocarbon Purchasing if you have any questions.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f8fafc;border-top:1px solid #e2e8f0;
                        padding:20px 32px;text-align:center;">
              <p style="margin:0;font-size:11px;color:#94a3b8;">
                This reminder was sent automatically by Avocarbon Supplier Management.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

        await send_email(
            subject=subject,
            recipients=to_recipients,
            cc=cc_recipients or None,
            body_html=body_html,
            db=None,
        )

        plan.updated_at = datetime.now()
        plan.updated_by = data.changed_by or "SYSTEM"
        await self.db.commit()
        await self.db.refresh(plan)
        return plan

    async def send_revision_request(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanRevisionRequest,
    ) -> SupplierDevelopmentPlan:
        """Send a revision-request email to the supplier after plan rejection."""
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )
        if not plan.due_date:
            raise AppException(
                "A due date is required before sending the revision request.",
                status_code=400,
            )

        if data.to_emails:
            to_recipients = [e.strip() for e in data.to_emails if e.strip() and "@" in e]
            cc_recipients = [e.strip() for e in (data.extra_cc_emails or []) if e.strip() and "@" in e]
        else:
            to_recipients, cc_recipients = await self._resolve_development_plan_email_targets(relation)
            for extra in data.extra_cc_emails or []:
                extra = extra.strip()
                if extra and "@" in extra and extra not in to_recipients and extra not in cc_recipients:
                    cc_recipients.append(extra)
        if not to_recipients:
            raise AppException(
                "No supplier recipient email was found for this relation.",
                status_code=400,
            )

        site_stmt = select(AvocarbonSite).where(AvocarbonSite.id_site == relation.id_site)
        site = (await self.db.execute(site_stmt)).scalars().first()
        site_name = site.site_name if site and site.site_name else f"Site #{relation.id_site}"

        unit_stmt = select(SupplierUnit).where(SupplierUnit.id_supplier_unit == relation.id_supplier_unit)
        unit = (await self.db.execute(unit_stmt)).scalars().first()
        group_name: Optional[str] = None
        if unit and unit.id_group:
            group_stmt = select(SupplierGroup).where(SupplierGroup.id_group == unit.id_group)
            group = (await self.db.execute(group_stmt)).scalars().first()
            if group:
                group_name = group.nom
        supplier_display = (
            group_name
            or (unit.supplier_name if unit else None)
            or f"Unit #{relation.id_supplier_unit}"
        )

        due_date_str = plan.due_date.strftime("%d %B %Y")
        plan_title = plan.plan_title or f"Development Plan #{plan.id_development_plan}"
        rejected_by = plan.rejected_by or "Avocarbon Committee"
        rejection_reason = (plan.internal_comments or "").strip()
        custom_message = (data.custom_message or "").strip()
        relation_code = relation.relation_code or f"REL-{relation.id_relation:06d}"

        rejection_block = ""
        if rejection_reason:
            rejection_block = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 6px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#991b1b;">
                        Reason for Rejection
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.6;color:#7f1d1d;">
                        {rejection_reason}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        custom_message_block = ""
        if custom_message:
            custom_message_block = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 4px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#92400e;">
                        Message from Avocarbon Purchasing
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.6;color:#78350f;">
                        {custom_message}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        subject = f"[Action Required] Development Plan Revision — {supplier_display} · {site_name}"
        body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Development Plan Revision Required</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#f1f5f9;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;background:#ffffff;
                      border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(15,39,68,0.10);">
          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#78350f 0%,#d97706 100%);
                        padding:32px 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:13px;font-weight:700;
                               letter-spacing:0.18em;text-transform:uppercase;
                               color:rgba(255,255,255,0.55);">
                      Avocarbon · Supplier Management
                    </p>
                    <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;
                                color:#ffffff;line-height:1.3;">
                      {supplier_display}
                    </h1>
                    <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.75);">
                      Plan Revision Required &nbsp;·&nbsp; {site_name}
                    </p>
                  </td>
                  <td align="right" valign="top" style="padding-top:4px;white-space:nowrap;">
                    <span style="display:inline-block;background:rgba(255,255,255,0.15);
                                  border:1px solid rgba(255,255,255,0.25);border-radius:6px;
                                  padding:6px 14px;font-size:12px;font-weight:700;
                                  color:#ffffff;letter-spacing:0.04em;">
                      REVISION
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Rejection banner -->
          <tr>
            <td style="background:#dc2626;padding:10px 32px;">
              <p style="margin:0;font-size:12px;font-weight:700;
                         letter-spacing:0.12em;text-transform:uppercase;color:#ffffff;">
                ✗&nbsp; Previous submission was not accepted &nbsp;·&nbsp; Rejected by {rejected_by}
              </p>
            </td>
          </tr>
          <!-- Greeting -->
          <tr>
            <td style="padding:28px 32px 16px;">
              <p style="margin:0;font-size:15px;line-height:1.7;color:#1e293b;">
                Dear <strong style="color:#062B49;">{supplier_display}</strong>,
              </p>
              <p style="margin:12px 0 0;font-size:15px;line-height:1.7;color:#334155;">
                The development plan you submitted for the
                <strong style="color:#062B49;">{site_name}</strong> plant has been reviewed
                by the Avocarbon committee and was <strong style="color:#dc2626;">not accepted</strong>.
                Please review the reason below and submit a revised plan before the new deadline.
              </p>
            </td>
          </tr>
          {rejection_block}
          <!-- Plan details -->
          <tr>
            <td style="padding:0 32px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;font-size:13px;">
                <thead>
                  <tr style="background:#f8fafc;">
                    <th colspan="2" style="padding:10px 16px;text-align:left;
                               font-size:10px;font-weight:700;letter-spacing:0.14em;
                               text-transform:uppercase;color:#64748b;
                               border-bottom:1px solid #e2e8f0;">
                      Plan Details
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;width:38%;">Supplier</td>
                    <td style="padding:11px 16px;color:#0f172a;font-weight:700;">{supplier_display}</td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">Plan</td>
                    <td style="padding:11px 16px;color:#0f172a;">{plan_title}</td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">Reference</td>
                    <td style="padding:11px 16px;color:#64748b;font-family:monospace;font-size:12px;">{relation_code}</td>
                  </tr>
                  <tr>
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">New Deadline</td>
                    <td style="padding:11px 16px;">
                      <strong style="color:#dc2626;font-size:14px;">{due_date_str}</strong>
                    </td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
          {custom_message_block}
          <!-- What to do -->
          <tr>
            <td style="padding:0 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 10px;font-size:13px;font-weight:700;color:#92400e;">
                      What you need to do
                    </p>
                    <table cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td valign="top" style="padding:3px 10px 3px 0;font-size:14px;color:#d97706;">&#10003;</td>
                        <td style="padding:3px 0;font-size:13px;line-height:1.6;color:#78350f;">
                          Review the reason for rejection above
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:3px 10px 3px 0;font-size:14px;color:#d97706;">&#10003;</td>
                        <td style="padding:3px 0;font-size:13px;line-height:1.6;color:#78350f;">
                          Prepare a revised development plan addressing the committee's feedback
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:3px 10px 3px 0;font-size:14px;color:#d97706;">&#10003;</td>
                        <td style="padding:3px 0;font-size:13px;line-height:1.6;color:#78350f;">
                          Submit the revised plan before <strong>{due_date_str}</strong>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 32px;text-align:center;">
              <p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;">
                This message was sent automatically by the Avocarbon Supplier Management platform.<br/>
                Please do not reply directly to this email — contact your Avocarbon purchasing representative.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

        await send_email(
            subject=subject,
            recipients=to_recipients,
            cc=cc_recipients or None,
            body_html=body_html,
            db=None,
        )

        plan.updated_at = datetime.now()
        plan.updated_by = data.changed_by or "SYSTEM"
        await self.db.commit()
        await self.db.refresh(plan)
        return plan

    async def send_decision_notification(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanDecisionNotification,
    ) -> None:
        """Notify the supplier of the committee's Approved or Rejected decision."""
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )

        if data.to_emails:
            to_recipients = [e.strip() for e in data.to_emails if e.strip() and "@" in e]
            cc_recipients = [e.strip() for e in (data.extra_cc_emails or []) if e.strip() and "@" in e]
        else:
            to_recipients, cc_recipients = await self._resolve_development_plan_email_targets(relation)
            for extra in data.extra_cc_emails or []:
                extra = extra.strip()
                if extra and "@" in extra and extra not in to_recipients and extra not in cc_recipients:
                    cc_recipients.append(extra)
        if not to_recipients:
            raise AppException(
                "No supplier recipient email was found for this relation.",
                status_code=400,
            )

        site_stmt = select(AvocarbonSite).where(AvocarbonSite.id_site == relation.id_site)
        site = (await self.db.execute(site_stmt)).scalars().first()
        site_name = site.site_name if site and site.site_name else f"Site #{relation.id_site}"

        unit_stmt = select(SupplierUnit).where(SupplierUnit.id_supplier_unit == relation.id_supplier_unit)
        unit = (await self.db.execute(unit_stmt)).scalars().first()
        group_name: Optional[str] = None
        if unit and unit.id_group:
            group_stmt = select(SupplierGroup).where(SupplierGroup.id_group == unit.id_group)
            group = (await self.db.execute(group_stmt)).scalars().first()
            if group:
                group_name = group.nom
        supplier_display = (
            group_name
            or (unit.supplier_name if unit else None)
            or f"Unit #{relation.id_supplier_unit}"
        )

        plan_title = plan.plan_title or f"Development Plan #{plan.id_development_plan}"
        relation_code = relation.relation_code or f"REL-{relation.id_relation:06d}"
        decision = (data.decision or "").strip().lower()
        is_approved = decision == "approved"
        decided_by = (
            plan.approved_by if is_approved else plan.rejected_by
        ) or "Avocarbon Committee"
        custom_message = (data.custom_message or "").strip()

        decision_date_str = ""
        decision_date_val = plan.decision_date or date.today()
        decision_date_str = decision_date_val.strftime("%d %B %Y")

        custom_message_block = ""
        if custom_message:
            custom_message_block = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 4px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#92400e;">
                        Message from Avocarbon Purchasing
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.6;color:#78350f;">
                        {custom_message}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        if is_approved:
            header_gradient = "linear-gradient(135deg,#14532d 0%,#16a34a 100%)"
            banner_bg = "#16a34a"
            banner_icon = "✓"
            banner_text = f"Plan Approved &nbsp;·&nbsp; {decided_by} &nbsp;·&nbsp; {decision_date_str}"
            greeting_body = (
                f"We are pleased to inform you that your development plan for the "
                f"<strong style='color:#062B49;'>{site_name}</strong> plant has been "
                f"<strong style='color:#15803d;'>approved</strong> by the Avocarbon committee. "
                f"Thank you for your commitment to continuous improvement."
            )
            badge_label = "APPROVED"
            outcome_block = """
            <tr>
              <td style="padding:0 32px 28px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 8px;font-size:13px;font-weight:700;color:#14532d;">
                        Next Steps
                      </p>
                      <p style="margin:0;font-size:13px;line-height:1.6;color:#166534;">
                        Please proceed with the implementation of the agreed actions in your plan.
                        Avocarbon Purchasing will follow up on progress during the next evaluation cycle.
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""
        else:
            rejection_reason = (plan.internal_comments or "").strip()
            header_gradient = "linear-gradient(135deg,#7f1d1d 0%,#dc2626 100%)"
            banner_bg = "#dc2626"
            banner_icon = "✗"
            banner_text = f"Plan Not Accepted &nbsp;·&nbsp; {decided_by} &nbsp;·&nbsp; {decision_date_str}"
            greeting_body = (
                f"Following the committee review, your development plan for the "
                f"<strong style='color:#062B49;'>{site_name}</strong> plant has "
                f"<strong style='color:#dc2626;'>not been accepted</strong>. "
                f"You will receive a separate email shortly with instructions to submit a revised plan."
            )
            badge_label = "REJECTED"
            reason_content = (
                f"<p style='margin:0 0 4px;font-size:11px;font-weight:700;"
                f"letter-spacing:0.08em;text-transform:uppercase;color:#991b1b;'>Reason</p>"
                f"<p style='margin:0;font-size:14px;line-height:1.6;color:#7f1d1d;'>{rejection_reason}</p>"
            ) if rejection_reason else (
                "<p style='margin:0;font-size:13px;color:#7f1d1d;'>Contact your Avocarbon purchasing representative for details.</p>"
            )
            outcome_block = f"""
            <tr>
              <td style="padding:0 32px 28px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      {reason_content}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>"""

        subject = (
            f"[Decision] Development Plan {'Approved' if is_approved else 'Not Accepted'} "
            f"— {supplier_display} · {site_name}"
        )
        body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Development Plan Decision</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#f1f5f9;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;background:#ffffff;
                      border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(15,39,68,0.10);">
          <!-- Header -->
          <tr>
            <td style="background:{header_gradient};padding:32px 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:13px;font-weight:700;
                               letter-spacing:0.18em;text-transform:uppercase;
                               color:rgba(255,255,255,0.55);">
                      Avocarbon · Supplier Management
                    </p>
                    <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;
                                color:#ffffff;line-height:1.3;">
                      {supplier_display}
                    </h1>
                    <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.75);">
                      Committee Decision &nbsp;·&nbsp; {site_name}
                    </p>
                  </td>
                  <td align="right" valign="top" style="padding-top:4px;white-space:nowrap;">
                    <span style="display:inline-block;background:rgba(255,255,255,0.15);
                                  border:1px solid rgba(255,255,255,0.25);border-radius:6px;
                                  padding:6px 14px;font-size:12px;font-weight:700;
                                  color:#ffffff;letter-spacing:0.04em;">
                      {badge_label}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <!-- Decision banner -->
          <tr>
            <td style="background:{banner_bg};padding:10px 32px;">
              <p style="margin:0;font-size:12px;font-weight:700;
                         letter-spacing:0.12em;text-transform:uppercase;color:#ffffff;">
                {banner_icon}&nbsp; {banner_text}
              </p>
            </td>
          </tr>
          <!-- Greeting -->
          <tr>
            <td style="padding:28px 32px 20px;">
              <p style="margin:0;font-size:15px;line-height:1.7;color:#1e293b;">
                Dear <strong style="color:#062B49;">{supplier_display}</strong>,
              </p>
              <p style="margin:12px 0 0;font-size:15px;line-height:1.7;color:#334155;">
                {greeting_body}
              </p>
            </td>
          </tr>
          <!-- Plan details -->
          <tr>
            <td style="padding:0 32px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;font-size:13px;">
                <tbody>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;width:38%;">Plan</td>
                    <td style="padding:11px 16px;color:#0f172a;">{plan_title}</td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">Plant</td>
                    <td style="padding:11px 16px;color:#0f172a;">{site_name}</td>
                  </tr>
                  <tr>
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">Reference</td>
                    <td style="padding:11px 16px;color:#64748b;font-family:monospace;font-size:12px;">{relation_code}</td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
          {outcome_block}
          {custom_message_block}
          <!-- Footer -->
          <tr>
            <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 32px;text-align:center;">
              <p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;">
                This message was sent automatically by the Avocarbon Supplier Management platform.<br/>
                Please do not reply directly — contact your Avocarbon purchasing representative for any questions.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

        await send_email(
            subject=subject,
            recipients=to_recipients,
            cc=cc_recipients or None,
            body_html=body_html,
            db=None,
        )

    async def send_development_plan_review_notification(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanReviewNotificationRequest,
    ) -> SupplierDevelopmentPlan:
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )

        to_recipients = [e.strip() for e in data.to_emails if e.strip() and "@" in e]
        if not to_recipients:
            raise AppException(
                "At least one valid reviewer email address is required.",
                status_code=400,
            )
        cc_recipients = [
            e.strip() for e in (data.extra_cc_emails or []) if e.strip() and "@" in e
        ]

        site_stmt = select(AvocarbonSite).where(AvocarbonSite.id_site == relation.id_site)
        site_result = await self.db.execute(site_stmt)
        site = site_result.scalars().first()
        site_name = site.site_name if site and site.site_name else f"Site #{relation.id_site}"
        site_location = ""
        if site:
            parts = [p for p in (site.city, site.country) if p]
            if parts:
                site_location = ", ".join(parts)

        unit_stmt = select(SupplierUnit).where(
            SupplierUnit.id_supplier_unit == relation.id_supplier_unit
        )
        unit_result = await self.db.execute(unit_stmt)
        unit = unit_result.scalars().first()
        group_name: Optional[str] = None
        if unit and unit.id_group:
            group_stmt = select(SupplierGroup).where(SupplierGroup.id_group == unit.id_group)
            group_result = await self.db.execute(group_stmt)
            group = group_result.scalars().first()
            if group:
                group_name = group.nom
        supplier_display = (
            group_name
            or (unit.supplier_name if unit else None)
            or f"Unit #{relation.id_supplier_unit}"
        )

        relation_code = relation.relation_code or f"REL-{relation.id_relation:06d}"
        plan_title = plan.plan_title or f"Development Plan #{plan.id_development_plan}"
        issue_date_str = (plan.issue_date or date.today()).strftime("%d %B %Y")
        submission_date_str = plan.submission_date.strftime("%d %B %Y") if plan.submission_date else "—"
        review_deadline_str = (
            data.review_deadline.strftime("%d %B %Y") if data.review_deadline else "—"
        )
        operational_grade = relation.operational_grade or "—"

        custom_message = (data.custom_message or "").strip()
        custom_message_block = (
            f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 4px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#92400e;">
                        Note from Supplier Owner
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.6;color:#78350f;">
                        {custom_message}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            """
            if custom_message
            else ""
        )

        supplier_comments_block = ""
        if plan.supplier_comments:
            supplier_comments_block = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 6px;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#166534;">
                        Supplier's Action Description
                      </p>
                      <p style="margin:0;font-size:14px;line-height:1.7;color:#14532d;">
                        {plan.supplier_comments}
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            """

        document_block = ""
        if plan.file_url:
            file_label = plan.file_name or "View attached action plan"
            document_block = f"""
            <tr>
              <td style="padding:0 32px 24px;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0"
                       style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;">
                  <tr>
                    <td style="padding:16px 20px;">
                      <p style="margin:0 0 10px;font-size:13px;font-weight:700;color:#1e40af;">
                        Attached Document
                      </p>
                      <a href="{plan.file_url}"
                         style="display:inline-block;background:#062B49;color:#ffffff;
                                padding:10px 20px;border-radius:8px;
                                font-size:13px;font-weight:700;text-decoration:none;">
                        &#128196; {file_label}
                      </a>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            """

        subject = f"[Review Required] Supplier Development Plan — {supplier_display} · {site_name}"
        body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Supplier Development Plan — Review Required</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#f1f5f9;padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;width:100%;background:#ffffff;
                      border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(15,39,68,0.10);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#3730a3 0%,#4f46e5 100%);
                        padding:32px 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td>
                    <p style="margin:0;font-size:13px;font-weight:700;
                               letter-spacing:0.18em;text-transform:uppercase;
                               color:rgba(255,255,255,0.55);">
                      Avocarbon · Supplier Management
                    </p>
                    <h1 style="margin:8px 0 0;font-size:22px;font-weight:700;
                                color:#ffffff;line-height:1.3;">
                      {supplier_display}
                    </h1>
                    <p style="margin:6px 0 0;font-size:13px;color:rgba(255,255,255,0.70);">
                      Development Plan Review &nbsp;·&nbsp; {site_name}
                    </p>
                  </td>
                  <td align="right" valign="top" style="padding-top:4px;white-space:nowrap;">
                    <span style="display:inline-block;background:rgba(255,255,255,0.15);
                                  border:1px solid rgba(255,255,255,0.25);
                                  border-radius:6px;padding:6px 14px;
                                  font-size:12px;font-weight:700;
                                  color:#ffffff;letter-spacing:0.04em;">
                      Grade {operational_grade}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Review-required banner -->
          <tr>
            <td style="background:#4f46e5;padding:10px 32px;">
              <p style="margin:0;font-size:12px;font-weight:700;
                         letter-spacing:0.12em;text-transform:uppercase;
                         color:#ffffff;">
                &#128203;&nbsp; Review Required &nbsp;·&nbsp; {supplier_display} &nbsp;&#8594;&nbsp; {site_name}
              </p>
            </td>
          </tr>

          <!-- Greeting -->
          <tr>
            <td style="padding:28px 32px 8px;">
              <p style="margin:0;font-size:15px;line-height:1.7;color:#1e293b;">
                Dear Committee,
              </p>
              <p style="margin:12px 0 0;font-size:15px;line-height:1.7;color:#334155;">
                The supplier <strong style="color:#3730a3;">{supplier_display}</strong>
                has submitted their action plan in response to their grade
                <strong style="color:#dc2626;">{operational_grade}</strong>
                operational evaluation for the
                <strong style="color:#062B49;">{site_name}</strong> plant.
                Please review the plan and record your decision (approve or reject).
              </p>
            </td>
          </tr>

          <!-- Plan details table -->
          <tr>
            <td style="padding:20px 32px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="border:1px solid #e2e8f0;border-radius:10px;
                             overflow:hidden;font-size:13px;">
                <thead>
                  <tr style="background:#f8fafc;">
                    <th colspan="2"
                        style="padding:10px 16px;text-align:left;
                               font-size:10px;font-weight:700;
                               letter-spacing:0.14em;text-transform:uppercase;
                               color:#64748b;border-bottom:1px solid #e2e8f0;">
                      Plan Details
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;width:38%;">
                      Supplier
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;font-weight:700;">
                      {supplier_display}
                    </td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Avocarbon Plant
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;font-weight:600;">
                      {site_name}{f' &nbsp;<span style="color:#94a3b8;font-weight:400;">({site_location})</span>' if site_location else ""}
                    </td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Operational Grade
                    </td>
                    <td style="padding:11px 16px;">
                      <strong style="color:#dc2626;font-size:15px;">{operational_grade}</strong>
                    </td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Plan Title
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;">
                      {plan_title}
                    </td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Reference
                    </td>
                    <td style="padding:11px 16px;color:#64748b;font-family:monospace;font-size:12px;">
                      {relation_code}
                    </td>
                  </tr>
                  <tr style="background:#f8fafc;border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Plan Issued
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;">
                      {issue_date_str}
                    </td>
                  </tr>
                  <tr style="border-bottom:1px solid #f1f5f9;">
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Supplier Submitted
                    </td>
                    <td style="padding:11px 16px;color:#0f172a;">
                      {submission_date_str}
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:11px 16px;color:#64748b;font-weight:600;">
                      Review Deadline
                    </td>
                    <td style="padding:11px 16px;">
                      <strong style="color:#4f46e5;font-size:14px;">{review_deadline_str}</strong>
                    </td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>

          <!-- Supplier action description -->
          {supplier_comments_block}

          <!-- Attached document -->
          {document_block}

          <!-- Custom message -->
          {custom_message_block}

          <!-- What you need to do -->
          <tr>
            <td style="padding:0 32px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:10px;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 10px;font-size:13px;font-weight:700;color:#3730a3;">
                      What you need to do
                    </p>
                    <table cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td valign="top" style="padding:4px 10px 4px 0;font-size:16px;color:#6366f1;font-weight:700;">1.</td>
                        <td style="padding:4px 0;font-size:13px;line-height:1.6;color:#1e1b4b;">
                          <strong>Read the attached action plan</strong> document submitted by the supplier
                          (attached to this email or available via the link above).
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:4px 10px 4px 0;font-size:16px;color:#6366f1;font-weight:700;">2.</td>
                        <td style="padding:4px 0;font-size:13px;line-height:1.6;color:#1e1b4b;">
                          <strong>Plan a review meeting</strong> with the relevant team members
                          (Quality, Logistics, Plant Manager) to discuss and align on the decision.
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:4px 10px 4px 0;font-size:16px;color:#6366f1;font-weight:700;">3.</td>
                        <td style="padding:4px 0;font-size:13px;line-height:1.6;color:#1e1b4b;">
                          <strong>Reach a decision:</strong><br/>
                          &nbsp;&nbsp;&#10003;&nbsp; <strong style="color:#166534;">Approve</strong> — if the plan adequately addresses the identified issues.<br/>
                          &nbsp;&nbsp;&#10007;&nbsp; <strong style="color:#991b1b;">Reject</strong> — if the plan is insufficient; include a clear reason.
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:4px 10px 4px 0;font-size:16px;color:#6366f1;font-weight:700;">4.</td>
                        <td style="padding:4px 0;font-size:13px;line-height:1.6;color:#1e1b4b;">
                          <strong>Communicate your decision</strong> to the Supplier Owner
                          (see contact below) so they can record it in the system.
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Communicate your decision -->
          <tr>
            <td style="padding:0 32px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 10px;font-size:13px;font-weight:700;color:#9a3412;">
                      &#128222;&nbsp; Communicate your decision to the Supplier Owner
                    </p>
                    <p style="margin:0 0 8px;font-size:13px;line-height:1.6;color:#7c2d12;">
                      Once you have reached a decision, please notify the Supplier Owner
                      by <strong>email or phone</strong> with:
                    </p>
                    <table cellpadding="0" cellspacing="0" border="0">
                      <tr>
                        <td valign="top" style="padding:3px 10px 3px 0;color:#c2410c;">&#8594;</td>
                        <td style="padding:3px 0;font-size:13px;color:#7c2d12;">
                          Your decision: <strong>Approved</strong> or <strong>Rejected</strong>
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:3px 10px 3px 0;color:#c2410c;">&#8594;</td>
                        <td style="padding:3px 0;font-size:13px;color:#7c2d12;">
                          Your name and the decision date
                        </td>
                      </tr>
                      <tr>
                        <td valign="top" style="padding:3px 10px 3px 0;color:#c2410c;">&#8594;</td>
                        <td style="padding:3px 0;font-size:13px;color:#7c2d12;">
                          If rejecting: the specific reason and what needs to be revised
                        </td>
                      </tr>
                    </table>
                    {f'''
                    <div style="margin-top:12px;padding:10px 14px;background:#fff;
                                border:1px solid #fdba74;border-radius:8px;">
                      <p style="margin:0;font-size:11px;font-weight:700;
                                letter-spacing:0.08em;text-transform:uppercase;color:#9a3412;">
                        Supplier Owner
                      </p>
                      <p style="margin:4px 0 0;font-size:14px;font-weight:600;color:#1e293b;">
                        {relation.supplier_owner}
                      </p>
                    </div>
                    ''' if relation.supplier_owner else ''}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Review deadline callout -->
          <tr>
            <td style="padding:0 32px 28px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:linear-gradient(135deg,#3730a3,#4f46e5);border-radius:10px;">
                <tr>
                  <td style="padding:18px 24px;" align="center">
                    <p style="margin:0;font-size:11px;font-weight:700;
                               letter-spacing:0.14em;text-transform:uppercase;
                               color:rgba(255,255,255,0.65);">
                      Review Deadline
                    </p>
                    <p style="margin:4px 0 0;font-size:22px;font-weight:700;color:#ffffff;">
                      {review_deadline_str}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:20px 32px;">
              <p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;text-align:center;">
                This message was sent automatically by the Avocarbon Supplier Management platform.<br/>
                To record the decision in the system, the Supplier Owner will update the plan
                once they receive your feedback.
              </p>
              <p style="margin:12px 0 0;font-size:11px;color:#cbd5e1;text-align:center;
                          letter-spacing:0.06em;">
                AVOCARBON &nbsp;·&nbsp; Supplier Management
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

        # Download all plan documents and attach them to the email.
        from app.shared.utils.email.email_service import send_email_with_attachments
        import os
        import tempfile
        import urllib.request as _urllib_req

        def _download(url: str, fname: str) -> Optional[str]:
            try:
                suffix = ("." + fname.rsplit(".", 1)[-1]) if fname and "." in fname else ""
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                with _urllib_req.urlopen(url, timeout=20) as resp:
                    tmp.write(resp.read())
                tmp.close()
                return tmp.name
            except Exception:
                return None

        plan_documents = await self.get_plan_documents(relation_id, plan_id)
        attachment_list: list[dict] = []
        temp_paths: list[str] = []
        for doc in plan_documents:
            fresh_url = get_fresh_doc_url(doc.file_url) if doc.file_url else None
            if fresh_url:
                fname = doc.original_file_name or f"document_{doc.id_document}"
                path = await asyncio.to_thread(_download, fresh_url, fname)
                if path:
                    attachment_list.append({"path": path, "filename": fname})
                    temp_paths.append(path)

        try:
            if attachment_list:
                await send_email_with_attachments(
                    subject=subject,
                    recipients=to_recipients,
                    cc=cc_recipients or None,
                    body_html=body_html,
                    attachments=attachment_list,
                    db=None,
                )
            else:
                await send_email(
                    subject=subject,
                    recipients=to_recipients,
                    cc=cc_recipients or None,
                    body_html=body_html,
                    db=None,
                )
        finally:
            for p in temp_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass

        plan.updated_at = datetime.now()
        plan.updated_by = data.changed_by or "SYSTEM"
        await self.db.commit()
        await self.db.refresh(plan)
        return plan

    async def update_development_plan(
        self,
        relation_id: int,
        plan_id: int,
        data: schemas.SupplierDevelopmentPlanUpdateRequest,
    ) -> SupplierDevelopmentPlan:
        relation = await self.get_relation(relation_id)
        plan = await self.db.get(SupplierDevelopmentPlan, plan_id)
        if not plan or plan.id_relation != relation_id:
            raise AppException(
                f"Supplier development plan with ID {plan_id} not found",
                status_code=404,
            )

        payload = data.model_dump(
            exclude={"sync_relation_hold_status", "changed_by"},
            exclude_unset=True,
        )
        for key, value in payload.items():
            setattr(plan, key, value)
        plan.updated_at = datetime.now()
        plan.updated_by = data.changed_by or "SYSTEM"
        await self.db.flush()

        if data.sync_relation_hold_status and plan.business_hold_active is not None:
            await self._apply_development_plan_hold_status(
                relation=relation,
                plan=plan,
                changed_by=data.changed_by,
            )

        await self.db.commit()
        await self.db.refresh(plan)
        return plan

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
            file_url=upload["file_url"],
            mime_type=upload["mimetype"],
            file_size=Decimal(str(upload["size"])),
            uploaded_by=uploaded_by or "SYSTEM",
            comments=comments or f"Evidence uploaded for {normalized_criteria_type}.",
            storage_provider="azure_blob",
            storage_object_key=upload["blob_name"],
        )
        self.db.add(document)
        await self.db.flush()
        latest_class_input = await self._get_latest_class_input(relation_id)
        latest_details = await self._get_latest_criteria_details(relation_id)
        existing_detail = latest_details.get(normalized_criteria_type) or {}
        selected_value = self._pluck(latest_class_input, normalized_criteria_type)
        detail_payload = {
            **existing_detail,
            "document_id": document.id_document,
            "document_name": document.document_name,
            "evidence_file_name": upload["filename"],
            "comments": existing_detail.get("comments") or comments,
        }
        detail = await self._normalize_detail_payload(
            criteria_type=normalized_criteria_type,
            selected_value=selected_value,
            payload=detail_payload,
        )
        has_document_column = await self._criteria_detail_has_document_column()
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
            "id_cycle": self._resolve_existing_cycle_id(latest_class_input),
            "criteria_type": normalized_criteria_type,
            "selected_value": selected_value,
            "score": detail.get("score"),
            "evidence_file_name": upload["filename"],
            "validity_start_date": detail.get("validity_start_date"),
            "validity_end_date": detail.get("validity_end_date"),
            "signature_date": detail.get("signature_date"),
            "last_update_date": detail.get("last_update_date"),
            "amount_value": detail.get("amount_value"),
            "amount_currency": detail.get("amount_currency"),
            "auto_validity_end_date": detail.get("auto_validity_end_date", False),
            "entered_by": uploaded_by or "SYSTEM",
            "comments": detail.get("comments"),
        }
        if has_document_column:
            insert_columns.insert(2, "id_document")
            insert_values["id_document"] = document.id_document

        stmt = text(
            f"""
            INSERT INTO pld_class_criteria_detail ({", ".join(insert_columns)})
            VALUES ({", ".join(f":{column}" for column in insert_columns)})
            """
        )
        await self.db.execute(stmt, insert_values)
        await self.db.commit()
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

        # quality_certification is server-derived only (locked in the UI) -- always
        # store whatever the unit's current best valid certification is, ignoring
        # any submitted value. There's no manual-override concept for it anymore.
        scoring_cert, _ = await self._get_best_quality_cert_for_unit(relation.id_supplier_unit)
        quality_certification = self._certification_label(scoring_cert)
        quality_certification_id = scoring_cert.id_certification if scoring_cert else None

        # model_fields_set contains every field explicitly provided in the request body,
        # including those explicitly set to null.  Omitted fields are NOT in the set.
        # This lets us distinguish "caller wants to clear this field" (null in payload)
        # from "caller didn't touch this field" (field absent from payload).
        sent = data.model_fields_set

        def _pick(field: str, data_val: Any) -> Any:
            return data_val if field in sent else self._pluck(previous_input, field)

        merged_values = {
            "top":                   self._normalize_criteria_value("top",                   _pick("top",                   data.top)),
            "lta":                   self._normalize_criteria_value("lta",                   _pick("lta",                   data.lta)),
            "productivity":          self._normalize_criteria_value("productivity",          _pick("productivity",          data.productivity)),
            "quality_certification": quality_certification,
            "prod_lia_ins":          self._normalize_criteria_value("prod_lia_ins",          _pick("prod_lia_ins",          data.prod_lia_ins)),
            "competitiveness":       self._normalize_criteria_value("competitiveness",       _pick("competitiveness",       data.competitiveness)),
            "sqma":                  self._normalize_criteria_value("sqma",                  _pick("sqma",                  data.sqma)),
            "family_coverage":       self._normalize_criteria_value("family_coverage",       _pick("family_coverage",       data.family_coverage)),
            "geo_coverage":          self._normalize_criteria_value("geo_coverage",          _pick("geo_coverage",          data.geo_coverage)),
            "cons_or_wd":            self._normalize_criteria_value("cons_or_wd",            _pick("cons_or_wd",            data.cons_or_wd)),
            "financial_health":      self._normalize_criteria_value("financial_health",      _pick("financial_health",      data.financial_health)),
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
            quality_certification_id=quality_certification_id,
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
            id_cycle=cycle.id_cycle
            if cycle
            else self._resolve_existing_cycle_id(
                previous_input,
                current_classification,
                previous_impact_input,
            ),
            top=merged_values["top"],
            lta=merged_values["lta"],
            productivity=merged_values["productivity"],
            id_certification=quality_certification_id,
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
            cycle_id=cycle.id_cycle
            if cycle
            else self._resolve_existing_cycle_id(
                previous_input,
                current_classification,
                previous_impact_input,
            ),
            merged_values=merged_values,
            submitted_details=data.class_criteria_details,
            changed_by=data.changed_by or "SYSTEM",
        )
        impact_input = ImpactEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle
            if cycle
            else self._resolve_existing_cycle_id(
                previous_input,
                current_classification,
                previous_impact_input,
            ),
            question_1=data.impact_question_1
            if data.impact_question_1 is not None
            else self._pluck(previous_impact_input, "question_1"),
            question_2=data.impact_question_2
            if data.impact_question_2 is not None
            else self._pluck(previous_impact_input, "question_2"),
            question_3=data.impact_question_3
            if data.impact_question_3 is not None
            else self._pluck(previous_impact_input, "question_3"),
            question_4=data.impact_question_4
            if data.impact_question_4 is not None
            else self._pluck(previous_impact_input, "question_4"),
            question_5=data.impact_question_5
            if data.impact_question_5 is not None
            else self._pluck(previous_impact_input, "question_5"),
            question_6=data.impact_question_6
            if data.impact_question_6 is not None
            else self._pluck(previous_impact_input, "question_6"),
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
                comments=data.comments
                or "Class evaluation recalculated from PLD criteria update.",
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
        relation.evaluation_suggestion = (
            panel_decision or relation.evaluation_suggestion
        )
        if history and history.old_status != history.new_status:
            relation.last_status_change = history.changed_at
        if evaluation_changed:
            relation.last_evaluation_date = evaluation_date
            # next_evaluation_date only advances on a genuine re-evaluation of the
            # scorecard -- not on an ad-hoc correction to one or two criteria (see
            # AD_HOC_CYCLE_TYPES) that isn't a full periodic review. Computed from
            # the relation's evaluation_frequency (or scope-based default), not from
            # an unrelated per-criterion field.
            if data.cycle_type not in AD_HOC_CYCLE_TYPES:
                from app.features.evaluations.service import (
                    compute_next_evaluation_date,
                    infer_frequency,
                )
                relation.next_evaluation_date = compute_next_evaluation_date(
                    evaluation_date, infer_frequency(relation)
                )
        if data.comments:
            relation.evaluation_comments = data.comments

        await self._ensure_auto_development_plan_for_low_grade(
            relation=relation,
            operational_grade=operational_grade,
            evaluation_date=evaluation_date,
            changed_by=data.changed_by,
            reason=data.comments,
        )

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

        previous_operational_input = await self._get_latest_operational_input(
            relation_id
        )
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
            cycle_type=data.cycle_type
            or self._default_operational_cycle_type(data.source_type),
            comments=data.comments
            or f"Operational evaluation refreshed from {data.source_type}.",
            evaluation_date=evaluation_date,
        )
        class_score = self._pluck(current_classification, "classification_score")
        class_value = (
            self._pluck(current_classification, "class_value") or relation.class_value
        )
        strategic_mention = (
            self._pluck(current_classification, "strategic_mention")
            or relation.strategic_mention
        )
        panel_decision = (
            self._pluck(current_classification, "panel_decision")
            or relation.panel_decision
        )
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
            production_manufacturing=merged_operational_values[
                "production_manufacturing"
            ],
            quality_audits=merged_operational_values["quality_audits"],
            suppliers_subcontractors=merged_operational_values[
                "suppliers_subcontractors"
            ],
            deliveries=merged_operational_values["deliveries"],
            environment_ethic_rules=merged_operational_values[
                "environment_ethic_rules"
            ],
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
            comments=data.comments
            or f"Operational evaluation updated from {data.source_type}.",
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
            comments=data.comments
            or "Operational evaluation refreshed while keeping latest class evaluation.",
            entered_by=data.changed_by or "SYSTEM",
        )
        self.db.add(classification)

        history = await self._record_transition(
            relation=relation,
            changed_by=data.changed_by or "SYSTEM",
            reason=data.comments
            or f"Operational evaluation updated from {data.source_type}.",
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
        relation.evaluation_suggestion = (
            panel_decision or relation.evaluation_suggestion
        )
        if history and history.old_status != history.new_status:
            relation.last_status_change = history.changed_at
        if evaluation_changed:
            relation.last_evaluation_date = evaluation_date
        if data.comments:
            relation.evaluation_comments = data.comments

        await self._ensure_auto_development_plan_for_low_grade(
            relation=relation,
            operational_grade=operational_grade,
            evaluation_date=evaluation_date,
            changed_by=data.changed_by,
            reason=data.comments,
        )

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

        # quality_certification is server-derived only -- always the unit's current
        # best valid certification, same as update_class_evaluation.
        scoring_cert, _ = await self._get_best_quality_cert_for_unit(relation.id_supplier_unit)
        certification_type = self._certification_label(scoring_cert)
        certification_id = scoring_cert.id_certification if scoring_cert else None

        cycle = await self._create_cycle(
            relation_id=relation_id,
            cycle_type="Initial Relation Evaluation",
            comments=data.comments
            or "Initial evaluation recorded for the supplier-site relation.",
            evaluation_date=evaluation_date,
        )

        merged_values = {
            "top": self._normalize_criteria_value("top", data.top),
            "lta": self._normalize_criteria_value("lta", data.lta),
            "productivity": self._normalize_criteria_value("productivity", data.prod),
            "quality_certification": certification_type,
            "prod_lia_ins": self._normalize_criteria_value(
                "prod_lia_ins", data.prod_lia_ins
            ),
            "competitiveness": self._normalize_criteria_value(
                "competitiveness", data.competitiveness
            ),
            "sqma": self._normalize_criteria_value("sqma", data.sqma),
            "family_coverage": self._normalize_criteria_value(
                "family_coverage", data.family_coverage
            ),
            "geo_coverage": self._normalize_criteria_value(
                "geo_coverage", data.geo_coverage
            ),
            "cons_or_wd": self._normalize_criteria_value("cons_or_wd", data.cons_or_wd),
            "financial_health": self._normalize_criteria_value(
                "financial_health", data.financial_health
            ),
        }
        class_score = self._prefer_decimal(
            await self._try_calculate_class_score(merged_values)
        )
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
            else data.operational_class.upper()
            if data.operational_class
            else None
        )
        impact_score = data.impact_score
        strategic_mention = (
            data.strategic_mention.lower() if data.strategic_mention else None
        )
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
            id_certification=certification_id,
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

        operational_input = OperationalEvaluationInput(
            id_relation=relation_id,
            id_cycle=cycle.id_cycle,
            source_type="self_assessment",
            management_system=merged_operational_values["management_system"],
            customer_communication=merged_operational_values["customer_communication"],
            development_design=merged_operational_values["development_design"],
            production_manufacturing=merged_operational_values[
                "production_manufacturing"
            ],
            quality_audits=merged_operational_values["quality_audits"],
            suppliers_subcontractors=merged_operational_values[
                "suppliers_subcontractors"
            ],
            deliveries=merged_operational_values["deliveries"],
            environment_ethic_rules=merged_operational_values[
                "environment_ethic_rules"
            ],
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
        relation.evaluation_suggestion = (
            panel_decision or relation.evaluation_suggestion
        )
        if history and history.old_status != history.new_status:
            relation.last_status_change = history.changed_at
        relation.last_evaluation_date = evaluation_date
        # The initial evaluation always establishes the first re-evaluation date --
        # computed from evaluation_frequency (or a scope-based default), not from an
        # unrelated per-criterion field.
        from app.features.evaluations.service import (
            compute_next_evaluation_date,
            infer_frequency,
        )
        relation.next_evaluation_date = compute_next_evaluation_date(
            evaluation_date, infer_frequency(relation)
        )
        if data.comments:
            relation.evaluation_comments = data.comments

        await self._ensure_auto_development_plan_for_low_grade(
            relation=relation,
            operational_grade=operational_grade,
            evaluation_date=evaluation_date,
            changed_by=data.changed_by,
            reason=data.comments,
        )

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

    async def _apply_development_plan_hold_status(
        self,
        relation: SupplierSiteRelation,
        plan: SupplierDevelopmentPlan,
        changed_by: Optional[str],
    ) -> None:
        target_status = self._development_plan_target_status(plan.business_hold_active)
        if not target_status:
            return

        computed_status = self._derive_supplier_status(relation.final_grade)
        current_status = relation.supplier_status or computed_status
        if current_status == target_status:
            return

        actor = changed_by or "SYSTEM"
        change_summary = (
            f"{DEVELOPMENT_PLAN_MARKER} Plan #{plan.id_development_plan} "
            f"set business hold to {'active' if plan.business_hold_active else 'released'}."
        )
        history = SupplierStatusHistory(
            id_relation=relation.id_relation,
            old_status=current_status,
            new_status=target_status,
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
            change_reason=change_summary,
            changed_by=actor,
            changed_at=datetime.now(),
        )
        self.db.add(history)
        relation.supplier_status = target_status
        relation.last_status_change = datetime.now()

    async def _ensure_auto_development_plan_for_low_grade(
        self,
        relation: SupplierSiteRelation,
        operational_grade: Optional[str],
        evaluation_date: date,
        changed_by: Optional[str],
        reason: Optional[str],
    ) -> Optional[SupplierDevelopmentPlan]:
        # Trigger only when the supplier's status is "New business on hold" (Red).
        # This is determined by the full final grade (e.g. C4, D1-D4, A4, B4),
        # not simply by the operational grade being C or D — grades like C1/C2/C3
        # map to Orange status and do NOT require a development plan, while A4/B4
        # map to Red and DO require one.
        if relation.supplier_status != STATUS_NEW_BUSINESS_ON_HOLD:
            return None

        stmt = (
            select(SupplierDevelopmentPlan)
            .where(SupplierDevelopmentPlan.id_relation == relation.id_relation)
            .order_by(SupplierDevelopmentPlan.id_development_plan.desc())
        )
        result = await self.db.execute(stmt)
        existing_plans = result.scalars().all()
        active_plan = next(
            (
                plan
                for plan in existing_plans
                if (plan.plan_status or "").strip().lower()
                not in {"approved", "closed", "cancelled", "rejected"}
            ),
            None,
        )
        if active_plan:
            return active_plan

        relation_code = relation.relation_code or f"REL-{relation.id_relation:06d}"
        final_grade_label = relation.final_grade or operational_grade or "unknown"
        plan = SupplierDevelopmentPlan(
            id_relation=relation.id_relation,
            plan_title=f"Development plan required - {relation_code}",
            plan_status=PLAN_STATUS_MUST_BE_SEND,
            issue_date=evaluation_date,
            due_date=evaluation_date + timedelta(days=30),
            internal_comments=reason
            or (
                f"Auto-created: evaluation grade {final_grade_label} triggered a development plan."
            ),
            updated_by=changed_by or "SYSTEM",
        )
        self.db.add(plan)
        await self.db.flush()
        return plan

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

    async def _find_criteria_document_fallback(
        self,
        relation_id: int,
        criteria_type: str,
        evidence_file_name: Optional[str],
    ) -> Optional[Document]:
        if not evidence_file_name:
            return None

        expected_name = f"{criteria_type.replace('_', ' ').title()} Evidence"
        stmt = (
            select(Document)
            .where(Document.id_relation == relation_id)
            .where(Document.document_type == "evaluation_criterion_evidence")
            .where(Document.original_file_name == evidence_file_name)
            .where(Document.document_name == expected_name)
            .order_by(Document.id_document.desc())
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    def _recover_criteria_blob_url(
        self,
        relation_id: int,
        criteria_type: str,
        evidence_file_name: Optional[str],
    ) -> Optional[str]:
        if not evidence_file_name:
            return None
        return get_recovered_blob_url(
            prefix=f"evaluation/evaluation_{relation_id}_{criteria_type}_",
            original_file_name=evidence_file_name,
        )

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
        document_ids = [
            entry["id_document"]
            for entry in entries
            if has_document_column and entry.get("id_document")
        ]
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
            if not document:
                document = await self._find_criteria_document_fallback(
                    relation_id=relation_id,
                    criteria_type=criteria_type,
                    evidence_file_name=entry.get("evidence_file_name"),
                )
                if document:
                    document_id = document.id_document
            recovered_url = None
            if not document or not document.file_url:
                recovered_url = self._recover_criteria_blob_url(
                    relation_id=relation_id,
                    criteria_type=criteria_type,
                    evidence_file_name=entry.get("evidence_file_name"),
                )
            latest_by_criteria[criteria_type] = {
                "document_id": document_id,
                "document_name": document.document_name if document else entry["evidence_file_name"],
                "document_url": get_fresh_doc_url(document.file_url)
                if document and document.file_url
                else recovered_url,
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

    @classmethod
    def _rank_certifications(
        cls,
        certifications: list[SupplierCertification],
    ) -> tuple[Optional[SupplierCertification], Optional[SupplierCertification]]:
        """Given ALL of a unit's certifications, return (scoring_cert, display_cert).

        - scoring_cert: the best currently VALID (non-expired) certification, live —
          never a stored snapshot. None when the unit has no valid quality cert, so
          an expired cert can never keep inflating the class score.
        - display_cert: best cert for the validity-tracker view — the valid one if
          one exists, otherwise the best expired cert so evaluators can see what lapsed.
        Returns (None, None) when given no certifications at all.
        """
        if not certifications:
            return None, None

        SCORE_ORDER = ["IATF / ISO9001 (cat BCD)", "ISO9001", "None"]
        today = date.today()

        def pick_best(certs: list[SupplierCertification]) -> Optional[SupplierCertification]:
            best_cert: Optional[SupplierCertification] = None
            best_rank = len(SCORE_ORDER)
            for cert in certs:
                normalized = cls._normalize_criteria_value("quality_certification", cert.certification_type)
                rank = SCORE_ORDER.index(normalized) if normalized in SCORE_ORDER else best_rank
                if rank < best_rank:
                    best_rank = rank
                    best_cert = cert
            return best_cert

        valid_certs = [c for c in certifications if not c.end_date or c.end_date >= today]
        valid_cert = pick_best(valid_certs)
        if valid_cert is not None:
            # A valid cert exists and drives the score.
            # For the tracker display: if the best valid cert has no end_date (no expiry
            # set), prefer showing the most recently expired cert instead so the tracker
            # reflects actual expiry dates and doesn't hide lapsed certs.
            if not valid_cert.end_date:
                expired_certs = [c for c in certifications if c.end_date and c.end_date < today]
                display_cert = pick_best(expired_certs) or valid_cert
            else:
                display_cert = valid_cert
            return valid_cert, display_cert
        # No valid cert: score is None (expired certs don't count), but still surface
        # the best expired cert in the validity tracker so the evaluator sees it lapsed.
        return None, pick_best(list(certifications))

    async def _get_best_quality_cert_for_unit(
        self,
        unit_id: int,
    ) -> tuple[Optional[SupplierCertification], Optional[SupplierCertification]]:
        """Single-unit convenience wrapper around _rank_certifications. See its
        docstring for the meaning of the returned (scoring_cert, display_cert)."""
        stmt = (
            select(SupplierCertification)
            .where(SupplierCertification.id_supplier_unit == unit_id)
            .where(SupplierCertification.is_deleted.is_(False))
        )
        certifications = (await self.db.execute(stmt)).scalars().all()
        return self._rank_certifications(list(certifications))

    async def _get_best_certs_for_units(
        self,
        unit_ids: list[int],
    ) -> dict[int, tuple[Optional[SupplierCertification], Optional[SupplierCertification]]]:
        """Batch version of _get_best_quality_cert_for_unit — one query for many units,
        used by the Criteria Validity Tracker to avoid an N+1 query per relation."""
        if not unit_ids:
            return {}
        stmt = (
            select(SupplierCertification)
            .where(SupplierCertification.id_supplier_unit.in_(unit_ids))
            .where(SupplierCertification.is_deleted.is_(False))
        )
        certs = (await self.db.execute(stmt)).scalars().all()
        by_unit: dict[int, list[SupplierCertification]] = {}
        for cert in certs:
            by_unit.setdefault(cert.id_supplier_unit, []).append(cert)
        return {uid: self._rank_certifications(cs) for uid, cs in by_unit.items()}

    def _certification_label(self, cert: Optional[SupplierCertification]) -> Optional[str]:
        if cert is None:
            return None
        return self._normalize_criteria_value("quality_certification", cert.certification_type)

    async def _get_relation_quality_certification(
        self,
        relation: SupplierSiteRelation,
    ) -> Optional[str]:
        scoring_cert, _ = await self._get_best_quality_cert_for_unit(relation.id_supplier_unit)
        return self._certification_label(scoring_cert)

    async def _sync_quality_cert_detail(
        self,
        relation_id: int,
        cert_value: Optional[str],
        best_cert: Optional[SupplierCertification],
    ) -> None:
        """Upsert the auto-derived PldClassCriteriaDetail row for quality_certification.

        Only touches rows that were themselves auto-derived (auto_validity_end_date=True).
        Manually edited rows are left untouched so evaluators can keep custom validity windows.
        """
        stmt = (
            select(PldClassCriteriaDetail)
            .where(PldClassCriteriaDetail.id_relation == relation_id)
            .where(PldClassCriteriaDetail.criteria_type == "quality_certification")
            .where(PldClassCriteriaDetail.auto_validity_end_date.is_(True))
            .where(PldClassCriteriaDetail.is_deleted.is_(False))
            .order_by(PldClassCriteriaDetail.id_detail.desc())
            .limit(1)
            # Lock the row so two concurrent cert patches on the same unit can't both
            # read "no existing row" and each INSERT a duplicate auto-detail.
            .with_for_update()
        )
        existing = (await self.db.execute(stmt)).scalar_one_or_none()

        end_date = best_cert.end_date if best_cert else None
        start_date = best_cert.start_date if best_cert else None

        if existing:
            existing.selected_value = cert_value
            existing.validity_start_date = start_date
            existing.validity_end_date = end_date
            existing.last_update_date = date.today()
        else:
            # Only create an auto-detail when we have at least a cert value or end_date
            if cert_value or end_date:
                self.db.add(PldClassCriteriaDetail(
                    id_relation=relation_id,
                    criteria_type="quality_certification",
                    selected_value=cert_value,
                    validity_start_date=start_date,
                    validity_end_date=end_date,
                    auto_validity_end_date=True,
                    last_update_date=date.today(),
                ))

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
            .order_by(
                SupplierStatusHistory.changed_at.desc(),
                SupplierStatusHistory.id_history.desc(),
            )
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
            and relation.class_value == new_class
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

    async def sync_quality_certification_for_unit(
        self,
        unit_id: int,
        triggered_by: Optional[str] = None,
        source_cert_id: Optional[int] = None,
        change: Optional[str] = None,
    ) -> list[dict]:
        """Re-derive quality_certification on all active relations for a unit.

        Called automatically after a SupplierCertification record is created, updated
        or deleted.
        - Always syncs PldClassCriteriaDetail so DocumentsValidityPage reflects cert expiry.
        - Only creates a new PldClassEvaluationInput when the derived value actually changes.

        `triggered_by` / `source_cert_id` / `change` are recorded on the generated
        evaluation rows so the audit trail can trace each auto-update back to the cert
        edit and the user who made it.
        """
        # Build a traceable provenance note for the generated evaluation rows.
        change_label = {"create": "added", "update": "updated", "delete": "removed"}.get(
            change or "", "changed"
        )
        cert_ref = f" #{source_cert_id}" if source_cert_id else ""
        actor = triggered_by or "unknown user"
        provenance = f"Auto-sync: quality certification{cert_ref} {change_label} by {actor}."

        stmt = (
            select(SupplierSiteRelation)
            .where(SupplierSiteRelation.id_supplier_unit == unit_id)
            .where(SupplierSiteRelation.is_deleted.is_(False))
        )
        relations = (await self.db.execute(stmt)).scalars().all()

        # Resolve best cert once — same unit_id for all relations
        scoring_cert, display_cert = await self._get_best_quality_cert_for_unit(unit_id)
        new_cert_id = scoring_cert.id_certification if scoring_cert else None
        new_cert_value = self._certification_label(scoring_cert)

        affected: list[dict] = []
        for relation in relations:
            # Always keep criteria detail in sync with the cert's actual dates
            await self._sync_quality_cert_detail(relation.id_relation, new_cert_value, display_cert)

            previous_input = await self._get_latest_class_input(relation.id_relation)
            if not previous_input:
                continue

            current_id = self._pluck(previous_input, "id_certification")
            if new_cert_id == current_id:
                continue
            previous_cert = (
                await self.db.get(SupplierCertification, current_id)
                if current_id is not None
                else None
            )
            current_value = self._certification_label(previous_cert)

            merged = {
                "top":                   self._pluck(previous_input, "top"),
                "lta":                   self._pluck(previous_input, "lta"),
                "productivity":          self._pluck(previous_input, "productivity"),
                "quality_certification": new_cert_value,
                "prod_lia_ins":          self._pluck(previous_input, "prod_lia_ins"),
                "competitiveness":       self._pluck(previous_input, "competitiveness"),
                "sqma":                  self._pluck(previous_input, "sqma"),
                "family_coverage":       self._pluck(previous_input, "family_coverage"),
                "geo_coverage":          self._pluck(previous_input, "geo_coverage"),
                "cons_or_wd":            self._pluck(previous_input, "cons_or_wd"),
                "financial_health":      self._pluck(previous_input, "financial_health"),
            }
            class_score = await self._try_calculate_class_score(merged)
            class_value = (
                self._derive_class_value_from_score(class_score)
                if class_score is not None
                else self._pluck(previous_input, "class_value")
            )
            strategic_mention = self._pluck(previous_input, "strategic_mention")
            panel_decision = self._pluck(previous_input, "panel_decision")
            evaluation_date = date.today()

            # A new EvaluationCycle is what makes this show up as its own entry in
            # History & Documents with a "changed from X to Y" diff -- reusing the
            # previous cycle id (as before) silently overwrote that cycle's snapshot
            # instead, leaving no visible trace of the transition.
            cycle = await self._create_cycle(
                relation_id=relation.id_relation,
                cycle_type="Certification Update",
                comments=provenance,
                evaluation_date=evaluation_date,
            )

            new_input = PldClassEvaluationInput(
                id_relation=relation.id_relation,
                id_cycle=cycle.id_cycle,
                top=merged["top"],
                lta=merged["lta"],
                productivity=merged["productivity"],
                id_certification=new_cert_id,
                prod_lia_ins=merged["prod_lia_ins"],
                competitiveness=merged["competitiveness"],
                sqma=merged["sqma"],
                family_coverage=merged["family_coverage"],
                geo_coverage=merged["geo_coverage"],
                cons_or_wd=merged["cons_or_wd"],
                financial_health=merged["financial_health"],
                class_score=class_score,
                class_value=class_value,
                impact_score=self._pluck(previous_input, "impact_score"),
                strategic_mention=strategic_mention,
                panel_decision=panel_decision,
                comments=provenance,
                entered_by="SYSTEM",
            )
            self.db.add(new_input)

            # Bring this up to parity with update_class_evaluation: recompute the
            # final grade/status from the new class_value and record the transition,
            # so a certification-driven class change is fully auditable, not just
            # a silently updated relation.class_value.
            current_classification = await self._get_latest_classification(relation.id_relation)
            operational_grade = relation.operational_grade
            operational_score = self._pluck(current_classification, "operational_score")
            final_grade = self._compose_final_grade(operational_grade, class_value)
            computed_status = self._derive_supplier_status(final_grade)
            effective_status = self._resolve_effective_supplier_status(
                relation=relation, computed_status=computed_status,
            )

            classification = Classification(
                id_relation=relation.id_relation,
                id_cycle=cycle.id_cycle,
                classification_date=evaluation_date,
                classification_score=class_score,
                class_value=class_value,
                operational_score=operational_score,
                operational_grade=operational_grade,
                final_grade=final_grade,
                impact_score=self._pluck(previous_input, "impact_score"),
                strategic_mention=strategic_mention,
                panel_decision=panel_decision,
                comments=provenance,
                entered_by="SYSTEM",
            )
            self.db.add(classification)
            await self.db.flush()

            history = await self._record_transition(
                relation=relation,
                changed_by=actor,
                reason=provenance,
                changed_at=datetime.now(),
                new_status=effective_status,
                new_class=class_value,
                new_grade=operational_grade,
                new_final_grade=final_grade,
                new_strategic_mention=strategic_mention,
                new_panel_decision=panel_decision,
            )
            if history and history.old_status != history.new_status:
                relation.last_status_change = history.changed_at

            relation.class_value = class_value
            relation.final_grade = final_grade
            relation.supplier_status = effective_status
            relation.last_evaluation_date = evaluation_date

            affected.append({
                "relation_id": relation.id_relation,
                "previous_quality_cert": current_value,
                "new_quality_cert": new_cert_value,
                "new_class_score": float(class_score) if class_score is not None else None,
                "new_class_value": class_value,
            })

        await self.db.commit()
        return affected

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
            payload = (
                submitted_details.get(criteria_type)
                or latest_details.get(criteria_type)
                or {}
            )
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(exclude_none=True)
            detail = await self._normalize_detail_payload(
                criteria_type=criteria_type,
                selected_value=merged_values.get(criteria_type),
                payload=payload,
            )
            if has_document_column:
                detail["document_id"] = await self._resolve_valid_document_id(
                    relation_id=relation_id,
                    document_id=detail.get("document_id"),
                )
            if not merged_values.get(criteria_type) and not any(
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
                "evidence_file_name": detail.get("evidence_file_name")
                or detail.get("document_name"),
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

    async def _resolve_development_plan_email_targets(
        self,
        relation: SupplierSiteRelation,
    ) -> tuple[list[str], list[str]]:
        stmt = (
            select(Contact)
            .where(Contact.id_supplier_unit == relation.id_supplier_unit)
            .where(Contact.email.is_not(None))
            .order_by(Contact.is_primary_contact.desc(), Contact.id_contact.asc())
        )
        result = await self.db.execute(stmt)
        contacts = result.scalars().all()

        to_recipients: list[str] = []
        for contact in contacts:
            email = (contact.email or "").strip()
            if email and "@" in email and email not in to_recipients:
                to_recipients.append(email)

        cc_recipients: list[str] = []
        owner_email = (relation.supplier_owner or "").strip()
        if owner_email and "@" in owner_email and owner_email not in to_recipients:
            cc_recipients.append(owner_email)

        return to_recipients, cc_recipients

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

    async def get_criteria_scores_breakdown(
        self,
        merged_values: dict[str, Optional[str]],
    ) -> dict[str, Optional[float]]:
        """Return a per-criterion score map for live display in the UI.

        Normalizes every criterion's raw value here, internally, rather than
        trusting each caller to normalize before calling -- a prior version
        relied on callers to normalize, and 4 of 11 criteria (top, lta,
        productivity, financial_health) were passed raw, silently returning
        None (blank score badges) for any relation using a pre-canonicalization
        alias (e.g. "30 days end of month or +"). Normalization is idempotent
        (a no-op on an already-canonical value), so this is safe regardless of
        whether the caller already normalized.
        """
        criteria_map = {
            criteria_type: self._normalize_criteria_value(criteria_type, raw_value)
            for criteria_type, raw_value in {
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
            }.items()
        }
        result_map: dict[str, Optional[float]] = {}
        for criteria_type, selected_value in criteria_map.items():
            score = await self._lookup_pld_score(criteria_type, selected_value)
            result_map[criteria_type] = float(score) if score is not None else None
        return result_map

    async def get_self_assessment_baseline(
        self,
        relation_id: int,
    ) -> Optional[OperationalEvaluationInput]:
        """Return the locked self-assessment baseline (source_type='self_assessment'), if any."""
        stmt = (
            select(OperationalEvaluationInput)
            .where(OperationalEvaluationInput.id_relation == relation_id)
            .where(OperationalEvaluationInput.source_type == "self_assessment")
            .order_by(OperationalEvaluationInput.id_operational_input.asc())
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def upload_evaluation_reference(
        self,
        relation_id: int,
        file: Any,
        uploaded_by: Optional[str],
        comments: Optional[str] = None,
    ) -> Document:
        relation = await self.get_relation(relation_id)
        upload = await upload_evaluation_document(file=file, relation_id=relation_id, criteria_type="evaluation_reference")
        doc = Document(
            id_relation=relation_id,
            id_supplier_unit=relation.id_supplier_unit,
            document_type="evaluation_reference",
            document_name=upload["filename"],
            original_file_name=upload["filename"],
            file_url=upload["file_url"],
            mime_type=upload["mimetype"],
            file_size=Decimal(str(upload["size"])),
            uploaded_by=uploaded_by or "SYSTEM",
            comments=comments or "Evaluation reference document.",
            storage_provider="azure_blob",
            storage_object_key=upload["blob_name"],
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def upload_lta_document(
        self,
        relation_id: int,
        file: Any,
        uploaded_by: Optional[str],
        comments: Optional[str] = None,
    ) -> Document:
        relation = await self.get_relation(relation_id)
        upload = await upload_evaluation_document(file=file, relation_id=relation_id, criteria_type="lta_agreement")
        doc = Document(
            id_relation=relation_id,
            id_supplier_unit=relation.id_supplier_unit,
            document_type="lta_agreement",
            document_name=upload["filename"],
            original_file_name=upload["filename"],
            file_url=upload["file_url"],
            mime_type=upload["mimetype"],
            file_size=Decimal(str(upload["size"])),
            uploaded_by=uploaded_by or "SYSTEM",
            comments=comments or "Long Term Agreement document.",
            storage_provider="azure_blob",
            storage_object_key=upload["blob_name"],
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def list_relation_documents_by_type(
        self,
        relation_id: int,
        document_types: Optional[list[str]] = None,
    ) -> list[Document]:
        stmt = select(Document).where(Document.id_relation == relation_id)
        if document_types:
            stmt = stmt.where(Document.document_type.in_(document_types))
        stmt = stmt.order_by(Document.uploaded_at.desc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def _lookup_pld_score(
        self,
        criteria_type: str,
        value: Optional[str],
    ) -> Optional[Decimal]:
        """Single query point for pld_scoring_rules lookups. Expects an
        already-normalized value (see _normalize_criteria_value) -- this
        function does exact matching only, by design, so every caller stays
        explicit about whether/when it normalizes. Used by
        _try_calculate_class_score(), get_criteria_scores_breakdown(), and
        _score_from_selected_value() so there is exactly one query to change
        if the lookup rule (e.g. tie-breaking, an effective-date filter) ever
        needs to change."""
        if not value:
            return None
        stmt = (
            select(PldScoringRules.score)
            .where(PldScoringRules.criteria_type == criteria_type)
            .where(PldScoringRules.is_active.is_(True))
            .where(PldScoringRules.min_value == value)
            .order_by(PldScoringRules.score.desc())
        )
        result = await self.db.execute(stmt)
        score = result.scalars().first()
        return Decimal(str(score)) if score is not None else None

    async def _try_calculate_class_score(
        self,
        merged_values: dict[str, Optional[str]],
    ) -> Optional[Decimal]:
        # Always divide by 11 (fixed denominator, same as Monday.com formula).
        # Missing criteria count as 0; an unrecognized value also counts as 0.
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

        total = Decimal("0")
        any_filled = False

        for criteria_type, selected_value in criteria_map.items():
            if not selected_value:
                continue
            any_filled = True
            score = await self._lookup_pld_score(criteria_type, selected_value)
            if score is not None:
                total += score
            else:
                # No matching active scoring rule — the criterion contributes 0.
                # Usually a normalization gap (value not mapped to a canonical tier);
                # log it so silent score deflation is traceable rather than invisible.
                logger.warning(
                    "No active pld_scoring_rule for criteria_type=%r value=%r; "
                    "scoring it as 0. Check CRITERIA_VALUE_NORMALIZATION mapping.",
                    criteria_type, selected_value,
                )

        if not any_filled:
            return None
        return total / Decimal("11")

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

    async def _normalize_detail_payload(
        self,
        criteria_type: str,
        selected_value: Optional[str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        start_date = payload.get("validity_start_date")
        end_date = payload.get("validity_end_date")
        auto_validity_end_date = bool(payload.get("auto_validity_end_date"))
        if (
            criteria_type == "financial_health"
            and start_date
            and (auto_validity_end_date or end_date is None)
        ):
            years = FINANCIAL_HEALTH_VALIDITY_YEARS.get(selected_value or "", 0)
            if years:
                end_date = date(
                    start_date.year + years, start_date.month, start_date.day
                )
                auto_validity_end_date = True

        return {
            "document_id": payload.get("document_id"),
            "document_name": payload.get("document_name"),
            "document_url": payload.get("document_url"),
            "document_mime_type": payload.get("document_mime_type"),
            "document_size": self._prefer_decimal(payload.get("document_size")),
            "evidence_file_name": payload.get("evidence_file_name"),
            "validity_start_date": start_date,
            "validity_end_date": end_date,
            "signature_date": payload.get("signature_date"),
            "last_update_date": payload.get("last_update_date"),
            "amount_value": self._prefer_decimal(payload.get("amount_value")),
            "amount_currency": payload.get("amount_currency"),
            "auto_validity_end_date": auto_validity_end_date,
            "comments": payload.get("comments"),
            "score": self._prefer_decimal(
                payload.get("score"),
                await self._score_from_selected_value(criteria_type, selected_value),
            ),
        }

    async def _score_from_selected_value(
        self,
        criteria_type: str,
        selected_value: Optional[str],
    ) -> Optional[Decimal]:
        """Look up a criterion value's score via the shared _lookup_pld_score()
        helper -- the single source of truth also used by
        _try_calculate_class_score() and get_criteria_scores_breakdown().
        Previously this duplicated the scoring table as its own hardcoded
        dict, which had drifted out of sync with pld_scoring_rules (e.g.
        "15 days net" was hardcoded to 10 instead of the real 0)."""
        if not selected_value:
            return None
        normalized_value = self._normalize_criteria_value(criteria_type, selected_value)
        return await self._lookup_pld_score(criteria_type, normalized_value)

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
        if score >= Decimal("80"):
            return 1
        if score >= Decimal("50"):
            return 2
        if score >= Decimal("30"):
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

    @staticmethod
    def _development_plan_target_status(
        business_hold_active: Optional[bool],
    ) -> Optional[str]:
        if business_hold_active is None:
            return None
        return (
            STATUS_NEW_BUSINESS_ON_HOLD
            if business_hold_active
            else STATUS_CAN_QUOTE_NOT_BE_AWARDED
        )

    @staticmethod
    def _serialize_development_plan(
        plan: SupplierDevelopmentPlan,
    ) -> dict[str, Any]:
        today = date.today()
        is_overdue = (
            plan.due_date is not None
            and plan.due_date < today
            and (plan.plan_status or "").lower() not in {"approved", "closed"}
        )
        days_past_due = (today - plan.due_date).days if is_overdue else None
        return {
            "id_development_plan": plan.id_development_plan,
            "id_relation": plan.id_relation,
            "id_document": plan.id_document,
            "plan_title": plan.plan_title,
            "plan_status": plan.plan_status,
            "issue_date": plan.issue_date,
            "due_date": plan.due_date,
            "submission_date": plan.submission_date,
            "review_date": plan.review_date,
            "decision_date": plan.decision_date,
            "reviewed_by": plan.reviewed_by,
            "approved_by": plan.approved_by,
            "rejected_by": plan.rejected_by,
            "business_hold_active": plan.business_hold_active,
            "escalated": plan.escalated,
            "escalation_date": plan.escalation_date,
            "file_name": plan.file_name,
            "file_url": (
                get_fresh_doc_url(plan.file_url)
                if plan.file_url
                else (
                    get_fresh_doc_url(plan.document.file_url)
                    if getattr(plan, "document", None) and plan.document.file_url
                    else None
                )
            ),
            "file_notes": plan.file_notes,
            "supplier_comments": plan.supplier_comments,
            "internal_comments": plan.internal_comments,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "is_overdue": is_overdue,
            "days_past_due": days_past_due,
        }

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
            cycle_id = (
                getattr(instance, "id_cycle", None) if instance is not None else None
            )
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
        quality_certification_id: Optional[int] = None,
    ) -> bool:
        for field_name, value in merged_values.items():
            if field_name == "quality_certification":
                # Stored as id_certification now, not the label -- compared separately.
                continue
            if self._pluck(previous_input, field_name) != value:
                return True
        if self._pluck(previous_input, "id_certification") != quality_certification_id:
            return True
        if self._pluck(current_classification, "impact_score") != impact_score:
            return True
        if (
            self._pluck(current_classification, "strategic_mention")
            != strategic_mention
        ):
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
            if (
                new_value is not None
                and self._pluck(previous_impact_input, previous_field_name) != new_value
            ):
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
            if (
                self._prefer_decimal(
                    self._pluck(previous_operational_input, field_name)
                )
                != value
            ):
                return True
        if (
            self._prefer_decimal(
                self._pluck(previous_operational_input, "average_score")
            )
            != operational_score
        ):
            return True
        if (
            self._pluck(previous_operational_input, "operational_grade")
            != operational_grade
        ):
            return True
        return False





