"""Seed team access identities from the RBAC matrix (2026-06-30)

Roles assigned based on the activity/responsibility table:
  vp_conversion      — Joan Zhao
                       (Olivier Grimaud already exists in DB — skipped)
  purchasing_director — (Jiehua Zhang already exists in DB — skipped)
  global_purchaser   — Jenny Li, Sarah Wu  (group-level purchasers, confirmed)
  local_purchaser    — Hosni Ben Ali, Nicolas Masson, Yassine Chitit,
                       Lili Dong, Eduardo Rodriguez, Joan Zhao, Hyering Kang

Emails are in the format firstname.lastname@avocarbon.com — update any that differ.
Starter password: Avoc@2026!  (bcrypt rounds=12)
The INSERT is idempotent: an existing email is skipped (ON CONFLICT DO NOTHING).

Revision ID: 20260630_0066
Revises: 20260630_0065
Create Date: 2026-06-30
"""

from alembic import op

revision = "20260630_0066"
down_revision = "20260630_0065"
branch_labels = None
depends_on = None

# Starter password: Avoc@2026!
_PW_HASH = "$2b$12$i7U4AoDjKA2hWBLkEyUDB.9l/uXSOfpIDWipzYrZS6ZWuCQc8piBC"

# ---------------------------------------------------------------------------
# People to seed  —  (full_name, email, access_profile)
# Olivier Grimaud (vp_conversion) and Jiehua Zhang (purchasing_director)
# are intentionally omitted — they already exist in the database.
# ---------------------------------------------------------------------------
_USERS = [
    # ── Global Purchaser (group-level, confirmed) ────────────────────────────
    ("Jenny Li", "jenny.li@avocarbon.com", "global_purchaser"),
    ("Sarah Wu", "sarah.wu@avocarbon.com", "global_purchaser"),
    # ── Local Purchaser ──────────────────────────────────────────────────────
    ("Hosni Ben Ali", "hosni.benali@avocarbon.com", "local_purchaser"),
    ("Nicolas Masson", "nicolas.masson@avocarbon.com", "local_purchaser"),
    ("Yassine Chitit", "yassine.chitit@avocarbon.com", "local_purchaser"),
    ("Lili Dong", "lili.dong@avocarbon.com", "local_purchaser"),
    ("Eduardo Rodriguez", "eduardo.rodriguez@avocarbon.com", "local_purchaser"),
    ("Joan Zhao", "joan.zhao@avocarbon.com", "local_purchaser"),
    ("Hyering Kang", "hyering.kang@avocarbon.com", "local_purchaser"),
]


def upgrade() -> None:
    for full_name, email, access_profile in _USERS:
        op.execute(
            f"""
            INSERT INTO access_identity (email, full_name, access_profile,
                                         password_hash, auth_source, registration_status)
            VALUES (
                '{email}',
                '{full_name}',
                '{access_profile}',
                '{_PW_HASH}',
                'local',
                'active'
            )
            ON CONFLICT (email) DO NOTHING
            """
        )


def downgrade() -> None:
    emails = ", ".join(f"'{email}'" for _, email, _ in _USERS)
    op.execute(f"DELETE FROM access_identity WHERE email IN ({emails})")
