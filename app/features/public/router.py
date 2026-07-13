"""Public supplier directory — no authentication required."""

from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import PANEL_ACTIVE_DECISIONS
from app.shared.dependencies.db import get_db
from app.db.models import (
    AvocarbonSite,
    Contact,
    ContactSiteRelation,
    PldClassEvaluationInput,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierUnit,
)

router = APIRouter(prefix="/public", tags=["public"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PublicSiteOption(BaseModel):
    id_site: int
    site_name: str
    country: Optional[str] = None


class PublicContactEntry(BaseModel):
    full_name: str
    role_label: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_primary_contact: bool = False


class PublicPlantEntry(BaseModel):
    id_relation: int
    site_name: str
    # Avocarbon owner for this relation
    buyer_owner: Optional[str] = None
    alias_1: Optional[str] = None
    final_grade: Optional[str] = None
    supplier_status: Optional[str] = None
    supplier_scope: Optional[str] = None
    last_evaluation_date: Optional[str] = None
    annual_spend_value: Optional[float] = None
    # 11 criteria from latest evaluation (all stored as String in DB)
    lta: Optional[str] = None
    quality_certification: Optional[str] = None
    top: Optional[str] = None
    productivity: Optional[str] = None
    prod_lia_ins: Optional[str] = None
    competitiveness: Optional[str] = None
    sqma: Optional[str] = None
    family_coverage: Optional[str] = None
    geo_coverage: Optional[str] = None
    cons_or_wd: Optional[str] = None
    financial_health: Optional[str] = None
    class_value: Optional[int] = None
    class_score: Optional[float] = None
    # Contacts linked to this relation
    relation_contacts: List[PublicContactEntry] = []


class PublicSupplierEntry(BaseModel):
    id_supplier_unit: int
    supplier_name: Optional[str] = None
    id_group: Optional[int] = None
    group_name: Optional[str] = None
    group_code: Optional[str] = None
    group_owner_email: Optional[str] = None
    # Unit fields
    city: Optional[str] = None
    country: Optional[str] = None
    continent: Optional[str] = None
    area: Optional[str] = None
    address_line: Optional[str] = None
    website: Optional[str] = None
    family: Optional[str] = None
    sub_family: Optional[str] = None
    product_line: Optional[str] = None
    strategique: bool = False
    monopolistique: bool = False
    directed: bool = False
    plants: List[PublicPlantEntry] = []
    unit_contacts: List[PublicContactEntry] = []
    group_contacts: List[PublicContactEntry] = []


# ---------------------------------------------------------------------------
# Public sites list
# ---------------------------------------------------------------------------

@router.get("/sites")
async def get_public_sites(db: AsyncSession = Depends(get_db)):
    """Return the list of Avocarbon sites for use in public directory filters."""
    stmt = (
        select(AvocarbonSite)
        .where(AvocarbonSite.active.is_(True))
        .order_by(AvocarbonSite.site_name)
    )
    result = await db.execute(stmt)
    sites = result.scalars().all()
    return {
        "status": "success",
        "data": [
            PublicSiteOption(
                id_site=s.id_site,
                site_name=s.site_name,
                country=s.country,
            )
            for s in sites
        ],
    }


# ---------------------------------------------------------------------------
# Public supplier directory
# ---------------------------------------------------------------------------

@router.get("/supplier-directory")
async def get_public_supplier_directory(
    q: Optional[str] = Query(None, description="Search in group name or supplier code"),
    family: Optional[str] = Query(None, description="Comma-separated family values"),
    sub_family: Optional[str] = Query(None, description="Comma-separated sub-family values"),
    product_line: Optional[str] = Query(None, description="Comma-separated product-line values"),
    country: Optional[str] = Query(None),
    continent: Optional[str] = Query(None),
    plant: Optional[str] = Query(None, description="Comma-separated site names to filter by"),
    final_grade: Optional[str] = Query(None, description="Comma-separated grade values"),
    supplier_scope: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Public supplier directory — no login required.
    Returns only suppliers that are active and on the panel
    (panel_decision = 'panel_add' or 'panel_add_committee_validated').
    """

    stmt = (
        select(SupplierUnit)
        .options(
            selectinload(SupplierUnit.group),
            selectinload(SupplierUnit.site_relations).selectinload(
                SupplierSiteRelation.site
            ),
        )
        .join(SupplierUnit.site_relations)
        .where(SupplierUnit.is_deleted.is_(False))
        .where(SupplierSiteRelation.panel_decision.in_(PANEL_ACTIVE_DECISIONS))
        .where(SupplierSiteRelation.validation_status == "approved")
        .where(SupplierSiteRelation.is_active.is_(True))
        .where(SupplierSiteRelation.is_deleted.is_(False))
        .distinct()
    )

    result = await db.execute(stmt)
    units = result.scalars().unique().all()

    # ── Collect IDs for batch lookups ────────────────────────────────────────
    approved_rel_ids: list[int] = []
    unit_ids: list[int] = []
    group_ids: list[int] = []

    for unit in units:
        unit_ids.append(unit.id_supplier_unit)
        if unit.id_group:
            group_ids.append(unit.id_group)
        for r in unit.site_relations:
            if r.panel_decision in PANEL_ACTIVE_DECISIONS and not r.is_deleted and r.is_active:
                approved_rel_ids.append(r.id_relation)

    # ── Latest PLD per relation ──────────────────────────────────────────────
    pld_map: dict[int, PldClassEvaluationInput] = {}
    if approved_rel_ids:
        pld_subq = (
            select(
                PldClassEvaluationInput.id_relation,
                func.max(PldClassEvaluationInput.id_pld_input).label("max_id"),
            )
            .where(PldClassEvaluationInput.id_relation.in_(approved_rel_ids))
            .where(PldClassEvaluationInput.is_deleted.is_(False))
            .group_by(PldClassEvaluationInput.id_relation)
            .subquery()
        )
        pld_res = await db.execute(
            select(PldClassEvaluationInput).join(
                pld_subq,
                and_(
                    PldClassEvaluationInput.id_relation == pld_subq.c.id_relation,
                    PldClassEvaluationInput.id_pld_input == pld_subq.c.max_id,
                ),
            )
        )
        pld_map = {p.id_relation: p for p in pld_res.scalars().all()}

    # ── Live quality_certification per unit ──────────────────────────────────
    # Never trust the frozen PldClassEvaluationInput snapshot for this field --
    # it's the one criterion backed by an independently-editable record
    # (SupplierCertification) that can expire without the evaluation being re-saved.
    from app.features.supplier_relations.service import SupplierRelationService

    rel_service = SupplierRelationService(db)
    certs_by_unit = await rel_service._get_best_certs_for_units(unit_ids)

    # ── Contacts per unit ────────────────────────────────────────────────────
    unit_contacts_map: dict[int, list[Contact]] = {uid: [] for uid in unit_ids}
    if unit_ids:
        c_res = await db.execute(
            select(Contact)
            .where(Contact.id_supplier_unit.in_(unit_ids))
            .where(Contact.is_deleted.is_(False))
            .order_by(Contact.is_primary_contact.desc(), Contact.full_name)
        )
        for c in c_res.scalars().all():
            if c.id_supplier_unit in unit_contacts_map:
                unit_contacts_map[c.id_supplier_unit].append(c)

    # ── Contacts per group ───────────────────────────────────────────────────
    group_contacts_map: dict[int, list[Contact]] = {gid: [] for gid in group_ids}
    if group_ids:
        gc_res = await db.execute(
            select(Contact)
            .where(Contact.id_supplier_group.in_(group_ids))
            .where(Contact.is_deleted.is_(False))
            .order_by(Contact.is_primary_contact.desc(), Contact.full_name)
        )
        for c in gc_res.scalars().all():
            if c.id_supplier_group in group_contacts_map:
                group_contacts_map[c.id_supplier_group].append(c)

    # ── Contacts per relation (via ContactSiteRelation) ──────────────────────
    rel_contacts_map: dict[int, list[Contact]] = {rid: [] for rid in approved_rel_ids}
    if approved_rel_ids:
        csr_res = await db.execute(
            select(ContactSiteRelation, Contact)
            .join(Contact, ContactSiteRelation.id_contact == Contact.id_contact)
            .where(ContactSiteRelation.id_relation.in_(approved_rel_ids))
            .where(ContactSiteRelation.is_deleted.is_(False))
            .where(Contact.is_deleted.is_(False))
        )
        for csr, c in csr_res.all():
            if csr.id_relation in rel_contacts_map:
                rel_contacts_map[csr.id_relation].append(c)

    # ── Parse multi-value filters ────────────────────────────────────────────

    def _split(v: Optional[str]) -> list[str]:
        if not v:
            return []
        return [x.strip().lower() for x in v.split(",") if x.strip()]

    family_filter       = _split(family)
    sub_family_filter   = _split(sub_family)
    product_line_filter = _split(product_line)
    plant_filter        = _split(plant)
    grade_filter        = _split(final_grade)

    def _norm(v: Optional[str]) -> str:
        return (v or "").strip().lower()

    def _to_float(v) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _to_date_str(v) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, date):
            return v.isoformat()
        return str(v)

    def _fmt_contact(c: Contact) -> PublicContactEntry:
        return PublicContactEntry(
            full_name=c.full_name or "",
            role_label=c.role_label,
            email=c.email,
            phone=c.phone,
            is_primary_contact=bool(c.is_primary_contact),
        )

    # ── Build result list ────────────────────────────────────────────────────

    items: list[PublicSupplierEntry] = []

    for unit in units:
        group: Optional[SupplierGroup] = unit.group

        # Text search
        if q:
            q_l = _norm(q)
            if q_l not in _norm(group.nom if group else "") and q_l not in _norm(unit.supplier_name):
                continue

        if country and _norm(country) not in _norm(unit.country):
            continue
        if continent and _norm(continent) not in _norm(unit.continent):
            continue

        if family_filter:
            if not any(f in _norm(unit.family) for f in family_filter):
                continue
        if sub_family_filter:
            if not any(f in _norm(unit.sub_family) for f in sub_family_filter):
                continue
        if product_line_filter:
            if not any(f in _norm(unit.product_line) for f in product_line_filter):
                continue

        approved = [
            r for r in unit.site_relations
            if r.panel_decision in PANEL_ACTIVE_DECISIONS
            and r.validation_status == "approved"
            and not r.is_deleted
            and r.is_active
        ]

        if plant_filter:
            approved = [
                r for r in approved
                if r.site and any(p in _norm(r.site.site_name) for p in plant_filter)
            ]
            if not approved:
                continue

        if grade_filter:
            approved = [
                r for r in approved
                if any(g in _norm(r.final_grade) for g in grade_filter)
            ]
            if not approved:
                continue

        if supplier_scope:
            approved = [
                r for r in approved
                if _norm(supplier_scope) in _norm(r.supplier_scope)
            ]
            if not approved:
                continue

        scoring_cert, _ = certs_by_unit.get(unit.id_supplier_unit, (None, None))
        live_quality_certification = rel_service._certification_label(scoring_cert)

        plants: list[PublicPlantEntry] = []
        for r in approved:
            pld = pld_map.get(r.id_relation)
            rel_contacts = [_fmt_contact(c) for c in rel_contacts_map.get(r.id_relation, [])]
            plants.append(
                PublicPlantEntry(
                    id_relation=r.id_relation,
                    site_name=r.site.site_name if r.site else "",
                    buyer_owner=r.buyer_owner,
                    alias_1=r.alias_1,
                    final_grade=r.final_grade,
                    supplier_status=r.supplier_status,
                    supplier_scope=r.supplier_scope,
                    last_evaluation_date=_to_date_str(r.last_evaluation_date),
                    annual_spend_value=_to_float(r.annual_spend_value),
                    lta=pld.lta if pld else None,
                    quality_certification=live_quality_certification,
                    top=pld.top if pld else None,
                    productivity=pld.productivity if pld else None,
                    prod_lia_ins=pld.prod_lia_ins if pld else None,
                    competitiveness=pld.competitiveness if pld else None,
                    sqma=pld.sqma if pld else None,
                    family_coverage=pld.family_coverage if pld else None,
                    geo_coverage=pld.geo_coverage if pld else None,
                    cons_or_wd=pld.cons_or_wd if pld else None,
                    financial_health=pld.financial_health if pld else None,
                    class_value=pld.class_value if pld else None,
                    class_score=_to_float(pld.class_score if pld else None),
                    relation_contacts=rel_contacts,
                )
            )

        unit_ctcts  = [_fmt_contact(c) for c in unit_contacts_map.get(unit.id_supplier_unit, [])]
        group_ctcts = [_fmt_contact(c) for c in group_contacts_map.get(unit.id_group, [])] if unit.id_group else []

        items.append(
            PublicSupplierEntry(
                id_supplier_unit=unit.id_supplier_unit,
                supplier_name=unit.supplier_name,
                id_group=group.id_group if group else None,
                group_name=group.nom if group else None,
                group_code=f"GRP-{group.id_group:06d}" if group else None,
                group_owner_email=group.group_supplier_owner_email if group else None,
                city=unit.city,
                country=unit.country,
                continent=unit.continent,
                area=getattr(unit, "area", None),
                address_line=getattr(unit, "address_line", None),
                website=getattr(unit, "website", None),
                family=unit.family,
                sub_family=unit.sub_family,
                product_line=unit.product_line,
                strategique=bool(unit.strategique),
                monopolistique=bool(unit.monopolistique),
                directed=bool(unit.directed),
                plants=plants,
                unit_contacts=unit_ctcts,
                group_contacts=group_ctcts,
            )
        )

    total = len(items)
    items.sort(key=lambda x: (x.group_name or "").lower())
    sliced = items[skip: skip + limit]

    return {
        "status": "success",
        "data": {
            "items": [i.model_dump() for i in sliced],
            "total": total,
            "skip": skip,
            "limit": limit,
        },
    }
