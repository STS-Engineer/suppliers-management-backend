"""Suppliers repository layer."""
from typing import List, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.features.suppliers.models import (
    Contact,
    SupplierCategory,
    SupplierCertification,
    SupplierGroup,
    SupplierGroupCategory,
    SupplierUnit,
)


class SupplierRepository:
    """Repository for supplier database operations."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # ========================================================================
    # SupplierGroup Operations
    # ========================================================================
    
    async def find_all_groups(self, skip: int = 0, limit: int = 100) -> List[SupplierGroup]:
        """Find all supplier groups with pagination."""
        stmt = (
            select(SupplierGroup)
            .options(
                selectinload(SupplierGroup.category_links).selectinload(
                    SupplierGroupCategory.category
                )
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def count_groups(self) -> int:
        """Count total number of supplier groups."""
        stmt = select(func.count(SupplierGroup.id_group))
        result = await self.db.execute(stmt)
        return result.scalar() or 0
    
    async def find_group_by_id(self, group_id: int) -> Optional[SupplierGroup]:
        """Find supplier group by ID without loading documents."""
        stmt = (
            select(SupplierGroup)
            .where(SupplierGroup.id_group == group_id)
            .options(
                selectinload(SupplierGroup.units),
                selectinload(SupplierGroup.contacts),
                selectinload(SupplierGroup.category_links).selectinload(
                    SupplierGroupCategory.category
                ),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
   
    async def find_group_with_documents(self, group_id: int) -> Optional[SupplierGroup]:
        """Find supplier group by ID with document relationships."""
        stmt = (
            select(SupplierGroup)
            .where(SupplierGroup.id_group == group_id)
            .options(
                selectinload(SupplierGroup.units),
                selectinload(SupplierGroup.contacts),
                selectinload(SupplierGroup.category_links).selectinload(
                    SupplierGroupCategory.category
                ),
                selectinload(SupplierGroup.documents),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
   
    async def find_group_for_response(self, group_id: int) -> Optional[SupplierGroup]:
        stmt = (
            select(SupplierGroup)
            .where(SupplierGroup.id_group == group_id)
            .options(
                selectinload(SupplierGroup.category_links)
                .selectinload(SupplierGroupCategory.category)
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
   
    async def find_group_by_name(self, name: str) -> Optional[SupplierGroup]:
        """Find supplier group by name."""
        stmt = select(SupplierGroup).where(SupplierGroup.nom == name)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create_group(self, data: dict) -> SupplierGroup:
        """Create a new supplier group."""
        group = SupplierGroup(**data)
        self.db.add(group)
        await self.db.flush()
        return group
    
    async def update_group(self, group_id: int, data: dict) -> Optional[SupplierGroup]:
        """Update a supplier group."""
        group = await self.find_group_by_id(group_id)
        if group:
            for key, value in data.items():
                if value is not None and hasattr(group, key):
                    setattr(group, key, value)
            await self.db.flush()
        return group

    async def find_category_by_key(self, category_key: str) -> Optional[SupplierCategory]:
        stmt = select(SupplierCategory).where(SupplierCategory.category_key == category_key)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def ensure_category(self, category_key: str, category_label: str) -> SupplierCategory:
        category = await self.find_category_by_key(category_key)
        if category:
            if category.category_label != category_label:
                category.category_label = category_label
                await self.db.flush()
            return category

        category = SupplierCategory(
            category_key=category_key,
            category_label=category_label,
        )
        self.db.add(category)
        await self.db.flush()
        return category

    async def replace_group_categories(
    self,
    group: SupplierGroup,
    categories: list[tuple[str, str]],
) -> None:
        stmt = select(SupplierGroupCategory).where(
            SupplierGroupCategory.id_group == group.id_group
        )
        result = await self.db.execute(stmt)
        existing_links = result.scalars().all()

        for link in existing_links:
            await self.db.delete(link)

        await self.db.flush()

        for category_key, category_label in categories:
            category = await self.ensure_category(category_key, category_label)
            self.db.add(
                SupplierGroupCategory(
                    id_group=group.id_group,
                    id_category=category.id_category,
                )
            )

        await self.db.flush()
    async def delete_group(self, group_id: int) -> bool:
        """Delete a supplier group and cascade delete its units."""
        group = await self.find_group_by_id(group_id)
        if group:
            await self.db.delete(group)
            await self.db.flush()
            return True
        return False
    
    # ========================================================================
    # SupplierUnit Operations
    # ========================================================================
    
    async def find_all_units(self, skip: int = 0, limit: int = 100) -> List[SupplierUnit]:
        """Find all supplier units with pagination."""
        stmt = select(SupplierUnit).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_units_by_group(self, group_id: int) -> List[SupplierUnit]:
        """Find all supplier units for a specific group."""
        stmt = select(SupplierUnit).where(SupplierUnit.id_group == group_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def count_units(self) -> int:
        """Count total number of supplier units."""
        stmt = select(func.count(SupplierUnit.id_supplier_unit))
        result = await self.db.execute(stmt)
        return result.scalar() or 0
    
    async def find_unit_by_id(self, unit_id: int) -> Optional[SupplierUnit]:
        """Find supplier unit by ID."""
        stmt = select(SupplierUnit).where(SupplierUnit.id_supplier_unit == unit_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def find_unit_by_code(self, code: str, group_id: Optional[int] = None) -> Optional[SupplierUnit]:
        """Find supplier unit by supplier code, optionally scoped to one group."""
        stmt = select(SupplierUnit).where(SupplierUnit.supplier_code == code)
        if group_id is None:
            stmt = stmt.where(SupplierUnit.id_group.is_(None))
        else:
            stmt = stmt.where(SupplierUnit.id_group == group_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create_unit(self, data: dict) -> SupplierUnit:
        """Create a new supplier unit."""
        unit = SupplierUnit(**data)
        self.db.add(unit)
        await self.db.flush()
        return unit
    
    async def update_unit(self, unit_id: int, data: dict) -> Optional[SupplierUnit]:
        """Update a supplier unit."""
        unit = await self.find_unit_by_id(unit_id)
        if unit:
            for key, value in data.items():
                if value is not None and hasattr(unit, key):
                    setattr(unit, key, value)
            await self.db.flush()
        return unit
    
    async def delete_unit(self, unit_id: int) -> bool:
        """Delete a supplier unit."""
        unit = await self.find_unit_by_id(unit_id)
        if unit:
            await self.db.delete(unit)
            await self.db.flush()
            return True
        return False
    
    # ========================================================================
    # SupplierCertification Operations
    # ========================================================================
    
    async def find_certifications_by_unit(self, unit_id: int) -> List[SupplierCertification]:
        """Find all certifications for a supplier unit."""
        stmt = select(SupplierCertification).where(
            SupplierCertification.id_supplier_unit == unit_id
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_certification_by_id(self, cert_id: int) -> Optional[SupplierCertification]:
        """Find certification by ID."""
        stmt = select(SupplierCertification).where(
            SupplierCertification.id_certification == cert_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create_certification(self, data: dict) -> SupplierCertification:
        """Create a new supplier certification."""
        cert = SupplierCertification(**data)
        self.db.add(cert)
        await self.db.flush()
        return cert
    
    async def update_certification(self, cert_id: int, data: dict) -> Optional[SupplierCertification]:
        """Update a supplier certification."""
        cert = await self.find_certification_by_id(cert_id)
        if cert:
            for key, value in data.items():
                if value is not None and hasattr(cert, key):
                    setattr(cert, key, value)
            await self.db.flush()
        return cert
    
    async def delete_certification(self, cert_id: int) -> bool:
        """Delete a supplier certification."""
        cert = await self.find_certification_by_id(cert_id)
        if cert:
            await self.db.delete(cert)
            await self.db.flush()
            return True
        return False
    
    # ========================================================================
    # Contact Operations
    # ========================================================================
    
    async def find_contacts_by_group(self, group_id: int) -> List[Contact]:
        """Find all contacts for a supplier group."""
        stmt = select(Contact).where(Contact.id_supplier_group == group_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_contacts_by_unit(self, unit_id: int) -> List[Contact]:
        """Find all contacts for a supplier unit."""
        stmt = select(Contact).where(Contact.id_supplier_unit == unit_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()
    
    async def find_contact_by_id(self, contact_id: int) -> Optional[Contact]:
        """Find contact by ID."""
        stmt = select(Contact).where(Contact.id_contact == contact_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create_contact(self, data: dict) -> Contact:
        """Create a new contact."""
        contact = Contact(**data)
        self.db.add(contact)
        await self.db.flush()
        return contact
    
    async def update_contact(self, contact_id: int, data: dict) -> Optional[Contact]:
        """Update a contact."""
        contact = await self.find_contact_by_id(contact_id)
        if contact:
            for key, value in data.items():
                if value is not None and hasattr(contact, key):
                    setattr(contact, key, value)
            await self.db.flush()
        return contact
        
    async def delete_contact(self, contact_id: int) -> bool:
        """Delete a contact."""
        contact = await self.find_contact_by_id(contact_id)
        if contact:
            await self.db.delete(contact)
            await self.db.flush()
            return True
        return False


