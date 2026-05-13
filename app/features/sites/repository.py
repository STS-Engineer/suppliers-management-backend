"""Sites repository layer."""

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AvocarbonSite


class SiteRepository:
    """Repository for site database operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_all(self, skip: int = 0, limit: int = 100):
        stmt = select(AvocarbonSite).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def find_options(self):
            stmt = (
                select(
                    AvocarbonSite.id_site,
                    AvocarbonSite.site_name,
                    AvocarbonSite.country,
                )
                .where(AvocarbonSite.active == True)
                .order_by(AvocarbonSite.site_name)
            )

            result = await self.db.execute(stmt)

            return [
                {
                    "id_site": row.id_site,
                    "site_name": row.site_name,
                    "country": row.country,
                }
                for row in result.all()
            ]
    
    async def count(self) -> int:
        stmt = select(func.count(AvocarbonSite.id_site))
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def find_by_id(self, site_id: int) -> Optional[AvocarbonSite]:
        stmt = select(AvocarbonSite).where(AvocarbonSite.id_site == site_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, data: dict) -> AvocarbonSite:
        site = AvocarbonSite(**data)
        self.db.add(site)
        await self.db.flush()
        return site

    async def update(self, site_id: int, data: dict) -> Optional[AvocarbonSite]:
        site = await self.find_by_id(site_id)

        if not site:
            return None

        for key, value in data.items():
            if value is not None and hasattr(site, key):
                setattr(site, key, value)

        await self.db.flush()
        return site

    async def delete(self, site_id: int) -> bool:
        site = await self.find_by_id(site_id)

        if not site:
            return False

        await self.db.delete(site)
        await self.db.flush()
        return True