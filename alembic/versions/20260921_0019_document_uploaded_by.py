"""documents.uploaded_by — who to check STOP/MOVE-TO-BIN ownership against

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-21

STOP and MOVE TO BIN need a USER-role caller to be checked against the
document they're acting on ("their own active documents"). There is no
login system yet, so this is free text captured at upload time — the
same trust model as corrected_by/confirmed_by elsewhere — not a foreign
key to a users table. Nullable and backfilled as NULL: existing rows
predate the field, and a USER caller cannot claim ownership of a NULL
row (see app.services.document_lifecycle._authorize), so nothing already
uploaded becomes newly actionable by a USER-role caller as a side effect
of this migration.
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("uploaded_by", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "uploaded_by")
