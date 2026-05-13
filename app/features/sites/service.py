"""Sites service layer."""

from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.features.sites.repository import SiteRepository
from app.features.sites import schemas


class SiteService:
    """Service for site operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SiteRepository(db)

    async def list_sites(self, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
        sites = await self.repo.find_all(skip=skip, limit=limit)
        total = await self.repo.count()

        return {
            "items": sites,
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