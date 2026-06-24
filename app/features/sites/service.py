"""Sites service layer."""

from datetime import date
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppException
from app.db.models import (
    AvocarbonSite,
    Contact,
    ContactSiteRelation,
    SupplierGroup,
    SupplierGroupCategory,
    SupplierSiteRelation,
    SupplierUnit,
)
from app.features.sites.repository import SiteRepository
from app.features.sites import schemas
from app.features.supplier_relations import schemas as relation_schemas
from app.features.suppliers import schemas as supplier_schemas


class SiteService:
    """Service for site operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SiteRepository(db)
        self._has_contact_site_relation: Optional[bool] = None

    async def _has_contact_site_relation_table(self) -> bool:
        if self._has_contact_site_relation is not None:
            return self._has_contact_site_relation

        result = await self.db.execute(
            text("SELECT to_regclass('public.contact_site_relation')")
        )
        self._has_contact_site_relation = result.scalar_one_or_none() is not None
        return self._has_contact_site_relation

    async def list_sites(self, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
        sites = await self.repo.find_all(skip=skip, limit=limit)
        total = await self.repo.count()

        return {
            "items": sites,
            "total": total,
            "skip": skip,
            "limit": limit,
        }

    async def list_site_panel(
        self,
        skip: int = 0,
        limit: int = 100,
        site_name: Optional[str] = None,
        supplier_owner: Optional[str] = None,
        class_grade: Optional[str] = None,
        status: Optional[str] = None,
        panel_decision: Optional[str] = None,
        category: Optional[str] = None,
        evaluation_start: Optional[date] = None,
        evaluation_end: Optional[date] = None,
        purchase_manager: Optional[str] = None,
        plant_manager: Optional[str] = None,
        scope: Optional[str] = None,
        family: Optional[str] = None,
        sub_family: Optional[str] = None,
        product_line: Optional[str] = None,
        supplier_name: Optional[str] = None,
        include_inactive: bool = False,
    ) -> Dict[str, Any]:
        has_contact_relation = await self._has_contact_site_relation_table()
        loader_options = [
            selectinload(AvocarbonSite.contacts),
            selectinload(AvocarbonSite.supplier_relations)
            .selectinload(SupplierSiteRelation.supplier_unit)
            .selectinload(SupplierUnit.group)
            .selectinload(SupplierGroup.category_links)
            .selectinload(SupplierGroupCategory.category),
        ]
        if has_contact_relation:
            loader_options.append(
                selectinload(AvocarbonSite.supplier_relations)
                .selectinload(SupplierSiteRelation.contacts_via_junction)
                .selectinload(ContactSiteRelation.contact)
            )

        stmt = select(AvocarbonSite).options(*loader_options).order_by(
            AvocarbonSite.site_name
        )
        result = await self.db.execute(stmt)
        sites = result.scalars().unique().all()

        def normalize(value: Optional[str]) -> str:
            return str(value or "").strip().lower()

        def matches_text(value: Optional[str], needle: Optional[str]) -> bool:
            if not needle:
                return True
            return normalize(needle) in normalize(value)

        def contacts_match(
            contacts: Iterable[Contact],
            needle: Optional[str],
        ) -> bool:
            if not needle:
                return True
            key = normalize(needle)
            for contact in contacts:
                if (
                    key in normalize(contact.role_label)
                    or key in normalize(contact.role_name)
                    or key in normalize(contact.full_name)
                    or key in normalize(contact.email)
                ):
                    return True
            return False

        filtered_items: list[schemas.SitePanelBundleResponse] = []
        for site in sites:
            if site_name and not matches_text(site.site_name, site_name):
                continue

            relation_entries: list[schemas.SitePanelRelationResponse] = []
            for relation in site.supplier_relations:
                unit = relation.supplier_unit
                group = unit.group if unit else None
                if not unit or not group:
                    continue
                if not include_inactive and not unit.is_active:
                    continue

                if supplier_owner and not (
                    matches_text(relation.supplier_owner, supplier_owner)
                    or matches_text(group.supplier_owner, supplier_owner)
                ):
                    continue

                if class_grade and not matches_text(relation.final_grade, class_grade):
                    continue
                if status and not matches_text(relation.supplier_status, status):
                    continue
                if panel_decision and not matches_text(relation.panel_decision, panel_decision):
                    continue

                if evaluation_start and (
                    not relation.last_evaluation_date
                    or relation.last_evaluation_date < evaluation_start
                ):
                    continue
                if evaluation_end and (
                    not relation.last_evaluation_date
                    or relation.last_evaluation_date > evaluation_end
                ):
                    continue

                if scope and not matches_text(relation.supplier_scope, scope):
                    continue
                if family and not matches_text(unit.family, family):
                    continue
                if sub_family and not matches_text(unit.sub_family, sub_family):
                    continue
                if product_line and not matches_text(unit.product_line, product_line):
                    continue
                if supplier_name and not (
                    matches_text(group.nom, supplier_name)
                    or matches_text(unit.supplier_code, supplier_name)
                ):
                    continue

                categories = list(group.supplier_categories)
                if category and not any(
                    matches_text(cat, category) for cat in categories
                ):
                    continue

                relation_contacts = (
                    [link.contact for link in relation.contacts_via_junction]
                    if has_contact_relation
                    else []
                )
                combined_contacts = [*site.contacts, *relation_contacts]
                if purchase_manager and not contacts_match(combined_contacts, purchase_manager):
                    continue
                if plant_manager and not contacts_match(combined_contacts, plant_manager):
                    continue

                relation_entries.append(
                    schemas.SitePanelRelationResponse(
                        relation=relation_schemas.SupplierRelationSummaryResponse.model_validate(
                            relation
                        ),
                        unit=supplier_schemas.SupplierUnitResponse.model_validate(
                            unit
                        ),
                        group=supplier_schemas.SupplierGroupResponse.model_validate(
                            group
                        ),
                        group_categories=categories,
                    )
                )

            if not relation_entries:
                continue

            unit_ids = {entry.unit.id_supplier_unit for entry in relation_entries}
            group_ids = {entry.group.id_group for entry in relation_entries}
            filtered_items.append(
                schemas.SitePanelBundleResponse(
                    site=schemas.SiteResponse.model_validate(site),
                    relations=relation_entries,
                    relation_count=len(relation_entries),
                    unit_count=len(unit_ids),
                    group_count=len(group_ids),
                )
            )

        total = len(filtered_items)
        sliced = filtered_items[skip : skip + limit]
        return {
            "items": sliced,
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    
    async def list_site_options(self):
        return await self.repo.find_options()
    
    async def create_site(self, data: schemas.SiteCreate):
        site_data = data.model_dump(exclude_unset=True)

        site = await self.repo.create(site_data)
        await self.db.commit()
        return site

    async def get_site(self, site_id: int):
        site = await self.repo.find_by_id(site_id)

        if not site:
            raise AppException(
                f"Site with ID {site_id} not found",
                status_code=404,
            )

        return site

    async def update_site(self, site_id: int, data: schemas.SiteUpdate):
        site_data = data.model_dump(exclude_unset=True)

        if not site_data:
            return await self.get_site(site_id)

        site = await self.repo.update(site_id, site_data)

        if not site:
            raise AppException(
                f"Site with ID {site_id} not found",
                status_code=404,
            )

        await self.db.commit()
        return site

    async def delete_site(self, site_id: int) -> bool:
        site = await self.repo.find_by_id(site_id)

        if not site:
            raise AppException(
                f"Site with ID {site_id} not found",
                status_code=404,
            )

        deleted = await self.repo.delete(site_id)
        await self.db.commit()
        return deleted