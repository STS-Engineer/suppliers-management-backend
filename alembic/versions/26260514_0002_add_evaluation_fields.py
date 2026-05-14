"""add evaluation_comments and evaluation_suggestion to supplier_site_relation

Revision ID: 26260514_0002
Revises: 5a9a60468ce4
Create Date: 2026-05-14 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '26260514_0002'
down_revision = '5a9a60468ce4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add evaluation_comments column as TEXT (nullable)
    op.add_column(
        'supplier_site_relation',
        sa.Column('evaluation_comments', sa.Text(), nullable=True)
    )
    
    # Add evaluation_suggestion column as VARCHAR(255) (nullable)
    op.add_column(
        'supplier_site_relation',
        sa.Column('evaluation_suggestion', sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    # Remove evaluation_suggestion column
    op.drop_column('supplier_site_relation', 'evaluation_suggestion')
    
    # Remove evaluation_comments column
    op.drop_column('supplier_site_relation', 'evaluation_comments')
