"""seed avocarbon_site data

Revision ID: 5a9a60468ce4
Revises: 8db63466d0ac
Create Date: 2026-05-13 11:12:42.524637
"""
from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = '5a9a60468ce4'
down_revision = '8db63466d0ac'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO avocarbon_site (
            site_name,
            address_line,
            city,
            country,
            active
        )
        VALUES
        (
            'SCEET',
            'Zone industrielle Elfahs',
            'Zaghouane',
            'Tunisia',
            TRUE
        ),
        (
            'SAME',
            'Zone industrielle Elfahs',
            'Zaghouane',
            'Tunisia',
            TRUE
        ),
        (
            'Poitiers',
            '9 rue des Imprimeurs',
            'Poitiers',
            'France',
            TRUE
        ),
        (
            'Cyclam',
            '75 rue Robert Le Coq',
            'Amiens',
            'France',
            TRUE
        ),
        (
            'GMBH Frankfurt',
            'Talstrasse 112',
            'Frankfurt am Main',
            'Germany',
            TRUE
        ),
        (
            'Chennai',
            '25/A2, Dairy Plant Road SIDCO Industrial Estate',
            'Chennai',
            'India',
            TRUE
        ),
        (
            'Daegu',
            '306, Nongong-ro, Nongong-eup',
            'Daegu',
            'Korea',
            TRUE
        ),
        (
            'Tianjin',
            'Junling Road 17',
            'Tianjin',
            'China',
            TRUE
        ),
               (
    'Monterrey',
    'San Sebastian 110',
    'Guadalupe',
    'Mexico',
    TRUE
),
        (
            'Kunshan',
            'No.9 Dongtinghu Road',
            'Kunshan',
            'China',
            TRUE
        );
    """)


def downgrade():
    op.execute("""
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
        );
    """)