"""Track A step 6: runtime settings seed for queue fallback controls."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260222_0003"
down_revision: str | None = "20260222_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    runtime_settings = sa.table(
        "runtime_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(
        runtime_settings,
        [
            {
                "key": "bus.redis_fallback_to_inmemory",
                "value": "true",
                "description": (
                    "Fallback to in-memory queue when Redis BUS_BACKEND "
                    "cannot be reached at startup."
                ),
            }
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("runtime_settings")
