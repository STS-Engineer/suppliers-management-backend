"""Compatibility re-exports for supplier relation models.

The shared ORM source of truth now lives in ``app.db.models``. This module
stays only to avoid stale imports elsewhere in the codebase.
"""

from app.db.models import SupplierSiteRelation, SupplierStatusHistory

__all__ = ["SupplierSiteRelation", "SupplierStatusHistory"]
    