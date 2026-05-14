"""add timestamps and last_status_change to supplier_site_relation

Revision ID: 26260514_0003
Revises: 26260514_0002
Create Date: 2026-05-14 10:05:00.000000
"""

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = 'ade5d80f331c'
down_revision = '26260514_0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add created_at column (timestamp for when relation was created)
    op.add_column(
        'supplier_site_relation',
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True)
    )
    
    # Add inactivated_at column (timestamp for when relation was inactivated)
    op.add_column(
        'supplier_site_relation',
        sa.Column('inactivated_at', sa.DateTime(), nullable=True)
    )
    
    # Add last_status_change column (timestamp for tracking last status/grade/class change)
    op.add_column(
        'supplier_site_relation',
        sa.Column('last_status_change', sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    # Remove columns in reverse order
    op.drop_column('supplier_site_relation', 'last_status_change')
    op.drop_column('supplier_site_relation', 'inactivated_at')
    op.drop_column('supplier_site_relation', 'created_at')
