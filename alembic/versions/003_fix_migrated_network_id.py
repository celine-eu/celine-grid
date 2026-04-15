"""Mark alert_rules rows that were backfilled with network_id='' in migration 002.

The 002 migration could not derive the correct DSO network_id at migration time
(it lives in Keycloak, not this DB).  Rows produced by that backfill carry an
empty-string sentinel.  This migration adds a boolean flag so the dispatcher and
any admin tooling can identify unattributed rules and skip or re-attribute them.

Re-attribution should be done via the API: each manager who logs in after this
migration will recreate their rules through the normal flow which captures the
correct network_id from their JWT.

Revision ID: 003
Revises: 002
Create Date: 2026-04-15

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add flag to distinguish properly-attributed rows from the 002 backfill placeholders
    op.add_column(
        "alert_rules",
        sa.Column("network_id_unset", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Mark every row that still carries the empty-string placeholder from migration 002
    op.execute("UPDATE alert_rules SET network_id_unset = true WHERE network_id = ''")


def downgrade() -> None:
    op.drop_column("alert_rules", "network_id_unset")
