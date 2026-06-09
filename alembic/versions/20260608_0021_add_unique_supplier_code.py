"""Enforce unique supplier_code on supplier_unit.

Revision ID: 20260608_0021
Revises: 20260605_0020
Create Date: 2026-06-08

supplier_code is the human-readable unit name and must be globally unique
so that batch evaluation uploads can resolve rows unambiguously.

NOTE: If your database already contains duplicate supplier_code values,
the upgrade will fail. Resolve duplicates first by running:
  SELECT supplier_code, COUNT(*) FROM supplier_unit
  GROUP BY supplier_code HAVING COUNT(*) > 1;
"""

from alembic import op


revision = "20260608_0021"
down_revision = "20260605_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_supplier_unit_code",
        "supplier_unit",
        ["supplier_code"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_supplier_unit_code",
        "supplier_unit",
        type_="unique",
    )
