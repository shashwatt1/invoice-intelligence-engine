"""commercial review — reviewer user id and role — additive

Revision ID: 0026
Revises: 0024
Create Date: 2026-09-30

Additive only; two nullable columns on `master_commercial_reviews`. No
existing row is rewritten and nothing outside the Product Master is touched.

A decision already records the reviewer's username. These record WHO more
completely: the account id (so a decision survives a username change) and
the role the reviewer held when deciding (so an audit can show a MANAGER or
an ADMIN made it, even if the role changes later). Both are taken from the
authenticated session by the service, never from the request. Rows written
before this revision keep NULL — their role at the time is not known and is
not back-filled by guesswork.

Revises 0024, the deployed head. The unreleased 0025 (identifier resolution
records) also descends from 0024 and is re-parented when it is released.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("master_commercial_reviews",
                  sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("master_commercial_reviews",
                  sa.Column("reviewer_role", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        "fk_master_commercial_reviews_reviewer_user_id", "master_commercial_reviews", "users",
        ["reviewer_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_master_commercial_reviews_reviewer_user_id", "master_commercial_reviews",
                       type_="foreignkey")
    op.drop_column("master_commercial_reviews", "reviewer_role")
    op.drop_column("master_commercial_reviews", "reviewer_user_id")
