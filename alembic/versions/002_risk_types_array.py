"""Migrate risk_type → risk_types (JSON), add network_id, drop operational_unit

Revision ID: 002
Revises: 001
Create Date: 2026-04-14

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column("alert_rules", sa.Column("risk_types", sa.JSON(), nullable=True))
    op.add_column(
        "alert_rules",
        sa.Column("network_id", sa.String(255), nullable=True),
    )

    # Backfill: wrap existing risk_type string into a single-element JSON array
    op.execute(
        "UPDATE alert_rules SET risk_types = json_build_array(risk_type), network_id = '' "
        "WHERE risk_types IS NULL"
    )

    # Make the new columns non-nullable now that data is populated
    op.alter_column("alert_rules", "risk_types", nullable=False)
    op.alter_column("alert_rules", "network_id", nullable=False)

    # Add index on network_id for dispatcher queries
    op.create_index("ix_alert_rules_network_id", "alert_rules", ["network_id"])

    # Drop old columns
    op.drop_column("alert_rules", "risk_type")
    op.drop_column("alert_rules", "operational_unit")


def downgrade() -> None:
    op.add_column(
        "alert_rules",
        sa.Column("risk_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "alert_rules",
        sa.Column("operational_unit", sa.String(255), nullable=True),
    )

    # Best-effort restore: take first element of risk_types array
    op.execute(
        "UPDATE alert_rules SET risk_type = risk_types->>0"
    )

    op.alter_column("alert_rules", "risk_type", nullable=False)

    op.drop_index("ix_alert_rules_network_id", "alert_rules")
    op.drop_column("alert_rules", "network_id")
    op.drop_column("alert_rules", "risk_types")
