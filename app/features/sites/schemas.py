"""Sites Pydantic schemas."""

from typing import Optional
from pydantic import BaseModel


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
