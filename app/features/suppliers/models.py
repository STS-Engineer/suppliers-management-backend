"""Supplier domain model exports.

New supplier-focused code should import supplier master entities from this
module instead of reaching directly into ``app.db.models``.
"""

from app.db.models import (
    AvocarbonSite,
    Contact,
    ContactSiteRelation,
    # SupplierAgreement,  # DEAD TABLE — commented out in app.db.models
    SupplierCertification,
    SupplierGroup,
    SupplierSiteRelation,
    SupplierStatusHistory,
    SupplierUnit,
)

__all__ = [
    "AvocarbonSite",
    "SupplierGroup",
    "SupplierUnit",
    "SupplierSiteRelation",
    "SupplierStatusHistory",
    "SupplierCertification",
    # "SupplierAgreement",  # DEAD TABLE — commented out
    "Contact",
    "ContactSiteRelation",
]
