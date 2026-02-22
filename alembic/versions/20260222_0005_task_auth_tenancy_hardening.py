"""Track A step 7: tenancy hardening constraints and idempotency keys."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260222_0005"
down_revision: str | None = "20260222_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.create_unique_constraint(
            "uq_tasks_org_user_idempotency_key",
            ["org_id", "requested_by_user_id", "idempotency_key"],
        )
        batch_op.create_index("ix_tasks_idempotency_key", ["idempotency_key"], unique=False)

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_org_telegram_user_id", type_="unique")
        batch_op.create_unique_constraint("uq_users_telegram_user_id", ["telegram_user_id"])

    op.execute(
        "UPDATE runtime_settings "
        "SET value = 'false', description = "
        "'Fallback to in-memory queue when Redis BUS_BACKEND cannot be reached at startup.' "
        "WHERE key = 'bus.redis_fallback_to_inmemory'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_telegram_user_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_users_org_telegram_user_id",
            ["org_id", "telegram_user_id"],
        )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_idempotency_key")
        batch_op.drop_constraint("uq_tasks_org_user_idempotency_key", type_="unique")
        batch_op.drop_column("idempotency_key")

    op.execute(
        "UPDATE runtime_settings "
        "SET value = 'true', description = "
        "'Fallback to in-memory queue when Redis BUS_BACKEND cannot be reached at startup.' "
        "WHERE key = 'bus.redis_fallback_to_inmemory'"
    )
