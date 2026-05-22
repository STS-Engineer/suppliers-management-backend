"""Sites Pydantic schemas."""

from typing import Optional
from pydantic import BaseModel, Field

from app.features.suppliers.schemas import SupplierGroupResponse, SupplierUnitResponse
from app.features.supplier_relations.schemas import SupplierRelationSummaryResponse


class SiteBase(BaseModel):
    """Base site schema."""
    
    site_name: Optional[str] = None
    address_line: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    active: Optional[bool] = True


class SiteCreate(SiteBase):
    """Site creation schema."""
    pass


class SiteUpdate(BaseModel):
    """Site update schema."""
    
    site_name: Optional[str] = None
    address_line: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    active: Optional[bool] = None


class SiteResponse(SiteBase):
    """Site response schema."""
    
    id_site: int
    
    class Config:
        from_attributes = True


class SitePanelRelationResponse(BaseModel):
    """Relation payload for site-first panel view."""

    relation: SupplierRelationSummaryResponse
    unit: SupplierUnitResponse
    group: SupplierGroupResponse
    group_categories: list[str] = Field(default_factory=list)


class SitePanelBundleResponse(BaseModel):
    """Site panel bundle for site-first view."""

    site: SiteResponse
    relations: list[SitePanelRelationResponse] = Field(default_factory=list)
    relation_count: int = 0
    unit_count: int = 0
    group_count: int = 0
