"""Track A step 7: tenancy hardening constraints and idempotency keys."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260222_0005"
down_revision: str | None = "20260222_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME_KEY = "bus.redis_fallback_to_inmemory"
_RUNTIME_DESCRIPTION = (
    "Fallback to in-memory queue when Redis BUS_BACKEND cannot be reached at startup."
)
_runtime_settings = sa.table(
    "runtime_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
    sa.column("description", sa.String),
)


def _set_runtime_fallback_setting(value: str) -> None:
    """Idempotently persist the fallback runtime setting value."""
    op.execute(sa.delete(_runtime_settings).where(_runtime_settings.c.key == _RUNTIME_KEY))
    op.bulk_insert(
        _runtime_settings,
        [
            {
                "key": _RUNTIME_KEY,
                "value": value,
                "description": _RUNTIME_DESCRIPTION,
            }
        ],
    )


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.create_unique_constraint(
            "uq_tasks_org_user_idempotency_key",
            ["org_id", "requested_by_user_id", "idempotency_key"],
        )
        batch_op.create_index("ix_tasks_idempotency_key", ["idempotency_key"], unique=False)

    duplicate_telegram_users = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT telegram_user_id "
                "FROM users "
                "GROUP BY telegram_user_id "
                "HAVING COUNT(DISTINCT org_id) > 1 "
                "LIMIT 5"
            )
        )
        .fetchall()
    )
    if duplicate_telegram_users:
        duplicate_ids = ", ".join(str(row[0]) for row in duplicate_telegram_users)
        raise RuntimeError(
            "Cannot enforce global telegram_user_id uniqueness. "
            f"Resolve duplicate IDs first: {duplicate_ids}"
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_org_telegram_user_id", type_="unique")
        batch_op.create_unique_constraint("uq_users_telegram_user_id", ["telegram_user_id"])

    _set_runtime_fallback_setting("false")


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

    _set_runtime_fallback_setting("true")
