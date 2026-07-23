"""Gate approval service."""
from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.db.models import GateApprovalRequest, GateApprovalVote, Opportunity
from app.features.auth.models import AccessIdentity
from app.features.gate_approval import schemas
from app.features.gate_approval.constants import (
    COMMITTEE_ELIGIBLE_PHASES,
    NEGOTIATION_APPROVER_ROLES,
    ROLE_PLANT_MANAGER,
    mandatory_roles_for_phase,
)
from app.features.notifications.service import NotificationService
from app.features.purchasing_value.service import PurchasingValueService
from app.features.purchasing_value.schemas import GateDecisionRequest
from app.features.purchasing_value.stp_pdf import generate_stp_pdf
from app.features.purchasing_value.full_report_pdf import generate_full_report_pdf
from app.shared.utils.email.email_service import get_email_service

# STP dossier only exists for these opportunity types (Negotiation/Cash have no STP format)
STP_ELIGIBLE_TYPES = {"Sourcing", "Technical Productivity"}


def _link_ttl_hours() -> int:
    """Configured lifetime (hours) of an approval link. 0 = never expires."""
    return settings.GATE_APPROVAL_LINK_EXPIRE_HOURS


def _token_expiry(now: datetime) -> Optional[datetime]:
    """Expiry timestamp to stamp on a new link, or None when expiry is disabled."""
    ttl = _link_ttl_hours()
    return now + timedelta(hours=ttl) if ttl else None


def _arrow(before, after) -> Optional[str]:
    """Render a before → after pair for the PM handover email, or None if both empty."""
    b = "" if before in (None, "") else str(before)
    a = "" if after in (None, "") else str(after)
    if not b and not a:
        return None
    return f"{b or '—'} → {a or '—'}"


@contextmanager
def _pdf_attachment(pdf_bytes: Optional[bytes], filename_prefix: str, opp_name: str):
    """Write pdf_bytes to a temp file for the duration of the block; yields
    (path, filename) or (None, None) if pdf_bytes is None. Cleans up on exit."""
    if pdf_bytes is None:
        yield None, None
        return
    safe = (opp_name or "opportunity").replace(" ", "_")[:50]
    filename = f"{filename_prefix}_{safe}.pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix=f"{filename_prefix}_") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        yield tmp_path, filename
    finally:
        os.unlink(tmp_path)


