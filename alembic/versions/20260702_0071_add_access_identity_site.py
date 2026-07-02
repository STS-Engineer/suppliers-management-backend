"""Add access_identity_site junction table and seed purchaser-site assignments

Each local purchaser is assigned to one or more Avocarbon sites.
Group-level purchasers (global_purchaser, purchasing_director) have no rows here —
they are returned for every site unconditionally by the API.

Hosni Ben Ali covers two sites (SCEET + SAME) — hence the many-to-many design.

Revision ID: 20260702_0071
Revises: 20260730_0070
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa

revision = "20260702_0071"
down_revision = "20260702_0070"
branch_labels = None
depends_on = None

# Local purchaser → site assignments from the perimeter/responsibility matrix.
# Format: (purchaser_email, site_name)
_ASSIGNMENTS = [
    ("hosni.benali@avocarbon.com", "SCEET"),
    ("hosni.benali@avocarbon.com", "SAME"),
    ("eduardo.rodriguez@avocarbon.com", "Monterrey"),
    ("lili.dong@avocarbon.com", "Tianjin"),
    ("joan.zhao@avocarbon.com", "Kunshan"),
    ("nicolas.masson@avocarbon.com", "Cyclam"),
    ("yassine.chiti@avocarbon.com", "GMBH Frankfurt"),
    ("hyerin.kang@avocarbon.com", "Daegu"),
    ("vivekanandan.p@avocarbon.com", "Chennai"),
]


def upgrade() -> None:
    op.create_table(
        "access_identity_site",
        sa.Column(
            "id_identity",
            sa.Integer,
            sa.ForeignKey("access_identity.id_identity", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "id_site",
            sa.Integer,
            sa.ForeignKey("avocarbon_site.id_site", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
    )

    for email, site_name in _ASSIGNMENTS:
        op.execute(
            f"""
            INSERT INTO access_identity_site (id_identity, id_site)
            SELECT ai.id_identity, s.id_site
            FROM access_identity ai
            CROSS JOIN avocarbon_site s
            WHERE ai.email = '{email}'
              AND s.site_name = '{site_name}'
            ON CONFLICT DO NOTHING
            """
        )


def downgrade() -> None:
    op.drop_table("access_identity_site")
