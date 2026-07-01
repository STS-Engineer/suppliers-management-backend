"""eval_notification_jobs — pg_cron job queue + distributed lock

Revision ID: 20260701_0068
Revises: 20260630_0067
Create Date: 2026-07-01

Architecture: pg_cron inserts a 'pending' row here daily + fires pg_notify.
FastAPI listens for the notification and processes the row. If the app is
down when pg_cron fires, the startup scan picks up the missed job.
One row per calendar day (unique on scheduled_for) prevents duplicate sends.

After applying this migration, enable pg_cron on your PostgreSQL Flexible
Server and register the job by running the SQL in the comment at the bottom
of this file (or keep it in a runbook).
"""

from alembic import op
import sqlalchemy as sa

revision = "20260701_0068"
down_revision = "20260630_0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_notification_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # One row per calendar day — UNIQUE is the distributed lock
        sa.Column("scheduled_for", sa.Date(), nullable=False, unique=True),
        # pending → running → completed | failed
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        # who created it: pg_cron | startup | manual
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("notifications_sent", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )

    # -------------------------------------------------------------------
    # pg_cron setup — run this manually on your PostgreSQL Flexible Server
    # after enabling the pg_cron extension:
    #
    #   CREATE EXTENSION IF NOT EXISTS pg_cron;
    #
    #   SELECT cron.schedule(
    #     'eval-due-notifications',
    #     '0 8 * * *',
    #     $$
    #       INSERT INTO eval_notification_jobs (scheduled_for, status, source, created_at)
    #       VALUES (CURRENT_DATE, 'pending', 'pg_cron', NOW())
    #       ON CONFLICT (scheduled_for) DO NOTHING;
    #
    #       SELECT pg_notify(
    #         'eval_due',
    #         json_build_object('date', CURRENT_DATE::text, 'source', 'pg_cron')::text
    #       );
    #     $$
    #   );
    #
    # To verify the job is registered:
    #   SELECT * FROM cron.job;
    # -------------------------------------------------------------------


def downgrade() -> None:
    op.drop_table("eval_notification_jobs")