class GateApprovalService:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # In-app notification — best-effort, mirrors committee_review's per-vote
    # and outcome notifications. Silently no-ops if the email has no matching
    # AccessIdentity (e.g. an external/non-app approver) since notifications
    # can only target logged-in accounts, unlike email which reaches anyone.
    # ------------------------------------------------------------------
    async def _notify_by_email(
        self,
        email: Optional[str],
        notification_type: str,
        title: str,
        body: str,
        action_url: str,
    ) -> None:
        if not email:
            return
        try:
            result = await self.db.execute(
                select(AccessIdentity).where(AccessIdentity.email.ilike(email))
            )
            identity = result.scalar_one_or_none()
            if not identity:
                return
            await NotificationService(self.db).create_notification(
                recipient_id=identity.id_identity,
                notification_type=notification_type,
                title=title,
                body=body,
                action_url=action_url,
            )
        except Exception:
            pass  # Non-blocking — email notification already covers delivery

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
        is_negotiation = opp.opportunity_type == "Negotiation"

        if opp.status == "Cancelled":
            raise AppException(400, "Cannot request approval for a cancelled opportunity.", "OPP_CANCELLED")

        _GATE_ELIGIBLE_PHASES = ("Phase 0",)
        if opp.phase_status not in _GATE_ELIGIBLE_PHASES:
            raise AppException(
                400,
                f"Gate approval is not applicable in phase '{opp.phase_status}'. "
                "Only Phase 0 opportunities can go through this approval flow — "
                "use the sourcing committee gate for Phase 1-4.",
                "INVALID_PHASE_FOR_APPROVAL",
            )

        # This status guard used to only apply at Phase 0 — Phase 1/2/3 had no
        # equivalent check, so a request could be opened from any status (e.g.
        # "Complete"/"Cancelled"-adjacent states left over from an unrelated
        # flow). Generalized to every gate-eligible phase.
        if opp.status not in ("Working on it", "Needs Rework", "Awaiting Validation"):
            raise AppException(
                400,
                f"Cannot request approval: {opp.phase_status} opportunity must be in "
                "'Working on it' or 'Needs Rework' status.",
                "INVALID_PHASE_FOR_APPROVAL",
            )

        # STP completeness check — non-Negotiation/Cash types must have all key sections filled
        NO_STP_TYPES = {"Negotiation", "Cash"}
        if opp.phase_status == "Phase 0" and opp.opportunity_type not in NO_STP_TYPES:
            stp_risks = opp.stp_risks or {}
            stp_benefits = opp.stp_benefits or {}
            missing: list[str] = []
            if not (opp.scope_in and opp.customers):
                missing.append("Scope (Scope IN + Customers)")
            if not (opp.annual_quantity_n1 and float(opp.annual_quantity_n1) > 0):
                missing.append("Quantities (Annual N1)")
            if not (opp.current_price and opp.proposed_price):
                missing.append("Prices (Before/After)")
            if not (opp.incoterms_before and opp.incoterms_after and opp.country_after):
                missing.append("Logistics (Incoterms + Country after)")
            if not (stp_risks.get("material_indexation_before") and stp_risks.get("material_indexation_after")):
                missing.append("Risks (Material indexation Before/After)")
            if not (stp_benefits.get("if_we_do") or stp_benefits.get("if_not")):
                missing.append("Benefits (If we do)")
            if not (opp.phase1_weeks and int(opp.phase1_weeks) > 0):
                missing.append("Planning (Phase 1 weeks)")
            if missing:
                raise AppException(
                    422,
                    f"STP format incomplete. Please fill all required sections before sending an approval request: {', '.join(missing)}",
                    "STP_INCOMPLETE",
                )

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
        # Negotiation: the Plant Manager is only notified once the gate is
        # actually approved (Go) — not when the request is sent. Stash the
        # email on the snapshot now (no vote row exists for them to read it
        # from later) and fire the FYI email from _check_consensus on Go.
        if is_negotiation and payload.plant_manager_email:
            snapshot["_negotiation_plant_manager_email"] = payload.plant_manager_email

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

        # Transition opportunity to "Awaiting Validation" for all gate-eligible phases
        # so the UI shows the correct state while votes are being collected.
        if opp.phase_status in _GATE_ELIGIBLE_PHASES and opp.status in ("Working on it", "Needs Rework"):
            opp.status = "Awaiting Validation"
            opp.validation_request_sent_at = now
            opp.validation_request_sent_by = requested_by

        email_svc = get_email_service()
        expires_at = _token_expiry(now)

        approver_roles: dict[str, str] = {}

        if is_negotiation:
            # Negotiation: a single approver decides — either Purchasing
            # Director or VP Conversion. The Plant Manager, if given, is
            # notified by email only (see below) and never gets a vote.
            if not payload.approver_role or payload.approver_role not in NEGOTIATION_APPROVER_ROLES:
                raise AppException(
                    422,
                    "Select an approver role (Purchasing Director or VP Conversion).",
                    "APPROVER_REQUIRED",
                )
            if not payload.approver_email:
                raise AppException(422, "Approver email is required.", "APPROVER_REQUIRED")
            all_approvers = [(payload.approver_email, False)]
            approver_roles[payload.approver_email] = payload.approver_role
        else:
            # All approvers: plant manager first (flagged), then purchasing managers.
            # Deduplicate by email so a person listed in both roles gets one vote row
            # (as plant manager, which carries the PM designation responsibility).
            if not payload.plant_manager_email:
                raise AppException(422, "Plant Manager email is required.", "PLANT_MANAGER_REQUIRED")
            seen_emails: set[str] = set()
            all_approvers = []
            for email, is_pm in [
                (payload.plant_manager_email, True),
                *[(e, False) for e in payload.purchasing_manager_emails if e],
            ]:
                if email and email not in seen_emails:
                    seen_emails.add(email)
                    all_approvers.append((email, is_pm))

            if len(all_approvers) < 2:
                raise AppException(
                    400,
                    "Gate approval requires at least 2 voters (Plant Manager + at least one Purchasing Manager). "
                    "A single approver cannot satisfy the segregation-of-duties requirement.",
                    "INSUFFICIENT_APPROVERS",
                )

        # Phase 0 gate: attach the STP dossier (phase 0) for Sourcing / Technical
        # Productivity opportunities — Negotiation/Cash have no STP format.
        stp_pdf_bytes = (
            generate_stp_pdf(opp, phase=0)
            if opp.phase_status == "Phase 0" and opp.opportunity_type in STP_ELIGIBLE_TYPES
            else None
        )

        with _pdf_attachment(stp_pdf_bytes, "STP_Phase0", opp.opportunity_name) as (attach_path, attach_filename):
            for email, is_pm in all_approvers:
                token = str(uuid.uuid4())
                vote = GateApprovalVote(
                    request_id=req.request_id,
                    approver_email=email,
                    access_token=token,
                    token_expires_at=expires_at,
                    is_plant_manager=is_pm,
                    approver_role=approver_roles.get(email),
                    created_at=now,
                    created_by=requested_by,
                )
                self.db.add(vote)

                link = f"{settings.frontend_base_url}/approve/{token}"
                # PM designation only happens once, at the Phase 0 gate — the plant
                # manager who approves later phases (1-3) is not asked to redesignate
                # a PM that opp.project_owner already holds.
                pm_note_applies = is_pm and opp.phase_status in ("Assigned", "Phase 0")
                html = self._build_email_html(
                    opp, req, link, payload.message,
                    is_plant_manager=pm_note_applies,
                    approver_role=approver_roles.get(email),
                )
                try:
                    email_svc.send_sync(
                        subject=f"[Action Required] Gate Approval — {opp.opportunity_name} ({opp.phase_status})",
                        recipients=[email],
                        body_html=html,
                        attachment_path=attach_path,
                        attachment_filename=attach_filename,
                    )
                except Exception:
                    pass  # Non-blocking — vote still created
                await self._notify_by_email(
                    email,
                    "gate_approval_requested",
                    f"Gate approval requested — {opp.opportunity_name}",
                    f"Your review is requested for the {opp.phase_status} gate.",
                    link,
                )

        await self.db.flush()
        # Re-query with selectinload so votes (including access_token) are available
        result = await self.db.execute(
            select(GateApprovalRequest)
            .where(GateApprovalRequest.request_id == req.request_id)
            .options(selectinload(GateApprovalRequest.votes))
        )
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Create sourcing committee approval request (Phase 1-4)
    # ------------------------------------------------------------------
    async def create_committee_approval_request(
        self,
        opportunity_id: int,
        payload: schemas.CommitteeGateApprovalCreateRequest,
        requested_by: str,
    ) -> GateApprovalRequest:
        pv_svc = PurchasingValueService(self.db)
        opp = await pv_svc.get_opportunity(opportunity_id)

        if opp.status == "Cancelled":
            raise AppException(400, "Cannot request approval for a cancelled opportunity.", "OPP_CANCELLED")

        if opp.phase_status not in COMMITTEE_ELIGIBLE_PHASES:
            raise AppException(
                400,
                f"Sourcing committee approval is not applicable in phase '{opp.phase_status}'. "
                "Only Phase 1-4 opportunities can go through this approval flow.",
                "INVALID_PHASE_FOR_APPROVAL",
            )

        # Deliberately excludes "Under Committee Review" — once a committee gate is
        # requested the opportunity is locked until quorum is reached; a new request
        # can only be opened after the current one resolves (Go/No Go/Review resets
        # status via apply_gate_decision).
        if opp.status not in ("Working on it", "Needs Rework"):
            raise AppException(
                400,
                f"Cannot request approval: {opp.phase_status} opportunity must be in "
                "'Working on it' or 'Needs Rework' status.",
                "INVALID_PHASE_FOR_APPROVAL",
            )

        is_negotiation = opp.opportunity_type == "Negotiation"
        tier: Optional[str] = None

        # Required-field guard at the gate (not at save — saving is intentionally
        # permissive). Mirrors the frontend committee checklist:
        #  - Phase 3/4: the real deployment start (real_start_date) must be recorded
        #    before a review can be requested — savings are flowing, so the timing
        #    must be firm. (Fill it on the opportunity form or inline in Budgeting.)
        #  - Non-Negotiation types: the execution start date must be entered from the
        #    first committee phase onward (Negotiation has no execution/tooling phase).
        gate_missing: list[str] = []
        if opp.phase_status in ("Phase 3", "Phase 4") and opp.real_start_date is None:
            gate_missing.append("Deployment Start Date (Real Savings Start)")
        if not is_negotiation and opp.execution_start_date is None:
            gate_missing.append("Execution Start Date")
        # "Proposed New Supplier — After" becomes mandatory from Phase 1: the field
        # is a panel dropdown that writes proposed_supplier_id (the Phase 0 free-text
        # candidate lives in proposed_supplier_name and is optional). Negotiation/Cash
        # skip PLD scoring, so they carry no proposed supplier link.
        if opp.opportunity_type not in ("Negotiation", "Cash") and opp.proposed_supplier_id is None:
            gate_missing.append("Proposed New Supplier — After (from panel)")
        # Purchasing Owner + Conversion Owner become mandatory from Phase 2: the
        # Purchasing Owner receives tracking/escalation alerts and the Conversion
        # Owner enters the monthly actuals that start flowing in execution — the
        # opportunity can't be tracked past Phase 2 without them. All types.
        if opp.phase_status in ("Phase 2", "Phase 3", "Phase 4"):
            if not opp.purchasing_owner:
                gate_missing.append("Purchasing Owner")
            if not opp.conversion_owner:
                gate_missing.append("Conversion Owner")
        if gate_missing:
            raise AppException(
                422,
                "Cannot request approval — fill these required fields first: "
                f"{', '.join(gate_missing)}.",
                "MISSING_REQUIRED_FIELDS",
            )

        if is_negotiation:
            # No committee tier for Negotiation — a single approver (Purchasing
            # Director or VP Conversion) decides every phase. Other roles
            # submitted (e.g. leftover Plant Manager/Project Leader fields) are
            # simply ignored — they never get a vote.
            chosen = [
                a for a in payload.approvers
                if a.role in NEGOTIATION_APPROVER_ROLES and a.email
            ]
            if not chosen:
                raise AppException(
                    422,
                    f"{opp.phase_status} requires an approver: Purchasing Director or VP Conversion.",
                    "MISSING_MANDATORY_APPROVER",
                )
            approvers_to_notify = chosen[:1]
        else:
            # Committee level is chosen once (expected at Phase 1) and locked for the
            # rest of the opportunity's life — later phases cannot silently switch tier.
            if opp.committee_level:
                tier = opp.committee_level
            else:
                if not payload.committee_level:
                    raise AppException(
                        422,
                        "A committee level (Light, Intermediate or Full) must be selected "
                        "for this opportunity's first sourcing committee gate.",
                        "COMMITTEE_LEVEL_REQUIRED",
                    )
                tier = payload.committee_level
                opp.committee_level = tier

            mandatory_roles = mandatory_roles_for_phase(opp.phase_status, tier)
            provided_roles = {a.role: a.email for a in payload.approvers}
            missing_roles = [r for r in mandatory_roles if r not in provided_roles]
            if missing_roles:
                raise AppException(
                    422,
                    f"{opp.phase_status} requires an approver for: {', '.join(missing_roles)}.",
                    "MISSING_MANDATORY_APPROVER",
                )
            approvers_to_notify = payload.approvers

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

        snapshot = pv_svc._build_opportunity_snapshot(opp)

        req = GateApprovalRequest(
            opportunity_id=opportunity_id,
            phase_from=opp.phase_status,
            requested_by=requested_by,
            requested_at=now,
            message=payload.message,
            status="Pending",
            committee_level=tier,
            opportunity_snapshot=snapshot,
            created_at=now,
            created_by=requested_by,
        )
        self.db.add(req)
        await self.db.flush()  # get request_id

        # Phase 1-4 committee gates use "Under Committee Review" (distinct from the
        # Phase 0 gate's "Awaiting Validation") — the opportunity stays locked in
        # this status until every mandatory approver has voted and consensus is
        # reached (see _check_consensus), then apply_gate_decision advances it.
        opp.status = "Under Committee Review"
        opp.validation_request_sent_at = now
        opp.validation_request_sent_by = requested_by

        email_svc = get_email_service()
        expires_at = _token_expiry(now)

        # Attach the STP dossier at Phase 1 (Sourcing / Technical Productivity only,
        # mirrors the Phase 0 gate), and the Full Opportunity Report at Phase 3/4
        # (any type — a live cross-phase status snapshot, not an STP proposal doc).
        attach_bytes: Optional[bytes] = None
        attach_prefix = ""
        if opp.phase_status == "Phase 1" and opp.opportunity_type in STP_ELIGIBLE_TYPES:
            attach_bytes = generate_stp_pdf(opp, phase=1)
            attach_prefix = "STP_Phase1"
        elif opp.phase_status in ("Phase 3", "Phase 4"):
            attach_bytes = generate_full_report_pdf(opp)
            attach_prefix = "FullReport"

        # One vote row per role, even if the same email covers several roles
        # (e.g. a single tester approving as both Purchasing Director and CEO) —
        # each role gets its own token/link and casts its own decision.
        subject_suffix = f", {tier} Committee)" if tier else ")"
        with _pdf_attachment(attach_bytes, attach_prefix, opp.opportunity_name) as (attach_path, attach_filename):
            for approver in approvers_to_notify:
                if not approver.email:
                    continue

                token = str(uuid.uuid4())
                vote = GateApprovalVote(
                    request_id=req.request_id,
                    approver_email=approver.email,
                    access_token=token,
                    token_expires_at=expires_at,
                    is_plant_manager=(approver.role == ROLE_PLANT_MANAGER),
                    approver_role=approver.role,
                    created_at=now,
                    created_by=requested_by,
                )
                self.db.add(vote)

                link = f"{settings.frontend_base_url}/approve/{token}"
                html = self._build_email_html(
                    opp, req, link, payload.message,
                    approver_role=approver.role, committee_level=tier,
                )
                try:
                    email_svc.send_sync(
                        subject=f"[Action Required] Gate Approval — {opp.opportunity_name} ({opp.phase_status}{subject_suffix}",
                        recipients=[approver.email],
                        body_html=html,
                        attachment_path=attach_path,
                        attachment_filename=attach_filename,
                    )
                except Exception:
                    pass  # Non-blocking — vote still created
                await self._notify_by_email(
                    approver.email,
                    "gate_approval_requested",
                    f"Gate approval requested — {opp.opportunity_name}",
                    f"Your role: {approver.role}"
                    + (f" · {tier} Committee" if tier else "")
                    + f" · {opp.phase_status} gate.",
                    link,
                )

        await self.db.flush()
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
                approver_role=v.approver_role,
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
            approver_role=vote.approver_role,
            committee_level=req.committee_level,
            already_decided=vote.decision is not None,
            decision=vote.decision,
            token_expires_at=vote.token_expires_at,
            # The Plant Manager designates the Project Manager on their approval
            # vote at EVERY gate (Phase 0-4) — the field is pre-filled with the
            # current designation (project_owner) and can be overridden. The PM is
            # notified only once the whole panel approves (see _check_consensus).
            requires_project_manager=bool(vote.is_plant_manager),
            all_votes=peer_votes,
            # Identity
            opportunity_name=snap.get("opportunity_name"),
            opportunity_type=snap.get("opportunity_type"),
            description=snap.get("description"),
            phase_from=req.phase_from,
            requested_by=req.requested_by,
            message=req.message,
            idea_owner=snap.get("idea_owner"),
            project_owner=snap.get("project_owner"),
            change_mode=snap.get("change_mode"),
            # Scope
            scope_in=snap.get("scope_in"),
            scope_out=snap.get("scope_out"),
            customers=snap.get("customers"),
            # Supplier before/after
            proposed_supplier_name=snap.get("proposed_supplier_name"),
            country_after=snap.get("country_after"),
            supplier_asked=snap.get("supplier_asked"),
            supplier_asked_result=snap.get("supplier_asked_result"),
            # Risks & benefits
            stp_risks=snap.get("stp_risks"),
            stp_benefits=snap.get("stp_benefits"),
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
            annual_quantity_n4=_f("annual_quantity_n4"),
            # Savings
            saving_year_n=_f("saving_year_n"),
            saving_year_n1=_f("saving_year_n1"),
            saving_year_n2=_f("saving_year_n2"),
            saving_year_n3=_f("saving_year_n3"),
            period_saving=_f("period_saving"),
            expected_annual_saving=_f("expected_annual_saving"),
            # Cash
            cash_impact=_f("cash_impact"),
            cash_inventory_gap=_f("cash_inventory_gap"),
            cash_ap_gap=_f("cash_ap_gap"),
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
            place_of_incoterms_before=snap.get("place_of_incoterms_before"),
            place_of_incoterms_after=snap.get("place_of_incoterms_after"),
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
        # Gate enforcement on the CURRENT config (not the stored stamp): when
        # expiry is disabled (_link_ttl_hours() == 0), even links that carry an
        # old expiry timestamp are accepted again — no DB backfill needed.
        if _link_ttl_hours() and vote.token_expires_at and datetime.utcnow() > vote.token_expires_at:
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

        # Notify the requester in-app with live progress — mirrors
        # committee_review's per-vote _notify_vp pattern.
        votes_result = await self.db.execute(
            select(GateApprovalVote).where(GateApprovalVote.request_id == req.request_id)
        )
        all_votes = votes_result.scalars().all()
        decided_count = sum(1 for v in all_votes if v.decision is not None)
        snap = req.opportunity_snapshot or {}
        opp_name = snap.get("opportunity_name") or "the opportunity"
        await self._notify_by_email(
            req.requested_by,
            "gate_approval_vote_cast",
            f"{vote.approver_email} {payload.decision.lower()} the {req.phase_from} gate",
            f"{opp_name} — {decided_count}/{len(all_votes)} responses received.",
            f"/purchasing-value?opp={req.opportunity_id}",
        )

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
    # Send reminder emails to approvers who have not yet decided
    # ------------------------------------------------------------------
    async def send_reminders(self, opportunity_id: int, requested_by: str) -> dict:
        """Re-send the approval email — reusing each approver's *existing* link —
        to every voter on the opportunity's open (Pending) gate request(s) who
        has not yet recorded a decision. No new tokens are created, so the
        original ``/approve/{token}`` links stay valid and anyone who already
        voted is left alone."""
        pv_svc = PurchasingValueService(self.db)
        opp = await pv_svc.get_opportunity(opportunity_id)

        result = await self.db.execute(
            select(GateApprovalRequest)
            .where(
                GateApprovalRequest.opportunity_id == opportunity_id,
                GateApprovalRequest.status == "Pending",
            )
            .options(selectinload(GateApprovalRequest.votes))
        )
        pending_requests = result.scalars().all()
        if not pending_requests:
            raise AppException(
                400,
                "No pending approval request to remind — this opportunity is not awaiting validation.",
                "NO_PENDING_REQUEST",
            )

        email_svc = get_email_service()
        now = datetime.utcnow()
        reminded: list[str] = []

        # Re-attach the same dossier the original request carried, keyed on the
        # opportunity's current phase (mirrors the create_* attachment logic).
        attach_bytes: Optional[bytes] = None
        attach_prefix = ""
        if opp.phase_status == "Phase 0" and opp.opportunity_type in STP_ELIGIBLE_TYPES:
            attach_bytes = generate_stp_pdf(opp, phase=0)
            attach_prefix = "STP_Phase0"
        elif opp.phase_status == "Phase 1" and opp.opportunity_type in STP_ELIGIBLE_TYPES:
            attach_bytes = generate_stp_pdf(opp, phase=1)
            attach_prefix = "STP_Phase1"
        elif opp.phase_status in ("Phase 3", "Phase 4"):
            attach_bytes = generate_full_report_pdf(opp)
            attach_prefix = "FullReport"

        with _pdf_attachment(attach_bytes, attach_prefix, opp.opportunity_name) as (attach_path, attach_filename):
            for req in pending_requests:
                tier = req.committee_level
                subject_suffix = f", {tier} Committee)" if tier else ")"
                for vote in req.votes:
                    # Skip anyone who already voted (or has no address to reach).
                    if vote.decision is not None or not vote.approver_email:
                        continue
                    link = f"{settings.frontend_base_url}/approve/{vote.access_token}"
                    pm_note_applies = vote.is_plant_manager and opp.phase_status in ("Assigned", "Phase 0")
                    html = self._build_email_html(
                        opp, req, link, req.message,
                        is_plant_manager=pm_note_applies,
                        approver_role=vote.approver_role,
                        committee_level=tier,
                    )
                    try:
                        email_svc.send_sync(
                            subject=f"[Reminder] Gate Approval — {opp.opportunity_name} ({opp.phase_status}{subject_suffix}",
                            recipients=[vote.approver_email],
                            body_html=html,
                            attachment_path=attach_path,
                            attachment_filename=attach_filename,
                        )
                    except Exception:
                        pass  # Non-blocking — a failed reminder must not error the request
                    await self._notify_by_email(
                        vote.approver_email,
                        "gate_approval_requested",
                        f"Reminder: gate approval pending — {opp.opportunity_name}",
                        f"A decision is still needed for the {opp.phase_status} gate.",
                        link,
                    )
                    # Record the reminder as history on the vote row.
                    vote.reminder_count = (vote.reminder_count or 0) + 1
                    vote.last_reminded_at = now
                    reminded.append(vote.approver_email)

        if not reminded:
            raise AppException(
                400,
                "Everyone has already recorded their decision — no reminders sent.",
                "NOTHING_TO_REMIND",
            )

        return {"reminded": reminded, "count": len(reminded)}

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
        decisions = [v.decision for v in decided]
        plant_vote = next((v for v in decided if v.is_plant_manager), None)
        last_decider = decided[-1].approver_email if decided else req.requested_by

        # Consensus rules:
        #  - No Go (opportunity cancelled) ONLY when EVERY voter rejects — a
        #    single rejection is not enough to kill the opportunity.
        #  - Go when every voter approves.
        #  - Any other mix (at least one Needs Review, or a non-unanimous
        #    rejection) falls back to Review → the opportunity goes to
        #    "Needs Rework" so the buyer/PM can address the concerns and
        #    re-submit, rather than being terminated.
        if decisions and all(d == "Rejected" for d in decisions):
            consensus = "No Go"
        elif all(d == "Approved" for d in decisions):
            # Guard: Go requires a PM email for project-based opportunity types —
            # but only at the Phase 0 gate, where the PM is first designated.
            # Phase 1-3 gates advance an opportunity that already has
            # opp.project_owner set; apply_gate_decision doesn't even read
            # payload.project_manager outside the Assigned/Phase 0 branch, so
            # requiring it again here would block consensus forever.
            snap = req.opportunity_snapshot or {}
            opp_type = snap.get("opportunity_type", "")
            pm_email = plant_vote.project_manager_email if plant_vote else None
            if (
                req.phase_from in ("Assigned", "Phase 0")
                and opp_type not in ("Negotiation", "Cash")
                and not pm_email
            ):
                # Cannot complete Go without PM — leave request Pending until
                # plant manager re-votes or re-request is made with PM assigned
                return
            consensus = "Go"
        else:
            # Mixed panel — at least one Needs Review, or some (but not all)
            # rejections. Send the opportunity back for rework rather than
            # cancelling it.
            consensus = "Review"

        if consensus == "Go":
            # PM designated by the plant manager on this gate; fall back to the
            # one carried over from Phase 0 (opportunity_snapshot.project_owner).
            pm_email = (plant_vote.project_manager_email if plant_vote else None) or (
                req.opportunity_snapshot or {}
            ).get("project_owner")
            gate_payload = GateDecisionRequest(
                decision="Go",
                decided_by=last_decider,
                project_manager=pm_email,
            )
        elif consensus == "No Go":
            gate_payload = GateDecisionRequest(
                decision="No Go",
                decided_by=last_decider,
                comments="Gate rejected unanimously by approval panel.",
            )
        else:  # Review
            gate_payload = GateDecisionRequest(
                decision="Review",
                decided_by=last_decider,
                comments="Gate panel requested rework before re-submission.",
            )

        # Apply the phase transition first — if it fails, let the exception propagate
        # so the request is NOT marked Completed and stays correctable.
        # _via_gate_approval=True bypasses the direct-call guard for Phase 1-3.
        pv_svc = PurchasingValueService(self.db)
        await pv_svc.apply_gate_decision(req.opportunity_id, gate_payload, _via_gate_approval=True)

        # Phase transition succeeded — now seal the approval request.
        req.consensus_result = consensus
        req.status = "Completed"
        req.applied_at = now
        req.updated_at = now
        await self.db.flush()

        # Notify the requester in-app that the outcome was applied — mirrors
        # committee_review's "auto-applied" notification once the round completes.
        snap = req.opportunity_snapshot or {}
        opp_name = snap.get("opportunity_name") or "Opportunity"
        await self._notify_by_email(
            req.requested_by,
            "gate_approval_outcome",
            f"Gate outcome: {consensus} — {opp_name}",
            f"The {req.phase_from} gate consensus is {consensus}.",
            f"/purchasing-value?opp={req.opportunity_id}",
        )

        # Notify the Project Manager — at EVERY gate (Phase 0-4), and ONLY once
        # the whole panel has approved (we are inside the consensus == "Go"
        # branch, so a single Rework/Needs Review never reaches here). Uses the
        # PM designated on this gate, falling back to the Phase 0 carry-over.
        # Best-effort: failure must not roll back the transition.
        if consensus == "Go" and pm_email:
            try:
                self._notify_project_manager(
                    pm_email=pm_email,
                    snap=snap,
                    phase=req.phase_from or "Phase 0",
                    approver_email=(plant_vote.approver_email if plant_vote else "")
                    or "",
                    opportunity_id=req.opportunity_id,
                )
            except Exception:
                pass
            await self._notify_by_email(
                pm_email,
                "gate_approval_pm_assigned",
                f"You are assigned as Project Manager — {snap.get('opportunity_name') or 'Opportunity'}",
                f"All approvers validated the {req.phase_from} gate. You now lead this project.",
                f"/purchasing-value?opp={req.opportunity_id}",
            )

        # Negotiation: the Plant Manager (stashed on the snapshot at request
        # time — see create_approval_request) is notified only now, after the
        # gate is approved — not when the request was sent, and never on
        # No Go/Review. Best-effort, same as the PM email above.
        pm_info_email = snap.get("_negotiation_plant_manager_email")
        if consensus == "Go" and pm_info_email:
            self._send_info_email(
                email=pm_info_email,
                opp_name=snap.get("opportunity_name") or "Opportunity",
                opp_type=snap.get("opportunity_type") or "",
                phase=req.phase_from or "Phase 0",
                message=req.message,
            )

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
        approver_role: Optional[str] = None,
        committee_level: Optional[str] = None,
    ) -> str:
        snap = req.opportunity_snapshot or {}

        def fmt(v, suffix="", decimals: int = 0) -> str:
            if v is None:
                return "—"
            try:
                fv = float(v)
                return f"{fv:,.{decimals}f}{suffix}"
            except Exception:
                return str(v)

        rows = [
            ("Opportunity", opp.opportunity_name or "—"),
            ("Type", opp.opportunity_type or "—"),
            ("Phase", f"{req.phase_from} → next"),
        ]
        if opp.description:
            rows.append(("Description", opp.description))
        if approver_role:
            rows.append(("Your role", f"{approver_role}" + (f" · {committee_level} Committee" if committee_level else "")))
        rows.append(("Owner (Idea)", snap.get("idea_owner") or "—"))
        # Negotiation has no STP format (no project/price breakdown) — show the
        # flat saving + cash figures instead of the STP-only fields below.
        if opp.opportunity_type == "Negotiation":
            if snap.get("expected_annual_saving") not in (None, 0):
                rows.append(("Est. Annual Saving", fmt(snap.get("expected_annual_saving"), " €")))
            if snap.get("cash_impact") not in (None, 0):
                rows.append(("Cash Impact", fmt(snap.get("cash_impact"), " €")))
        else:
            rows += [
                ("Project Manager", snap.get("project_owner") or "—"),
                ("Change type", snap.get("change_mode") or "—"),
                ("Current price", fmt(snap.get("current_price"), " €", 4)),
                ("Proposed price", fmt(snap.get("proposed_price"), " €", 4)),
                ("Saving / year", fmt(snap.get("saving_year_n"), " €")),
                ("Period saving", fmt(snap.get("period_saving"), " €")),
            ]
        rows += [
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
        ttl = _link_ttl_hours()
        link_validity_note = (
            f"This link expires in {ttl} hours and can be used only once."
            if ttl else "This link stays valid until you respond and can be used only once."
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
    {link_validity_note}
  </p>
</div>"""

    # ------------------------------------------------------------------
    # Plain FYI email — no vote, no token (Negotiation Plant Manager notice,
    # sent only once the gate is approved — see _check_consensus)
    # ------------------------------------------------------------------
    def _send_info_email(
        self,
        email: str,
        opp_name: str,
        opp_type: str,
        phase: str,
        message: Optional[str],
    ) -> None:
        msg_block = (
            f"<p style='background:#f1f5f9;border-radius:8px;padding:10px 14px;"
            f"font-size:13px;color:#334155;margin:16px 0'>{message}</p>"
            if message else ""
        )
        html = f"""
<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
  <h2 style="color:#1e40af;font-size:18px;margin-bottom:4px">Gate Approved — FYI</h2>
  <p style="color:#64748b;font-size:13px;margin-top:0">
    <strong>{opp_name}</strong> ({opp_type}, {phase}) has been approved.
    This is an informational notice only — no action or vote was required
    from you.
  </p>
  {msg_block}
</div>"""
        try:
            get_email_service().send_sync(
                subject=f"[FYI] Gate approved — {opp_name}",
                recipients=[email],
                body_html=html,
            )
        except Exception:
            pass  # Non-blocking

    # ------------------------------------------------------------------
    # Notify designated Project Manager (Phase 0 approval)
    # ------------------------------------------------------------------
    def _notify_project_manager(
        self,
        pm_email: str,
        snap: dict,
        phase: str,
        approver_email: str,
        opportunity_id: Optional[int] = None,
    ) -> None:
        """Modern, self-contained handover email to the designated Project Manager.
        Includes the full opportunity dossier so the PM can track it in their own
        system without opening the app."""
        snap = snap or {}
        cur = snap.get("currency") or "EUR"
        sym = {"EUR": "€", "USD": "$", "RMB": "¥", "INR": "₹"}.get(cur, cur + " ")
        opp_name = snap.get("opportunity_name") or "Opportunity"
        opp_type = snap.get("opportunity_type") or "—"
        is_nego = opp_type == "Negotiation"

        def money(v, decimals: int = 0) -> Optional[str]:
            if v in (None, ""):
                return None
            try:
                return f"{sym}{float(v):,.{decimals}f}"
            except Exception:
                return str(v)

        def txt(v) -> Optional[str]:
            if v in (None, ""):
                return None
            return str(v)

        def qty(v) -> Optional[str]:
            if v in (None, ""):
                return None
            try:
                return f"{float(v):,.0f}"
            except Exception:
                return str(v)

        # (title, [(label, value_or_None), ...]) — empty rows/sections are dropped.
        risks = snap.get("stp_risks") or {}
        benefits = snap.get("stp_benefits") or {}
        sections = [
            ("Identity", [
                ("Type", opp_type),
                ("Priority", txt(snap.get("priority_category"))),
                ("Description", txt(snap.get("description"))),
                ("Idea owner", txt(snap.get("idea_owner"))),
                ("Purchasing owner", txt(snap.get("purchasing_owner"))),
                ("Conversion owner", txt(snap.get("conversion_owner"))),
                ("Change type", txt(snap.get("change_mode"))),
                ("Currency", cur + (f" · FX→EUR {snap.get('fx_rate_to_eur')}" if snap.get("fx_rate_to_eur") else "")),
            ]),
            ("Scope", [
                ("Scope in", txt(snap.get("scope_in"))),
                ("Scope out", txt(snap.get("scope_out"))),
                ("Customers", txt(snap.get("customers"))),
            ]),
            ("Supplier", [
                ("Proposed supplier", txt(snap.get("proposed_supplier_name"))),
                ("Country (after)", txt(snap.get("country_after"))),
                ("Supplier consulted", "Yes" if snap.get("supplier_asked") else None),
                ("Consultation result", txt(snap.get("supplier_asked_result"))),
            ]),
            ("Pricing", None if is_nego else [
                ("Current price", money(snap.get("current_price"), 4)),
                ("Proposed price", money(snap.get("proposed_price"), 4)),
                ("Current price N+1", money(snap.get("current_price_n1"), 4)),
                ("Proposed price N+1", money(snap.get("proposed_price_n1"), 4)),
                ("Current price N+2", money(snap.get("current_price_n2"), 4)),
                ("Proposed price N+2", money(snap.get("proposed_price_n2"), 4)),
                ("Current price N+3", money(snap.get("current_price_n3"), 4)),
                ("Proposed price N+3", money(snap.get("proposed_price_n3"), 4)),
            ]),
            ("Quantities", None if is_nego else [
                ("Annual qty N+1", qty(snap.get("annual_quantity_n1"))),
                ("Annual qty N+2", qty(snap.get("annual_quantity_n2"))),
                ("Annual qty N+3", qty(snap.get("annual_quantity_n3"))),
                ("Annual qty N+4", qty(snap.get("annual_quantity_n4"))),
            ]),
            ("Savings & ROI", [
                ("Expected annual saving", money(snap.get("expected_annual_saving"))),
                ("Saving year N", money(snap.get("saving_year_n"))),
                ("Saving year N+1", money(snap.get("saving_year_n1"))),
                ("Saving year N+2", money(snap.get("saving_year_n2"))),
                ("Saving year N+3", money(snap.get("saving_year_n3"))),
                ("Period saving (total)", money(snap.get("period_saving"))),
                ("ROI", f"{snap.get('roi_percent')}%" if snap.get("roi_percent") not in (None, "") else None),
                ("ROI (period)", f"{snap.get('roi_period_percent')}%" if snap.get("roi_period_percent") not in (None, "") else None),
            ]),
            ("Investment & costs", [
                ("Total investment", money(snap.get("total_investment"))),
                ("Tooling", money(snap.get("tooling_cost"))),
                ("Travel", money(snap.get("travel_cost"))),
                ("Qualification", money(snap.get("qualification_cost"))),
                ("Other", money(snap.get("other_cost"))),
            ]),
            ("Cash", [
                ("Cash impact", money(snap.get("cash_impact"))),
                ("Inventory gap", money(snap.get("cash_inventory_gap"))),
                ("A/P gap", money(snap.get("cash_ap_gap"))),
            ]),
            ("Logistics", None if is_nego else [
                ("Incoterms (before → after)", _arrow(snap.get("incoterms_before"), snap.get("incoterms_after"))),
                ("Incoterms place (before → after)", _arrow(snap.get("place_of_incoterms_before"), snap.get("place_of_incoterms_after"))),
                ("TOP days (before → after)", _arrow(snap.get("top_days_before"), snap.get("top_days_after"))),
                ("Transit days (before → after)", _arrow(snap.get("transit_days_before"), snap.get("transit_days_after"))),
                ("Bonus (before → after)", _arrow(snap.get("bonus_before"), snap.get("bonus_after"))),
                ("Consignment (before → after)", _arrow(snap.get("consignment_before"), snap.get("consignment_after"))),
            ]),
            ("Planning", [
                ("Planned start", txt(snap.get("planned_start_date"))),
                ("Real start", txt(snap.get("real_start_date"))),
                ("Planned end", txt(snap.get("planned_end_date"))),
                ("Duration", f"{snap.get('duration_months')} months" if snap.get("duration_months") not in (None, "") else None),
            ]),
            ("Risks & benefits", [
                *[(f"Risk · {k.replace('_', ' ')}", txt(v)) for k, v in (risks.items() if isinstance(risks, dict) else [])],
                *[(f"Benefit · {k.replace('_', ' ')}", txt(v)) for k, v in (benefits.items() if isinstance(benefits, dict) else [])],
            ]),
        ]

        def render_section(title, rows) -> str:
            if not rows:
                return ""
            body = "".join(
                f"<tr>"
                f"<td style='padding:7px 16px 7px 0;color:#64748b;font-size:13px;white-space:nowrap;vertical-align:top'>{k}</td>"
                f"<td style='padding:7px 0;color:#0f172a;font-size:13px;font-weight:600;vertical-align:top'>{v}</td>"
                f"</tr>"
                for k, v in rows if v not in (None, "")
            )
            if not body:
                return ""
            return (
                f"<tr><td colspan='2' style='padding:18px 0 6px'>"
                f"<span style='display:inline-block;font-size:11px;font-weight:700;letter-spacing:.08em;"
                f"text-transform:uppercase;color:#0891b2'>{title}</span>"
                f"<span style='display:block;height:2px;width:34px;margin-top:4px;background:linear-gradient(90deg,#2563eb,#0891b2);border-radius:2px'></span>"
                f"</td></tr>{body}"
            )

        sections_html = "".join(render_section(t, r) for t, r in sections)

        # Headline stat tiles (only the ones that have a value).
        tiles = [
            ("Expected annual saving", money(snap.get("expected_annual_saving"))),
            ("Total period saving", money(snap.get("period_saving"))),
            ("Cash impact", money(snap.get("cash_impact"))),
        ]
        tiles = [(lbl, val) for lbl, val in tiles if val]
        tiles_html = ""
        if tiles:
            cells = "".join(
                f"<td style='padding:6px'>"
                f"<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px'>"
                f"<div style='font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8'>{lbl}</div>"
                f"<div style='font-size:18px;font-weight:800;color:#0f2744;margin-top:4px'>{val}</div>"
                f"</div></td>"
                for lbl, val in tiles
            )
            tiles_html = f"<table role='presentation' width='100%' style='border-collapse:separate;margin:4px -6px 6px'><tr>{cells}</tr></table>"

        by_line = f" by {approver_email}" if approver_email else ""
        link = (
            f"{settings.frontend_base_url}/purchasing-value?opp={opportunity_id}"
            if opportunity_id else settings.frontend_base_url
        )

        html = f"""
<div style="margin:0;padding:24px 12px;background:#eef2f7;font-family:Inter,Segoe UI,Arial,sans-serif">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:20px;overflow:hidden;box-shadow:0 18px 40px -20px rgba(2,10,25,.35)">
    <div style="background:linear-gradient(135deg,#0f2744,#1b5d92 55%,#0891b2);padding:26px 28px;color:#fff">
      <div style="font-size:11px;font-weight:700;letter-spacing:.22em;text-transform:uppercase;color:rgba(255,255,255,.7)">AvoCarbon · Purchasing Value</div>
      <div style="font-size:22px;font-weight:800;margin-top:8px;line-height:1.25">You're the Project Manager</div>
      <div style="font-size:13px;color:rgba(255,255,255,.85);margin-top:6px">
        The <strong>{phase}</strong> gate was approved by the full panel{by_line}. You now lead this project.
      </div>
    </div>
    <div style="padding:22px 28px 28px">
      <div style="font-size:15px;font-weight:800;color:#0f2744">{opp_name}</div>
      <div style="font-size:12px;color:#64748b;margin-top:2px">Full dossier below — for tracking in your own system.</div>
      {tiles_html}
      <table role="presentation" width="100%" style="border-collapse:collapse;margin-top:6px">
        {sections_html}
      </table>
      <a href="{link}" style="display:inline-block;margin-top:22px;background:linear-gradient(135deg,#0f2744,#1b5d92,#0891b2);color:#fff;text-decoration:none;padding:12px 26px;border-radius:12px;font-size:14px;font-weight:700">Open in Purchasing Value →</a>
      <p style="font-size:11px;color:#94a3b8;margin:22px 0 0">
        AvoCarbon · Suppliers Management · Purchasing Value — automated notification, no reply needed.
      </p>
    </div>
  </div>
</div>"""
        try:
            email_svc = get_email_service()
            email_svc.send_sync(
                subject=f"[Project handover] You lead — {opp_name} ({phase} approved)",
                recipients=[pm_email],
                body_html=html,
            )
        except Exception:
            pass  # Non-blocking
