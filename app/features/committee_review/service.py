"""Committee review workflow service."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundError
from app.db.models import (
    CommitteeDecision,
    CommitteeMember,
    CommitteeReview,
    SupplierSiteRelation,
)
from app.features.auth.models import AccessIdentity
from app.features.committee_review import schemas
from app.features.notifications.models import Notification
from app.shared.utils.email.email_service import EmailService

_TOKEN_EXPIRY_DAYS = 14
_email_svc = EmailService()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CommitteeReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Members ───────────────────────────────────────────────────────────

    async def list_members(self, active_only: bool = True) -> list[CommitteeMember]:
        stmt = select(CommitteeMember)
        if active_only:
            stmt = stmt.where(CommitteeMember.is_active.is_(True))
        stmt = stmt.order_by(CommitteeMember.id_member)
        return list((await self.db.execute(stmt)).scalars().all())

    async def create_member(
        self, payload: schemas.CommitteeMemberCreate
    ) -> CommitteeMember:
        existing = (
            await self.db.execute(
                select(CommitteeMember).where(
                    CommitteeMember.email == payload.email.lower()
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.name = payload.name
            existing.position = payload.position
            existing.is_active = payload.is_active
            await self.db.flush()
            return existing
        member = CommitteeMember(
            name=payload.name,
            position=payload.position,
            email=payload.email.lower(),
            is_active=payload.is_active,
        )
        self.db.add(member)
        await self.db.flush()
        return member

    async def update_member(
        self, id_member: int, payload: schemas.CommitteeMemberUpdate
    ) -> CommitteeMember:
        member = await self._get_member(id_member)
        if payload.name is not None:
            member.name = payload.name
        if payload.position is not None:
            member.position = payload.position
        if payload.email is not None:
            member.email = payload.email.lower()
        if payload.is_active is not None:
            member.is_active = payload.is_active
        await self.db.flush()
        return member

    async def _get_member(self, id_member: int) -> CommitteeMember:
        result = await self.db.execute(
            select(CommitteeMember).where(CommitteeMember.id_member == id_member)
        )
        m = result.scalar_one_or_none()
        if not m:
            raise NotFoundError("CommitteeMember", id_member)
        return m

    # ── Initiate review ───────────────────────────────────────────────────

    async def initiate_review(
        self, relation_id: int, initiated_by: str
    ) -> CommitteeReview:
        relation = await self._load_relation(relation_id)
        members = await self.list_members(active_only=True)
        if not members:
            raise AppException(
                status_code=400,
                message="No active committee members configured. Please add committee members before initiating a review.",
                error_code="NO_COMMITTEE_MEMBERS",
            )

        # Relation must be reviewer-approved before going to committee — otherwise
        # committee approval would set panel_decision without validation_status ever
        # reaching "approved", leaving the supplier invisible on the active panel.
        if relation.validation_status != "approved":
            raise AppException(
                status_code=409,
                message="The supplier relation must be approved by a reviewer before initiating a committee review.",
                error_code="RELATION_NOT_APPROVED",
            )

        # Once a relation is committee-approved, the outcome is sealed — no new review
        # cycle is allowed. Rejected relations may retry after improvements.
        if relation.panel_decision == "panel_add_committee_validated":
            raise AppException(
                status_code=409,
                message="This supplier relation has already been committee-approved and added to the panel. No further review cycle can be initiated.",
                error_code="RELATION_ALREADY_COMMITTEE_APPROVED",
            )

        # Prevent concurrent review cycles on the same relation — multiple active
        # token sets would create competing outcomes and break the auto-approval logic.
        existing_active = (
            await self.db.execute(
                select(CommitteeReview).where(
                    CommitteeReview.id_relation == relation_id,
                    CommitteeReview.status == "in_progress",
                )
            )
        ).scalar_one_or_none()
        if existing_active:
            raise AppException(
                status_code=409,
                message="A committee review is already in progress for this supplier relation. Complete or cancel it before starting a new one.",
                error_code="REVIEW_ALREADY_ACTIVE",
            )

        snapshot = await self._build_snapshot(relation)

        review = CommitteeReview(
            id_relation=relation_id,
            status="in_progress",
            initiated_by=initiated_by,
            initiated_at=_now(),
            supplier_snapshot=snapshot,
        )
        self.db.add(review)
        await self.db.flush()

        expiry = _now() + timedelta(days=_TOKEN_EXPIRY_DAYS)
        doc_url = await self._get_latest_eval_doc_url(relation_id)

        for member in members:
            token = str(uuid.uuid4())
            decision = CommitteeDecision(
                id_review=review.id_review,
                member_email=member.email,
                member_name=member.name,
                member_position=member.position,
                access_token=token,
                token_expires_at=expiry,
            )
            self.db.add(decision)
            await self.db.flush()

            vote_url = f"{settings.frontend_base_url}/committee-vote/{token}"
            await self._send_committee_email(
                member=member,
                snapshot=snapshot,
                vote_url=vote_url,
                doc_url=doc_url,
            )

        return review

    # ── Get review ────────────────────────────────────────────────────────

    async def get_review(self, review_id: int) -> schemas.CommitteeReviewResponse:
        review = await self._load_review(review_id)
        return self._to_response(review)

    async def get_latest_review_for_relation(
        self, relation_id: int
    ) -> Optional[schemas.CommitteeReviewResponse]:
        stmt = (
            select(CommitteeReview)
            .where(CommitteeReview.id_relation == relation_id)
            .options(selectinload(CommitteeReview.decisions))
            .order_by(CommitteeReview.initiated_at.desc())
            .limit(1)
        )
        review = (await self.db.execute(stmt)).scalar_one_or_none()
        if not review:
            return None
        return self._to_response(review)

    # ── Public vote ───────────────────────────────────────────────────────

    async def get_vote_by_token(self, token: str) -> schemas.VoteFormResponse:
        decision = await self._load_decision_by_token(token)
        review = await self._load_review(decision.id_review)

        if not decision.accessed_at:
            decision.accessed_at = _now()

        return schemas.VoteFormResponse(
            id_decision=decision.id_decision,
            id_review=decision.id_review,
            member_name=decision.member_name,
            member_position=decision.member_position,
            member_email=decision.member_email,
            already_decided=decision.decision is not None,
            decision=decision.decision,
            comments=decision.comments,
            decided_at=decision.decided_at,
            token_expires_at=decision.token_expires_at,
            supplier_snapshot=review.supplier_snapshot,
            review_status=review.status,
        )

    async def submit_vote(
        self,
        token: str,
        payload: schemas.SubmitDecisionRequest,
        ip_address: Optional[str],
    ) -> schemas.VoteFormResponse:
        decision = await self._load_decision_by_token(token)
        review = await self._load_review(decision.id_review)

        if review.status == "completed":
            raise AppException(
                status_code=409,
                message="This review has already been completed.",
                error_code="REVIEW_COMPLETED",
            )

        if decision.decision is not None:
            raise AppException(
                status_code=409,
                message="You have already submitted your decision for this review.",
                error_code="ALREADY_DECIDED",
            )

        decision.decision = payload.decision
        decision.comments = payload.comments
        decision.decided_at = _now()
        decision.ip_address = ip_address
        decision.suggested_supplier_status = payload.suggested_supplier_status or None
        decision.suggested_strategic_mention = (
            payload.suggested_strategic_mention or None
        )
        if not decision.accessed_at:
            decision.accessed_at = _now()

        await self.db.flush()

        # Reload all decisions with updated state
        review_fresh = await self._load_review(review.id_review)
        total = len(review_fresh.decisions)
        decided = sum(1 for d in review_fresh.decisions if d.decision is not None)

        # Notify VP_Conversion: member submitted
        await self._notify_vp(
            title=f"Committee decision received from {decision.member_name or decision.member_email}",
            body=f"Decision: {payload.decision.capitalize()}. {decided}/{total} responses received.",
            action_url=f"/committee-review/{review.id_review}",
            review_id=review.id_review,
        )

        if decided == total:
            review_fresh.status = "completed"
            review_fresh.all_decided_at = _now()
            await self.db.flush()

            all_approved = all(d.decision == "approved" for d in review_fresh.decisions)
            all_rejected = all(d.decision == "rejected" for d in review_fresh.decisions)

            if all_approved:
                # Unanimous approval → auto-apply final decision
                review_fresh.final_decision = "approved"
                review_fresh.final_decision_by = "auto"
                review_fresh.final_decision_at = _now()
                review_fresh.final_decision_comments = (
                    "Automatically approved: all committee members agreed."
                )
                await self.db.flush()

                relation = await self._load_relation(review_fresh.id_relation)
                relation.panel_decision = "panel_add_committee_validated"
                await self.db.flush()

                await self._notify_vp(
                    title="Supplier automatically added to panel",
                    body=f"All {total} committee members approved. The supplier has been automatically added to the panel.",
                    action_url=f"/committee-review/{review.id_review}",
                    review_id=review.id_review,
                )
            elif all_rejected:
                # Unanimous rejection → auto-apply final decision (symmetric with approval)
                review_fresh.final_decision = "rejected"
                review_fresh.final_decision_by = "auto"
                review_fresh.final_decision_at = _now()
                review_fresh.final_decision_comments = (
                    "Automatically rejected: all committee members rejected."
                )
                await self.db.flush()

                relation = await self._load_relation(review_fresh.id_relation)
                relation.panel_decision = "panel_reject"
                await self.db.flush()

                await self._notify_vp(
                    title="Supplier automatically rejected by committee",
                    body=f"All {total} committee members rejected. The supplier panel decision has been set to rejected.",
                    action_url=f"/committee-review/{review.id_review}",
                    review_id=review.id_review,
                )
            else:
                # Mixed result → VP must decide manually
                approved_count = sum(
                    1 for d in review_fresh.decisions if d.decision == "approved"
                )
                rejected_count = sum(
                    1 for d in review_fresh.decisions if d.decision == "rejected"
                )
                await self._notify_vp(
                    title="Committee review requires your decision",
                    body=(
                        f"All {total} members responded: {approved_count} approved, {rejected_count} rejected. "
                        "Please review the decisions and submit a final ruling."
                    ),
                    action_url=f"/committee-review/{review.id_review}",
                    review_id=review.id_review,
                )

        return await self.get_vote_by_token(token)

    # ── Final decision ────────────────────────────────────────────────────

    async def submit_final_decision(
        self,
        review_id: int,
        payload: schemas.FinalDecisionRequest,
        decided_by: str,
    ) -> schemas.CommitteeReviewResponse:
        review = await self._load_review(review_id)

        if review.status != "completed":
            raise AppException(
                status_code=409,
                message="Cannot submit a final decision before all committee members have voted.",
                error_code="REVIEW_NOT_COMPLETE",
            )

        if review.final_decision is not None:
            raise AppException(
                status_code=409,
                message="A final decision has already been recorded for this review and cannot be changed.",
                error_code="DECISION_ALREADY_FINALIZED",
            )

        review.final_decision = payload.decision
        review.final_decision_by = decided_by
        review.final_decision_at = _now()
        review.final_decision_comments = payload.comments
        await self.db.flush()

        # Update relation panel_decision to reflect committee outcome
        relation = await self._load_relation(review.id_relation)
        if payload.decision == "approved":
            relation.panel_decision = "panel_add_committee_validated"
        elif payload.decision == "rejected":
            relation.panel_decision = "panel_reject"
        await self.db.flush()

        return self._to_response(review)

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _load_relation(self, relation_id: int) -> SupplierSiteRelation:
        stmt = (
            select(SupplierSiteRelation)
            .where(SupplierSiteRelation.id_relation == relation_id)
            .options(
                selectinload(SupplierSiteRelation.site),
                selectinload(SupplierSiteRelation.supplier_unit),
            )
        )
        relation = (await self.db.execute(stmt)).scalar_one_or_none()
        if not relation:
            raise NotFoundError("SupplierSiteRelation", relation_id)
        return relation

    async def _load_review(self, review_id: int) -> CommitteeReview:
        stmt = (
            select(CommitteeReview)
            .where(CommitteeReview.id_review == review_id)
            .options(selectinload(CommitteeReview.decisions))
        )
        review = (await self.db.execute(stmt)).scalar_one_or_none()
        if not review:
            raise NotFoundError("CommitteeReview", review_id)
        return review

    async def _load_decision_by_token(self, token: str) -> CommitteeDecision:
        stmt = select(CommitteeDecision).where(CommitteeDecision.access_token == token)
        decision = (await self.db.execute(stmt)).scalar_one_or_none()
        if not decision:
            raise AppException(
                status_code=404,
                message="Invalid or expired committee link.",
                error_code="INVALID_TOKEN",
            )
        if decision.token_expires_at and decision.token_expires_at < _now():
            raise AppException(
                status_code=410,
                message="This committee link has expired.",
                error_code="TOKEN_EXPIRED",
            )
        return decision

    async def _build_snapshot(self, relation: SupplierSiteRelation) -> dict:
        unit = relation.supplier_unit
        site = relation.site
        return {
            "relation_id": relation.id_relation,
            "supplier_name": unit.supplier_code if unit else None,
            "supplier_code": unit.supplier_code if unit else None,
            "site_name": site.site_name if site else None,
            "family": unit.family if unit else None,
            "sub_family": unit.sub_family if unit else None,
            "panel_decision": relation.panel_decision,
            "final_grade": relation.final_grade,
            "supplier_status": relation.supplier_status,
            "strategic_mention": relation.strategic_mention,
        }

    async def _get_latest_eval_doc_url(self, relation_id: int) -> Optional[str]:
        from app.db.models import Document

        stmt = (
            select(Document)
            .where(
                Document.id_relation == relation_id,
                Document.document_type == "evaluation_reference",
            )
            .order_by(Document.uploaded_at.desc())
            .limit(1)
        )
        doc = (await self.db.execute(stmt)).scalar_one_or_none()
        return doc.file_url if doc else None

    async def _notify_vp(
        self, title: str, body: str, action_url: str, review_id: int
    ) -> None:
        stmt = select(AccessIdentity).where(
            AccessIdentity.access_profile == "vp_conversion",
            AccessIdentity.is_active.is_(True),
        )
        vp_identities = list((await self.db.execute(stmt)).scalars().all())
        for identity in vp_identities:
            notif = Notification(
                recipient_id=identity.id_identity,
                notification_type="committee_decision",
                title=title,
                body=body,
                action_url=action_url,
                metadata_json=json.dumps({"review_id": review_id}),
            )
            self.db.add(notif)

    async def _send_committee_email(
        self,
        member: CommitteeMember,
        snapshot: dict,
        vote_url: str,
        doc_url: Optional[str],
    ) -> None:
        supplier_name = snapshot.get("supplier_name") or "N/A"
        supplier_code = snapshot.get("supplier_code") or "N/A"
        site_name = snapshot.get("site_name") or "N/A"
        family = snapshot.get("family") or "N/A"
        grade = snapshot.get("final_grade") or "N/A"
        panel = snapshot.get("panel_decision") or "N/A"

        doc_section = (
            f'<p style="margin:8px 0"><a href="{doc_url}" style="color:#062B49">Download evaluation file</a></p>'
            if doc_url
            else "<p style='color:#888;margin:8px 0'>No evaluation file attached.</p>"
        )

        body_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
          <div style="background:#062B49;padding:20px 28px;border-radius:8px 8px 0 0">
            <h1 style="color:#fff;margin:0;font-size:18px">Committee Validation Request</h1>
            <p style="color:#a8c4d4;margin:4px 0 0;font-size:13px">Supplier Panel Decision — AvoCarbon Purchasing</p>
          </div>
          <div style="background:#f8fafc;padding:24px 28px;border:1px solid #e2e8f0;border-top:none">
            <p style="margin:0 0 16px">Dear <strong>{member.name}</strong> ({member.position}),</p>
            <p style="margin:0 0 16px">You have been requested to review a supplier panel decision requiring committee validation.</p>

            <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
              <tr style="background:#e8f0f8"><td style="padding:8px 12px;font-weight:bold;width:40%">Supplier</td><td style="padding:8px 12px">{supplier_name}</td></tr>
              <tr><td style="padding:8px 12px;font-weight:bold">Code</td><td style="padding:8px 12px">{supplier_code}</td></tr>
              <tr style="background:#e8f0f8"><td style="padding:8px 12px;font-weight:bold">Plant</td><td style="padding:8px 12px">{site_name}</td></tr>
              <tr><td style="padding:8px 12px;font-weight:bold">Family</td><td style="padding:8px 12px">{family}</td></tr>
              <tr style="background:#e8f0f8"><td style="padding:8px 12px;font-weight:bold">Grade</td><td style="padding:8px 12px">{grade}</td></tr>
              <tr><td style="padding:8px 12px;font-weight:bold">Panel Decision</td><td style="padding:8px 12px">{panel}</td></tr>
            </table>

            <p style="margin:0 0 8px"><strong>Evaluation File:</strong></p>
            {doc_section}

            <div style="margin:24px 0;text-align:center">
              <a href="{vote_url}" style="background:#062B49;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;font-size:14px">
                Submit My Decision →
              </a>
            </div>

            <p style="color:#888;font-size:12px;margin:16px 0 0">
              This link is unique to you and expires in {_TOKEN_EXPIRY_DAYS} days. Do not share it.
            </p>
          </div>
          <div style="background:#f1f5f9;padding:12px 28px;border-radius:0 0 8px 8px;border:1px solid #e2e8f0;border-top:none">
            <p style="color:#94a3b8;font-size:11px;margin:0">AvoCarbon Supplier Management Platform</p>
          </div>
        </div>
        """

        try:
            await _email_svc.send_email(
                subject=f"[Action Required] Committee Review: {supplier_name} ({supplier_code})",
                recipients=[member.email],
                body_html=body_html,
            )
        except Exception:
            pass  # email failure should not block the review creation

    def _to_response(self, review: CommitteeReview) -> schemas.CommitteeReviewResponse:
        decisions = [
            schemas.CommitteeDecisionResponse(
                id_decision=d.id_decision,
                member_email=d.member_email,
                member_name=d.member_name,
                member_position=d.member_position,
                decision=d.decision,
                comments=d.comments,
                decided_at=d.decided_at,
                accessed_at=d.accessed_at,
                suggested_supplier_status=d.suggested_supplier_status,
                suggested_strategic_mention=d.suggested_strategic_mention,
            )
            for d in (review.decisions or [])
        ]
        total = len(decisions)
        decided = sum(1 for d in decisions if d.decision is not None)
        approved = sum(1 for d in decisions if d.decision == "approved")
        rejected = sum(1 for d in decisions if d.decision == "rejected")

        return schemas.CommitteeReviewResponse(
            id_review=review.id_review,
            id_relation=review.id_relation,
            status=review.status,
            initiated_by=review.initiated_by,
            initiated_at=review.initiated_at,
            all_decided_at=review.all_decided_at,
            final_decision=review.final_decision,
            final_decision_by=review.final_decision_by,
            final_decision_at=review.final_decision_at,
            final_decision_comments=review.final_decision_comments,
            supplier_snapshot=review.supplier_snapshot,
            decisions=decisions,
            total_members=total,
            decided_count=decided,
            approved_count=approved,
            rejected_count=rejected,
        )
