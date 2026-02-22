"""Track A step 4: Telegram webhook idempotency persistence."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260222_0002"
down_revision: str | None = "20260221_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "telegram_webhook_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('TASK_ENQUEUED', 'REGISTERED', 'REGISTRATION_REQUIRED', 'IGNORED')",
            name="ck_telegram_webhook_events_outcome",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("update_id", name="uq_telegram_webhook_events_update_id"),
    )

    op.create_index(
        "ix_telegram_webhook_events_telegram_user_id",
        "telegram_webhook_events",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_webhook_events_task_id",
        "telegram_webhook_events",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_telegram_webhook_events_task_id", table_name="telegram_webhook_events")
    op.drop_index(
        "ix_telegram_webhook_events_telegram_user_id",
        table_name="telegram_webhook_events",
    )
    op.drop_table("telegram_webhook_events")
