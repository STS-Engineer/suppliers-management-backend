"""Supplier monitoring service — computes data-completeness gaps per supplier unit.

The "supplier" the dashboard reports on is the ``SupplierUnit`` (relations and
certifications both attach to the unit), enriched with its parent
``SupplierGroup`` for owner/click-through context. One pass over the active
portfolio builds a gap profile per unit; the router wraps counts + drill-down.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AvocarbonSite,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierUnit,
)
from app.features.supplier_relations.service import SupplierRelationService


# The predefined checks the dashboard can surface. `key` is the stable id the
# frontend toggles on; `label` is the default display text (frontend may relabel).
#
# `scope` says WHAT the check's count measures, so the UI can group the tiles and
# label the number correctly:
#   - "unit"     → the gap is a property of the supplier unit itself (commodity,
#                  quality cert, contact, or having no committee-validated relation
#                  at all). Count = number of units.
#   - "relation" → the gap lives on an individual supplier↔plant relation
#                  (evaluation cadence, grade/class, buyer owner). Count = number
#                  of relations, matching the Batch Evaluation dashboard exactly.
CHECK_DEFINITIONS: list[dict[str, str]] = [
    {"key": "no_relation", "label": "No relation", "scope": "unit"},
    {"key": "no_quality_cert", "label": "No quality certificate", "scope": "unit"},
    {"key": "never_evaluated", "label": "Never evaluated", "scope": "relation"},
    {"key": "missing_eval_date", "label": "Missing eval date", "scope": "relation"},
    {"key": "overdue_evaluation", "label": "Overdue evaluation", "scope": "relation"},
    {"key": "on_hold", "label": "On hold", "scope": "relation"},
    {"key": "poor_performer", "label": "Poor performer", "scope": "relation"},
    {"key": "no_grade", "label": "No grade / class", "scope": "relation"},
    {"key": "no_eval_frequency", "label": "No eval frequency", "scope": "relation"},
    {"key": "no_supplier_owner", "label": "No supplier owner", "scope": "relation"},
]
CHECK_KEYS = [c["key"] for c in CHECK_DEFINITIONS]
SCOPE_BY_KEY = {c["key"]: c["scope"] for c in CHECK_DEFINITIONS}

# Supplier status that means the relation is blocked from new business. Compared
# case-insensitively: the Monday import stores "New business on Hold" while the
# app's own grade→status logic writes "New Business on Hold".
STATUS_ON_HOLD = "new business on hold"


def _blank(value: Optional[str]) -> bool:
    """True when a string field is missing or whitespace-only."""
    return value is None or str(value).strip() == ""


class SupplierMonitoringService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overview(
        self,
        *,
        country: Optional[str] = None,
        commodity: Optional[str] = None,
        group_id: Optional[int] = None,
        q: Optional[str] = None,
    ) -> dict:
        """Build the monitoring overview: per-check counts + the list of units in
        default, with click-through identifiers. Optional filters narrow the set
        the counts and list are computed over."""
        today = date.today()

        # 1. Active, non-deleted supplier units with their group (single query).
        stmt = (
            select(SupplierUnit, SupplierGroup)
            .join(SupplierGroup, SupplierUnit.id_group == SupplierGroup.id_group)
            .where(SupplierUnit.is_active.is_(True))
            .where(SupplierUnit.is_deleted.is_(False))
            .where(SupplierGroup.is_active.is_(True))
            .where(SupplierGroup.is_deleted.is_(False))
        )
        if group_id is not None:
            stmt = stmt.where(SupplierUnit.id_group == group_id)
        if country:
            stmt = stmt.where(SupplierUnit.country == country)
        if commodity:
            stmt = stmt.where(SupplierUnit.commodity.ilike(f"%{commodity}%"))
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                SupplierUnit.supplier_name.ilike(like) | SupplierGroup.nom.ilike(like)
            )
        rows = (await self.db.execute(stmt)).all()
        units: list[SupplierUnit] = [r[0] for r in rows]
        group_by_unit: dict[int, SupplierGroup] = {
            r[0].id_supplier_unit: r[1] for r in rows
        }
        unit_ids = [u.id_supplier_unit for u in units]

        # 2. Committee-validated relations grouped per unit (for no_relation +
        #    overdue_evaluation, etc.). We only consider relations that are "in
        #    panel / committee validated" — i.e. validation_status == "approved"
        #    and not inactivated — so this dashboard sees the exact same relation
        #    population as the Batch Evaluation dashboard (get_evaluations_due).
        #    Draft / pending_review / rejected relations are excluded on purpose:
        #    a supplier that is not yet approved is not part of the active panel.
        relations_by_unit: dict[int, list[SupplierSiteRelation]] = {}
        site_by_relation: dict[int, AvocarbonSite] = {}
        if unit_ids:
            rel_stmt = (
                select(SupplierSiteRelation, AvocarbonSite)
                .join(AvocarbonSite, AvocarbonSite.id_site == SupplierSiteRelation.id_site)
                .where(SupplierSiteRelation.id_supplier_unit.in_(unit_ids))
                .where(SupplierSiteRelation.is_deleted.is_(False))
                .where(SupplierSiteRelation.inactivated_at.is_(None))
                .where(SupplierSiteRelation.validation_status == "approved")
            )
            for rel, site in (await self.db.execute(rel_stmt)).all():
                relations_by_unit.setdefault(rel.id_supplier_unit, []).append(rel)
                site_by_relation[rel.id_relation] = site

        # 3. Best quality cert per unit (reuse the app's own ranking logic).
        certs_by_unit = await SupplierRelationService(self.db)._get_best_certs_for_units(
            unit_ids
        )

        # 4. Build gap profiles at BOTH granularities so the UI can present them
        #    in two tabs that never conflate their counts:
        #      - unit_items:     one row per supplier unit, carrying only the
        #                        UNIT-scoped gaps (no relation, no cert, no
        #                        commodity, no contact).
        #      - relation_items: one row per offending supplier↔plant relation,
        #                        carrying only the RELATION-scoped gaps (never
        #                        evaluated, overdue, no grade, no owner). This is
        #                        the same granularity as the Batch Evaluation
        #                        dashboard, so a relation tile's count equals the
        #                        number of rows its filter shows.
        unit_items: list[dict] = []
        relation_items: list[dict] = []
        counts = {k: 0 for k in CHECK_KEYS}
        # Distinct units behind each relation-scoped key (for "N relations · M units").
        units_touched: dict[str, set[int]] = {
            k: set() for k in CHECK_KEYS if SCOPE_BY_KEY[k] == "relation"
        }
        affected_groups: set[int] = set()
        # Units flagged for ANY gap (either scope) — drives the portfolio health %.
        units_needing_attention: set[int] = set()

        for unit in units:
            uid = unit.id_supplier_unit
            group = group_by_unit[uid]
            relations = relations_by_unit.get(uid, [])
            scoring_cert = certs_by_unit.get(uid, (None, None))[0]

            owner_fallback = group.group_supplier_owner_email

            # --- Unit-scoped gaps -------------------------------------------------
            unit_gaps: list[str] = []
            if not relations:
                unit_gaps.append("no_relation")
            if scoring_cert is None:
                unit_gaps.append("no_quality_cert")

            if unit_gaps:
                for g in unit_gaps:
                    counts[g] += 1
                affected_groups.add(group.id_group)
                units_needing_attention.add(uid)
                unit_items.append(
                    {
                        "id_group": group.id_group,
                        "group_code": group.group_code,
                        "group_name": group.nom,
                        "id_supplier_unit": uid,
                        "unit_code": unit.unit_code,
                        "supplier_name": unit.supplier_name,
                        "city": unit.city,
                        "country": unit.country,
                        "commodity": unit.commodity,
                        "supplier_owner": next(
                            (r.buyer_owner for r in relations if not _blank(r.buyer_owner)),
                            None,
                        )
                        or owner_fallback,
                        "relation_count": len(relations),
                        "gaps": unit_gaps,
                    }
                )

            # --- Relation-scoped gaps (one row per offending relation) -----------
            for rel in relations:
                rel_gaps: list[str] = []
                # A relation counts as evaluated if it carries ANY scorecard
                # evidence (grade / class / final grade / score) — even when the
                # evaluation date wasn't recorded (common in the Monday import,
                # e.g. suppliers graded B4/NBOH with no "Last Known E. Period").
                has_eval_evidence = not (
                    _blank(rel.operational_grade)
                    and _blank(rel.final_grade)
                    and rel.class_value is None
                    and rel.last_eval_score is None
                )
                if rel.last_evaluation_date is None:
                    # No date: truly never evaluated only if there's no evidence
                    # either; otherwise it's evaluated with a missing date.
                    rel_gaps.append(
                        "missing_eval_date" if has_eval_evidence else "never_evaluated"
                    )
                if rel.next_evaluation_date and rel.next_evaluation_date < today:
                    rel_gaps.append("overdue_evaluation")
                if (rel.supplier_status or "").strip().lower() == STATUS_ON_HOLD:
                    rel_gaps.append("on_hold")
                # Red performance band by scorecard (grade D or class 4). Flags a
                # poor performer even when supplier_status wasn't updated to match.
                if (rel.operational_grade or "").strip().upper() == "D" or rel.class_value == 4:
                    rel_gaps.append("poor_performer")
                if _blank(rel.final_grade) and rel.class_value is None:
                    rel_gaps.append("no_grade")
                if _blank(rel.evaluation_frequency):
                    rel_gaps.append("no_eval_frequency")
                if _blank(rel.buyer_owner):
                    rel_gaps.append("no_supplier_owner")
                if not rel_gaps:
                    continue

                for g in rel_gaps:
                    counts[g] += 1
                    units_touched[g].add(uid)
                affected_groups.add(group.id_group)
                units_needing_attention.add(uid)

                site = site_by_relation.get(rel.id_relation)
                relation_items.append(
                    {
                        "id_relation": rel.id_relation,
                        "id_group": group.id_group,
                        "group_code": group.group_code,
                        "group_name": group.nom,
                        "id_supplier_unit": uid,
                        "unit_code": unit.unit_code,
                        "supplier_name": unit.supplier_name,
                        "commodity": unit.commodity,
                        "supplier_owner": rel.buyer_owner or owner_fallback,
                        "plant_name": site.site_name if site else None,
                        "plant_city": site.city if site else None,
                        "plant_country": site.country if site else None,
                        "last_evaluation_date": rel.last_evaluation_date.isoformat()
                        if rel.last_evaluation_date
                        else None,
                        "next_evaluation_date": rel.next_evaluation_date.isoformat()
                        if rel.next_evaluation_date
                        else None,
                        "final_grade": rel.final_grade,
                        "class_value": rel.class_value,
                        "gaps": rel_gaps,
                    }
                )

        # Sort each list worst-first (most gaps), then by name for stability.
        unit_items.sort(
            key=lambda it: (-len(it["gaps"]), (it["supplier_name"] or "").lower())
        )
        relation_items.sort(
            key=lambda it: (
                -len(it["gaps"]),
                (it["supplier_name"] or "").lower(),
                (it["plant_name"] or "").lower(),
            )
        )

        checks = [
            {
                "key": c["key"],
                "label": c["label"],
                "scope": c["scope"],
                "count": counts[c["key"]],
                # For relation-scoped checks: distinct units behind those
                # relations (so the UI can show "N relations · M units").
                "units_affected": len(units_touched[c["key"]])
                if c["scope"] == "relation"
                else None,
            }
            for c in CHECK_DEFINITIONS
        ]

        # Filter options derived from the full active portfolio for the UI dropdowns.
        countries = sorted(
            {u.country for u in units if not _blank(u.country)}, key=str.lower
        )
        commodities = sorted(
            {
                part.strip()
                for u in units
                if u.commodity
                for part in u.commodity.split(",")
                if part.strip()
            },
            key=str.lower,
        )
        groups = sorted(
            (
                {"id_group": g.id_group, "nom": g.nom}
                for g in {gg.id_group: gg for gg in group_by_unit.values()}.values()
            ),
            key=lambda x: (x["nom"] or "").lower(),
        )

        return {
            "checks": checks,
            "total_units": len(units),
            "units_with_gaps": len(units_needing_attention),
            "groups_with_gaps": len(affected_groups),
            # Two drill-downs at matching granularity for the two UI tabs.
            "unit_items": unit_items,
            "relation_items": relation_items,
            "available_filters": {
                "countries": countries,
                "commodities": commodities,
                "groups": groups,
            },
        }
