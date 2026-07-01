"""
Seed script — cycle history demo data
======================================
Creates one complete supplier (group + unit + site + relation) with three
evaluation cycles that show a realistic IATF progression:

  Cycle 1  Initial Self-Assessment  (12 months ago)  Class 3 / B / B3
  Cycle 2  Criteria Change Review   ( 6 months ago)  Class 2 / B / B2  <- LTA upgraded
  Cycle 3  Decision & Impact Update (today)           Class 1 / A / A1  <- IATF cert + grade

All rows are committed — data persists in the DB and is visible in the UI.

Run from the backend root:
    python -m scripts.seed_cycle_history
"""
from __future__ import annotations

import asyncio
import sys
import os
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import (
    AvocarbonSite,
    Classification,
    EvaluationCycle,
    ImpactEvaluationInput,
    OperationalEvaluationInput,
    PldClassEvaluationInput,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierUnit,
)

SEED_TAG  = "[SEED-CH]"
GROUP_NAME = f"{SEED_TAG} ACME Electronics"
UNIT_CODE  = f"{SEED_TAG}-ACME-001"
SITE_NAME  = f"{SEED_TAG} Casablanca Plant"
BUYER      = "buyer@avocarbon.com"

NOW = datetime.utcnow()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pld(relation_id: int, cycle_id: int, *, lta: str, top: str,
         quality_certification: str, class_score: float, class_value: int,
         prod_lia_ins: str = "yes") -> PldClassEvaluationInput:
    return PldClassEvaluationInput(
        id_relation=relation_id,
        id_cycle=cycle_id,
        lta=lta,
        top=top,
        quality_certification=quality_certification,
        prod_lia_ins=prod_lia_ins,
        productivity="improving",
        competitiveness="competitive",
        sqma="yes",
        family_coverage="full",
        geo_coverage="regional",
        cons_or_wd="consignment",
        financial_health="good",
        class_score=Decimal(str(class_score)),
        class_value=class_value,
        entered_by=BUYER,
    )


def _op(relation_id: int, cycle_id: int, *,
        management_system: float, deliveries: float,
        operational_grade: str, source_type: str = "self_assessment") -> OperationalEvaluationInput:
    avg = (management_system + deliveries) / 2
    return OperationalEvaluationInput(
        id_relation=relation_id,
        id_cycle=cycle_id,
        management_system=Decimal(str(management_system)),
        customer_communication=Decimal("70"),
        development_design=Decimal("65"),
        production_manufacturing=Decimal("80"),
        quality_audits=Decimal("75"),
        suppliers_subcontractors=Decimal("70"),
        deliveries=Decimal(str(deliveries)),
        environment_ethic_rules=Decimal("80"),
        average_score=Decimal(str(round(avg, 2))),
        operational_grade=operational_grade,
        source_type=source_type,
        entered_by=BUYER,
    )


def _cls(relation_id: int, cycle_id: int, *,
         class_value: int, operational_grade: str, final_grade: str,
         impact_score: int, panel_decision: str,
         strategic_mention: str = "none") -> Classification:
    return Classification(
        id_relation=relation_id,
        id_cycle=cycle_id,
        class_value=class_value,
        operational_grade=operational_grade,
        final_grade=final_grade,
        impact_score=impact_score,
        panel_decision=panel_decision,
        strategic_mention=strategic_mention,
    )


