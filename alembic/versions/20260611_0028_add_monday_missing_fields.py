"""Add missing Monday.com fields to supplier_unit, supplier_site_relation, and supplier_development_plan.

Fields added:
  supplier_unit:
    continent, supplier_email, scope1_ghg, scope2_ghg, ghg_comments,
    ghg_requested_date, ghg_completion_pct, commodity_responsible, area, main_plants

  supplier_site_relation:
    last_eval_score, transit_days, transport_mode, real_ap_days,
    real_ap_days_validated, incoterm_place, consignment,
    preferred_dev_supplier, data_validity, quality_cert_required,
    delivery_status, req_ap_date

  supplier_development_plan:
    decision, commodity, plant

Revision ID: 20260611_0028
Revises: 20260611_0027
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa

revision = "20260611_0028"
down_revision = "20260611_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # supplier_unit
    # ------------------------------------------------------------------
    op.add_column("supplier_unit", sa.Column("continent", sa.String(100), nullable=True))
    op.add_column("supplier_unit", sa.Column("supplier_email", sa.String(255), nullable=True))
    op.add_column("supplier_unit", sa.Column("scope1_ghg", sa.Numeric(18, 4), nullable=True))
    op.add_column("supplier_unit", sa.Column("scope2_ghg", sa.Numeric(18, 4), nullable=True))
    op.add_column("supplier_unit", sa.Column("ghg_comments", sa.Text(), nullable=True))
    op.add_column("supplier_unit", sa.Column("ghg_requested_date", sa.Date(), nullable=True))
    op.add_column("supplier_unit", sa.Column("ghg_completion_pct", sa.String(50), nullable=True))
    op.add_column("supplier_unit", sa.Column("commodity_responsible", sa.String(200), nullable=True))
    op.add_column("supplier_unit", sa.Column("area", sa.String(100), nullable=True))
    op.add_column("supplier_unit", sa.Column("main_plants", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # supplier_site_relation
    # ------------------------------------------------------------------
    op.add_column("supplier_site_relation", sa.Column("last_eval_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("transit_days", sa.Integer(), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("transport_mode", sa.String(100), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("real_ap_days", sa.Integer(), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("real_ap_days_validated", sa.Integer(), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("incoterm_place", sa.String(200), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("consignment", sa.Boolean(), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("preferred_dev_supplier", sa.Boolean(), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("data_validity", sa.String(50), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("quality_cert_required", sa.String(200), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("delivery_status", sa.String(50), nullable=True))
    op.add_column("supplier_site_relation", sa.Column("req_ap_date", sa.Date(), nullable=True))

    # ------------------------------------------------------------------
    # supplier_development_plan
    # ------------------------------------------------------------------
    op.add_column("supplier_development_plan", sa.Column("decision", sa.String(255), nullable=True))
    op.add_column("supplier_development_plan", sa.Column("commodity", sa.String(200), nullable=True))
    op.add_column("supplier_development_plan", sa.Column("plant", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("supplier_development_plan", "plant")
    op.drop_column("supplier_development_plan", "commodity")
    op.drop_column("supplier_development_plan", "decision")

    op.drop_column("supplier_site_relation", "req_ap_date")
    op.drop_column("supplier_site_relation", "delivery_status")
    op.drop_column("supplier_site_relation", "quality_cert_required")
    op.drop_column("supplier_site_relation", "data_validity")
    op.drop_column("supplier_site_relation", "preferred_dev_supplier")
    op.drop_column("supplier_site_relation", "consignment")
    op.drop_column("supplier_site_relation", "incoterm_place")
    op.drop_column("supplier_site_relation", "real_ap_days_validated")
    op.drop_column("supplier_site_relation", "real_ap_days")
    op.drop_column("supplier_site_relation", "transport_mode")
    op.drop_column("supplier_site_relation", "transit_days")
    op.drop_column("supplier_site_relation", "last_eval_score")

    op.drop_column("supplier_unit", "main_plants")
    op.drop_column("supplier_unit", "area")
    op.drop_column("supplier_unit", "commodity_responsible")
    op.drop_column("supplier_unit", "ghg_completion_pct")
    op.drop_column("supplier_unit", "ghg_requested_date")
    op.drop_column("supplier_unit", "ghg_comments")
    op.drop_column("supplier_unit", "scope2_ghg")
    op.drop_column("supplier_unit", "scope1_ghg")
    op.drop_column("supplier_unit", "supplier_email")
    op.drop_column("supplier_unit", "continent")
