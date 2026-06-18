"""Gate approval service."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.db.models import GateApprovalRequest, GateApprovalVote, Opportunity
from app.features.gate_approval import schemas
from app.features.purchasing_value.service import PurchasingValueService
from app.features.purchasing_value.schemas import GateDecisionRequest
from app.shared.utils.email.email_service import get_email_service

FRONTEND_BASE_URL = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:5173")
TOKEN_TTL_HOURS = 72


class GateApprovalService:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Create approval request + send emails
    # ------------------------------------------------------------------
    async def create_approval_request(
        self,
        opportunity_id: int,
        payload: schemas.GateApprovalCreateRequest,
        requested_by: str,
    ) -> GateApprovalRequest:
        pv_svc = PurchasingValueService(self.db)
        opp = await pv_svc.get_opportunity(opportunity_id)

        if opp.status == "Cancelled":
            raise AppException(400, "Cannot request approval for a cancelled opportunity.", "OPP_CANCELLED")

        now = datetime.utcnow()

        # Close any previously open request for this opportunity before creating a new one
        existing_result = await self.db.execute(
            select(GateApprovalRequest).where(
                GateApprovalRequest.opportunity_id == opportunity_id,
                GateApprovalRequest.status == "Pending",
            )
        )
        for old_req in existing_result.scalars().all():
            old_req.status = "Superseded"
            old_req.updated_at = now

        # Build snapshot using PurchasingValueService helper
        snapshot = pv_svc._build_opportunity_snapshot(opp)

        req = GateApprovalRequest(
            opportunity_id=opportunity_id,
            phase_from=opp.phase_status,
            requested_by=requested_by,
            requested_at=now,
            message=payload.message,
            status="Pending",
            opportunity_snapshot=snapshot,
            created_at=now,
            created_by=requested_by,
        )
        self.db.add(req)
        await self.db.flush()  # get request_id

        email_svc = get_email_service()
        expires_at = now + timedelta(hours=TOKEN_TTL_HOURS)

        # All approvers: plant manager first (flagged), then purchasing managers
        all_approvers = [
            (payload.plant_manager_email, True),
            *[(e, False) for e in payload.purchasing_manager_emails if e],
        ]

        for email, is_pm in all_approvers:
            token = str(uuid.uuid4())
            vote = GateApprovalVote(
                request_id=req.request_id,
                approver_email=email,
                access_token=token,
                token_expires_at=expires_at,
                is_plant_manager=is_pm,
                created_at=now,
                created_by=requested_by,
            )
            self.db.add(vote)

            link = f"{FRONTEND_BASE_URL}/approve/{token}"
            html = self._build_email_html(opp, req, link, payload.message, is_plant_manager=is_pm)
            try:
                email_svc.send_sync(
                    subject=f"[Action Required] Gate Approval — {opp.opportunity_name} ({opp.phase_status})",
                    recipients=[email],
                    body_html=html,
                )
            except Exception:
                pass  # Non-blocking — vote still created

        await self.db.flush()
        # Re-query with selectinload so votes (including access_token) are available
        result = await self.db.execute(
            select(GateApprovalRequest)
            .where(GateApprovalRequest.request_id == req.request_id)
            .options(selectinload(GateApprovalRequest.votes))
        )
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Get vote form data by token (public endpoint)
    # ------------------------------------------------------------------
    async def get_vote_by_token(self, token: str) -> schemas.VoteFormData:
        result = await self.db.execute(
            select(GateApprovalVote).where(GateApprovalVote.access_token == token)
        )
        vote = result.scalar_one_or_none()
        if not vote:
            raise AppException(404, "Approval link not found.", "VOTE_NOT_FOUND")

        # Mark first access here so it's included in the returned data
        if not vote.accessed_at:
            vote.accessed_at = datetime.utcnow()
            await self.db.flush()

        req_result = await self.db.execute(
            select(GateApprovalRequest)
            .where(GateApprovalRequest.request_id == vote.request_id)
            .options(selectinload(GateApprovalRequest.votes))
        )
        req = req_result.scalar_one_or_none()
        if not req:
            raise AppException(404, "Approval request not found.", "REQUEST_NOT_FOUND")

        snap: dict = req.opportunity_snapshot or {}

        # Build peer vote list for mutual visibility
        peer_votes = [
            schemas.PeerVote(
                approver_email=v.approver_email,
                is_plant_manager=v.is_plant_manager,
                decision=v.decision,
                decided_at=v.decided_at,
            )
            for v in req.votes
        ]

        def _f(key: str):
            """Extract numeric field from snapshot."""
            val = snap.get(key)
            if val is None:
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        return schemas.VoteFormData(
            vote_id=vote.vote_id,
            approver_email=vote.approver_email,
            already_decided=vote.decision is not None,
            decision=vote.decision,
            token_expires_at=vote.token_expires_at,
            requires_project_manager=bool(vote.is_plant_manager),
            all_votes=peer_votes,
            # Identity
            opportunity_name=snap.get("opportunity_name"),
            opportunity_type=snap.get("opportunity_type"),
            phase_from=req.phase_from,
            requested_by=req.requested_by,
            message=req.message,
            idea_owner=snap.get("idea_owner"),
            project_owner=snap.get("project_owner"),
            change_mode=snap.get("change_mode"),
            # STP pricing
            current_price=_f("current_price"),
            proposed_price=_f("proposed_price"),
            current_price_n1=_f("current_price_n1"),
            proposed_price_n1=_f("proposed_price_n1"),
            current_price_n2=_f("current_price_n2"),
            proposed_price_n2=_f("proposed_price_n2"),
            current_price_n3=_f("current_price_n3"),
            proposed_price_n3=_f("proposed_price_n3"),
            # Quantities
            annual_quantity_n1=_f("annual_quantity_n1"),
            annual_quantity_n2=_f("annual_quantity_n2"),
            annual_quantity_n3=_f("annual_quantity_n3"),
            # Savings
            saving_year_n=_f("saving_year_n"),
            saving_year_n1=_f("saving_year_n1"),
            saving_year_n2=_f("saving_year_n2"),
            saving_year_n3=_f("saving_year_n3"),
            period_saving=_f("period_saving"),
            expected_annual_saving=_f("expected_annual_saving"),
            # ROI & investment
            roi_percent=_f("roi_percent"),
            roi_period_percent=_f("roi_period_percent"),
            total_investment=_f("total_investment"),
            tooling_cost=_f("tooling_cost"),
            travel_cost=_f("travel_cost"),
            qualification_cost=_f("qualification_cost"),
            other_cost=_f("other_cost"),
            # Logistics
            incoterms_before=snap.get("incoterms_before"),
            incoterms_after=snap.get("incoterms_after"),
            top_days_before=_f("top_days_before"),
            top_days_after=_f("top_days_after"),
            # Planning
            planned_start_date=snap.get("planned_start_date"),
            planned_end_date=snap.get("planned_end_date"),
            duration_months=int(snap["duration_months"]) if snap.get("duration_months") else None,
        )

    # ------------------------------------------------------------------
    # Submit vote
    # ------------------------------------------------------------------
    async def submit_vote(
        self,
        token: str,
        payload: schemas.VoteSubmitRequest,
        ip_address: Optional[str] = None,
    ) -> schemas.VoteFormData:
        if payload.decision not in ("Approved", "Rejected", "Needs Review"):
            raise AppException(422, "Invalid decision value.", "INVALID_DECISION")

        result = await self.db.execute(
            select(GateApprovalVote).where(GateApprovalVote.access_token == token)
        )
        vote = result.scalar_one_or_none()
        if not vote:
            raise AppException(404, "Approval link not found.", "VOTE_NOT_FOUND")
        if vote.decision is not None:
            raise AppException(409, "Decision already recorded.", "ALREADY_DECIDED")
        if vote.token_expires_at and datetime.utcnow() > vote.token_expires_at:
            raise AppException(410, "This approval link has expired.", "TOKEN_EXPIRED")

        # Load the parent request (for phase context + snapshot)
        req_result = await self.db.execute(
            select(GateApprovalRequest).where(
                GateApprovalRequest.request_id == vote.request_id
            )
        )
        req = req_result.scalar_one_or_none()
        if not req or req.status != "Pending":
            raise AppException(
                410,
                "This approval request has been superseded or is no longer active.",
                "REQUEST_SUPERSEDED",
            )

        now = datetime.utcnow()
        if not vote.accessed_at:
            vote.accessed_at = now
        vote.decision = payload.decision
        vote.comment = payload.comment
        vote.decided_at = now
        vote.ip_address = ip_address
        vote.updated_at = now

        # Save the PM designation — do NOT notify yet; wait for full consensus
        if payload.project_manager_email and vote.is_plant_manager:
            vote.project_manager_email = payload.project_manager_email

        await self.db.flush()

        # Check consensus — PM notification fires only if all approve (Go)
        await self._check_consensus(vote.request_id)

        return await self.get_vote_by_token(token)

    # ------------------------------------------------------------------
    # Get approval status for an opportunity
    # ------------------------------------------------------------------
    async def get_approval_status(self, opportunity_id: int) -> list:
        result = await self.db.execute(
            select(GateApprovalRequest)
            .where(GateApprovalRequest.opportunity_id == opportunity_id)
            .options(selectinload(GateApprovalRequest.votes))
            .order_by(GateApprovalRequest.requested_at.desc())
        )
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Internal: check consensus after each vote
    # ------------------------------------------------------------------
    async def _check_consensus(self, request_id: int) -> None:
        req_result = await self.db.execute(
            select(GateApprovalRequest).where(
                GateApprovalRequest.request_id == request_id
            )
        )
        req = req_result.scalar_one_or_none()
        if not req or req.status != "Pending":
            return

        votes_result = await self.db.execute(
            select(GateApprovalVote)
            .where(GateApprovalVote.request_id == request_id)
            .order_by(GateApprovalVote.decided_at)
        )
        votes = votes_result.scalars().all()
        decided = [v for v in votes if v.decision is not None]

        if len(decided) < len(votes):
            return  # Still waiting for others

        now = datetime.utcnow()
        decisions = {v.decision for v in decided}
        plant_vote = next((v for v in decided if v.is_plant_manager), None)
        last_decider = decided[-1].approver_email if decided else req.requested_by

        if "Rejected" in decisions:
            consensus = "No Go"
        elif "Needs Review" in decisions:
            consensus = "Review"
        else:
            # Guard: Go requires a PM email for project-based opportunity types
            snap = req.opportunity_snapshot or {}
            opp_type = snap.get("opportunity_type", "")
            pm_email = plant_vote.project_manager_email if plant_vote else None
            if opp_type not in ("Negotiation", "Cash") and not pm_email:
                # Cannot complete Go without PM — leave request Pending until
                # plant manager re-votes or re-request is made with PM assigned
                return
            consensus = "Go"

        req.consensus_result = consensus
        req.status = "Completed"
        req.applied_at = now
        req.updated_at = now

        await self.db.flush()

        if consensus == "Go":
            pm_email = plant_vote.project_manager_email if plant_vote else None
            gate_payload = GateDecisionRequest(
                decision="Go",
                decided_by=last_decider,
                project_manager=pm_email,
            )
        elif consensus == "No Go":
            gate_payload = GateDecisionRequest(
                decision="No Go",
                decided_by=last_decider,
                comments="Gate rejected by approval panel.",
            )
        else:  # Review
            gate_payload = GateDecisionRequest(
                decision="Review",
                decided_by=last_decider,
                comments="Gate panel requested rework before re-submission.",
            )

        pv_svc = PurchasingValueService(self.db)
        try:
            await pv_svc.apply_gate_decision(req.opportunity_id, gate_payload)
            # Notify PM only after the phase advance actually succeeded
            if consensus == "Go" and plant_vote and plant_vote.project_manager_email:
                snap = req.opportunity_snapshot or {}
                self._notify_project_manager(
                    pm_email=plant_vote.project_manager_email,
                    opp_name=snap.get("opportunity_name") or "Opportunity",
                    opp_type=snap.get("opportunity_type") or "",
                    phase=req.phase_from or "Phase 0",
                    idea_owner=snap.get("idea_owner") or "",
                    approver_email=plant_vote.approver_email or "",
                )
        except Exception:
            pass  # Non-blocking — consensus is already recorded

    # ------------------------------------------------------------------
    # Email HTML builder
    # ------------------------------------------------------------------
    def _build_email_html(
        self,
        opp: Opportunity,
        req: GateApprovalRequest,
        link: str,
        message: Optional[str],
        is_plant_manager: bool = False,
    ) -> str:
        snap = req.opportunity_snapshot or {}

        def fmt(v, suffix="") -> str:
            if v is None:
                return "—"
            try:
                return f"{float(v):,.0f}{suffix}"
            except Exception:
                return str(v)

        rows = [
            ("Opportunity", opp.opportunity_name or "—"),
            ("Type", opp.opportunity_type or "—"),
            ("Phase", f"{req.phase_from} → next"),
            ("Owner (Idea)", snap.get("idea_owner") or "—"),
            ("Project Manager", snap.get("project_owner") or "—"),
            ("Change type", snap.get("change_mode") or "—"),
            ("Current price", fmt(snap.get("current_price"), " €")),
            ("Proposed price", fmt(snap.get("proposed_price"), " €")),
            ("Saving / year", fmt(snap.get("saving_year_n"), " €")),
            ("Period saving", fmt(snap.get("period_saving"), " €")),
            ("Planned start", snap.get("planned_start_date") or "—"),
            ("Duration", fmt(snap.get("duration_months"), " months")),
        ]
        table_rows = "".join(
            f"<tr><td style='padding:4px 12px 4px 0;color:#64748b;font-size:13px'>{k}</td>"
            f"<td style='padding:4px 0;font-size:13px;font-weight:600'>{v}</td></tr>"
            for k, v in rows
        )
        msg_block = (
            f"<p style='background:#f1f5f9;border-radius:8px;padding:10px 14px;"
            f"font-size:13px;color:#334155;margin:16px 0'>{message}</p>"
            if message else ""
        )
        pm_note = (
            "<p style='background:#fefce8;border:1px solid #fde68a;border-radius:8px;"
            "padding:10px 14px;font-size:13px;color:#92400e;margin:12px 0'>"
            "<strong>Note:</strong> As Plant Manager, if you approve this opportunity "
            "you will be asked to designate the <strong>Project Manager</strong> "
            "who will lead this project through Phase 1 and beyond.</p>"
            if is_plant_manager else ""
        )
        return f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="color:#1e40af;font-size:18px;margin-bottom:4px">Gate Approval Required</h2>
  <p style="color:#64748b;font-size:13px;margin-top:0">
    <strong>{req.requested_by}</strong> is requesting your approval to advance this opportunity.
  </p>
  {pm_note}
  {msg_block}
  <table style="width:100%;border-collapse:collapse;margin:16px 0">{table_rows}</table>
  <p style="font-size:12px;color:#94a3b8;margin-top:4px">
    Open the form to see the full dossier and give your decision.
  </p>
  <a href="{link}" style="display:inline-block;background:#2563eb;color:#fff;
     text-decoration:none;padding:12px 28px;border-radius:10px;font-size:14px;
     font-weight:600;margin-top:8px">Open Approval Form →</a>
  <p style="font-size:11px;color:#94a3b8;margin-top:20px">
    This link expires in 72 hours and can be used only once.
  </p>
</div>"""

    # ------------------------------------------------------------------
    # Notify designated Project Manager (Phase 0 approval)
    # ------------------------------------------------------------------
    def _notify_project_manager(
        self,
        pm_email: str,
        opp_name: str,
        opp_type: str,
        phase: str,
        idea_owner: str,
        approver_email: str,
    ) -> None:
        by_line = f"by {approver_email}" if approver_email else ""
        html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="color:#1e40af;font-size:18px;margin-bottom:4px">
    You have been assigned as Project Manager
  </h2>
  <p style="color:#64748b;font-size:13px;margin-top:0">
    All approvers have validated the <strong>{phase}</strong> gate {by_line}.
    You are designated as the <strong>Project Manager</strong> responsible for
    leading this project through Phase 1 and beyond.
  </p>
  <table style="width:100%;border-collapse:collapse;margin:16px 0">
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Opportunity</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{opp_name}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Type</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{opp_type}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Idea Owner</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{idea_owner}</td>
    </tr>
    <tr>
      <td style="padding:4px 12px 4px 0;color:#64748b;font-size:13px">Gate</td>
      <td style="padding:4px 0;font-size:13px;font-weight:600">{phase} — approved by all reviewers, advancing to Phase 1</td>
    </tr>
  </table>
  <p style="color:#64748b;font-size:12px;margin-top:4px">
    Please connect with the idea owner and the purchasing team to start the Phase 1 feasibility study.
  </p>
  <p style="font-size:11px;color:#94a3b8;margin-top:20px">
    Avocarbon · Suppliers Management · Purchasing Value
  </p>
</div>"""
        try:
            email_svc = get_email_service()
            email_svc.send_sync(
                subject=f"[Action Required] You are assigned as Project Manager — {opp_name}",
                recipients=[pm_email],
                body_html=html,
            )
        except Exception:
            pass  # Non-blocking