def _impact(relation_id: int, cycle_id: int, *,
            q1: str, q2: str, q3: str,
            q4: str, q5: str, q6: str,
            impact_score: int) -> ImpactEvaluationInput:
    return ImpactEvaluationInput(
        id_relation=relation_id,
        id_cycle=cycle_id,
        question_1=q1,
        question_2=q2,
        question_3=q3,
        question_4=q4,
        question_5=q5,
        question_6=q6,
        impact_score=impact_score,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def seed() -> None:
    async with SessionLocal() as db:

        existing_unit = (await db.execute(
            select(SupplierUnit).where(SupplierUnit.supplier_code == UNIT_CODE)
        )).scalar_one_or_none()

        if existing_unit:
            # Unit already exists — patch missing ImpactEvaluationInput rows only
            await _patch_impact_rows(db, existing_unit)
            return

        # ── 1. supplier group ─────────────────────────────────────────────
        group = SupplierGroup(
            nom=GROUP_NAME,
            supplier_scope="Cat A",
            group_supplier_owner_email=BUYER,
        )
        db.add(group)
        await db.flush()

        # ── 2. supplier unit ──────────────────────────────────────────────
        unit = SupplierUnit(
            id_group=group.id_group,
            supplier_code=UNIT_CODE,
            city="Casablanca",
            country="Morocco",
            continent="Africa",
            family="Passive Components",
            sub_family="Ferrites",
            is_active=True,
        )
        db.add(unit)
        await db.flush()

        # ── 3. site ───────────────────────────────────────────────────────
        site = AvocarbonSite(
            site_name=SITE_NAME,
            city="Casablanca",
            country="Morocco",
            active=True,
        )
        db.add(site)
        await db.flush()

        # ── 4. relation ───────────────────────────────────────────────────
        relation = SupplierSiteRelation(
            id_site=site.id_site,
            id_supplier_unit=unit.id_supplier_unit,
            supplier_owner=BUYER,
            supplier_status="Can quote and be awarded",
            final_grade="A1",
            class_value=1,
            operational_grade="A",
            panel_decision="panel_add",
            last_evaluation_date=NOW.date(),
            evaluation_comments="Supplier passed all IATF criteria after three audit cycles.",
        )
        db.add(relation)
        await db.flush()

        rid = relation.id_relation

        # ── Cycle 1 — Initial Self-Assessment (12 months ago) ─────────────
        c1 = EvaluationCycle(
            id_relation=rid, cycle_type="Initial Self-Assessment",
            launched_by=BUYER, launched_at=NOW - timedelta(days=365),
        )
        db.add(c1)
        await db.flush()

        db.add(_pld(rid, c1.id_cycle,
                    lta="1 year", top="30 days net",
                    quality_certification="ISO9001",
                    class_score=58.0, class_value=3))
        db.add(_op(rid, c1.id_cycle,
                   management_system=65.0, deliveries=60.0, operational_grade="B"))
        db.add(_cls(rid, c1.id_cycle,
                    class_value=3, operational_grade="B", final_grade="B3",
                    impact_score=3, panel_decision="panel_add_exec_committee"))
        db.add(_impact(rid, c1.id_cycle,
                       q1="Minor +", q2="None", q3="Minor +",
                       q4="None", q5="Minor +", q6="None",
                       impact_score=3))
        await db.flush()

        # ── Cycle 2 — Criteria Change Review (6 months ago) ──────────────
        c2 = EvaluationCycle(
            id_relation=rid, cycle_type="Criteria Change Review",
            launched_by=BUYER, launched_at=NOW - timedelta(days=180),
        )
        db.add(c2)
        await db.flush()

        db.add(_pld(rid, c2.id_cycle,
                    lta="3 years/+", top="30 days net",
                    quality_certification="IATF 16949:2016",
                    class_score=72.0, class_value=2))
        db.add(_op(rid, c2.id_cycle,
                   management_system=78.0, deliveries=75.0, operational_grade="B"))
        db.add(_cls(rid, c2.id_cycle,
                    class_value=2, operational_grade="B", final_grade="B2",
                    impact_score=4, panel_decision="panel_add"))
        db.add(_impact(rid, c2.id_cycle,
                       q1="Minor +", q2="Minor +", q3="Minor +",
                       q4="None", q5="Minor +", q6="None",
                       impact_score=4))
        await db.flush()

        # ── Cycle 3 — Decision & Impact Update (today) ────────────────────
        c3 = EvaluationCycle(
            id_relation=rid, cycle_type="Decision & Impact Update",
            launched_by=BUYER, launched_at=NOW,
        )
        db.add(c3)
        await db.flush()

        db.add(_pld(rid, c3.id_cycle,
                    lta="3 years/+", top="60 days net",
                    quality_certification="IATF 16949:2016",
                    class_score=90.0, class_value=1))
        db.add(_op(rid, c3.id_cycle,
                   management_system=92.0, deliveries=88.0,
                   operational_grade="A", source_type="kpi"))
        db.add(_cls(rid, c3.id_cycle,
                    class_value=1, operational_grade="A", final_grade="A1",
                    impact_score=5, panel_decision="panel_add",
                    strategic_mention="strategic"))
        db.add(_impact(rid, c3.id_cycle,
                       q1="Major +", q2="Minor +", q3="Major +",
                       q4="Minor +", q5="Major +", q6="Minor +",
                       impact_score=5))
        await db.flush()

        await db.commit()

        print(f"[seed] Done - data committed.")
        print(f"  Group    : {GROUP_NAME}  (id={group.id_group})")
        print(f"  Unit     : {UNIT_CODE}   (id={unit.id_supplier_unit})")
        print(f"  Site     : {SITE_NAME}   (id={site.id_site})")
        print(f"  Relation : id={rid}")
        print(f"  Cycles   : {c1.id_cycle} (Initial SA) / {c2.id_cycle} (Criteria Change) / {c3.id_cycle} (Decision)")
        print(f"")
        print(f'  Open the Supplier Panel -> site "{SITE_NAME}" -> Evaluation > History tab.')


async def _patch_impact_rows(db, unit: SupplierUnit) -> None:
    """Add missing ImpactEvaluationInput rows to already-seeded cycles."""
    rel = (await db.execute(
        select(SupplierSiteRelation).where(SupplierSiteRelation.id_supplier_unit == unit.id_supplier_unit)
    )).scalar_one_or_none()
    if not rel:
        print("[patch] No relation found — nothing to patch.")
        return

    cycles = (await db.execute(
        select(EvaluationCycle)
        .where(EvaluationCycle.id_relation == rel.id_relation)
        .where(EvaluationCycle.is_deleted.is_(False))
        .order_by(EvaluationCycle.launched_at)
    )).scalars().all()

    # Payload per cycle (by position: oldest first)
    impact_payloads = [
        dict(q1="Minor +", q2="None",    q3="Minor +", q4="None",    q5="Minor +", q6="None",    impact_score=3),
        dict(q1="Minor +", q2="Minor +", q3="Minor +", q4="None",    q5="Minor +", q6="None",    impact_score=4),
        dict(q1="Major +", q2="Minor +", q3="Major +", q4="Minor +", q5="Major +", q6="Minor +", impact_score=5),
    ]

    patched = 0
    for i, cycle in enumerate(cycles):
        existing = (await db.execute(
            select(ImpactEvaluationInput)
            .where(ImpactEvaluationInput.id_cycle == cycle.id_cycle)
            .where(ImpactEvaluationInput.is_deleted.is_(False))
        )).scalar_one_or_none()

        if existing:
            print(f"[patch] Cycle {cycle.id_cycle} ({cycle.cycle_type}) — impact row already exists, skipping.")
            continue

        payload = impact_payloads[i] if i < len(impact_payloads) else impact_payloads[-1]
        db.add(_impact(rel.id_relation, cycle.id_cycle, **payload))
        patched += 1
        print(f"[patch] Cycle {cycle.id_cycle} ({cycle.cycle_type}) — impact row added.")

    if patched:
        await db.commit()
        print(f"[patch] Committed {patched} new ImpactEvaluationInput row(s).")
    else:
        print("[patch] Nothing to patch — all cycles already have impact rows.")


if __name__ == "__main__":
    asyncio.run(seed())
