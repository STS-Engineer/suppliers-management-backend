"""Seed Avocarbon site reference data.

Revision ID: 20260520_0002
Revises: 20260520_0001
Create Date: 2026-05-20
"""

from alembic import op


revision = "20260520_0002"
down_revision = "20260520_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO avocarbon_site (site_name, address_line, city, country, active)
        VALUES
            ('SCEET', 'Zone industrielle Elfahs', 'Zaghouane', 'Tunisia', TRUE),
            ('SAME', 'Zone industrielle Elfahs', 'Zaghouane', 'Tunisia', TRUE),
            ('Poitiers', '9 rue des Imprimeurs', 'Poitiers', 'France', TRUE),
            ('Cyclam', '75 rue Robert Le Coq', 'Amiens', 'France', TRUE),
            ('GMBH Frankfurt', 'Talstrasse 112', 'Frankfurt am Main', 'Germany', TRUE),
            ('Chennai', '25/A2, Dairy Plant Road SIDCO Industrial Estate', 'Chennai', 'India', TRUE),
            ('Daegu', '306, Nongong-ro, Nongong-eup', 'Daegu', 'Korea', TRUE),
            ('Tianjin', 'Junling Road 17', 'Tianjin', 'China', TRUE),
            ('Monterrey', 'San Sebastian 110', 'Guadalupe', 'Mexico', TRUE),
            ('Kunshan', 'No.9 Dongtinghu Road', 'Kunshan', 'China', TRUE)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM avocarbon_site
        WHERE site_name IN (
            'SCEET',
            'SAME',
            'Poitiers',
            'Cyclam',
            'GMBH Frankfurt',
            'Chennai',
            'Daegu',
            'Tianjin',
            'Monterrey',
            'Kunshan'
        )
        """
    )
