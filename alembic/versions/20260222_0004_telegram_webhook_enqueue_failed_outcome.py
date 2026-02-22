"""Track A step 6: allow ENQUEUE_FAILED Telegram webhook outcomes."""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260222_0004"
down_revision: str | None = "20260222_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OUTCOME_CONSTRAINT = "ck_telegram_webhook_events_outcome"
_OUTCOME_EXPRESSION_WITH_FAILED = (
    "outcome IN ('TASK_ENQUEUED', 'ENQUEUE_FAILED', 'REGISTERED', "
    "'REGISTRATION_REQUIRED', 'IGNORED')"
)
_OUTCOME_EXPRESSION_LEGACY = (
    "outcome IN ('TASK_ENQUEUED', 'REGISTERED', 'REGISTRATION_REQUIRED', 'IGNORED')"
)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("telegram_webhook_events") as batch_op:
        batch_op.drop_constraint(_OUTCOME_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_OUTCOME_CONSTRAINT, _OUTCOME_EXPRESSION_WITH_FAILED)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "UPDATE telegram_webhook_events "
        "SET outcome = 'TASK_ENQUEUED' "
        "WHERE outcome = 'ENQUEUE_FAILED'"
    )
    with op.batch_alter_table("telegram_webhook_events") as batch_op:
        batch_op.drop_constraint(_OUTCOME_CONSTRAINT, type_="check")
        batch_op.create_check_constraint(_OUTCOME_CONSTRAINT, _OUTCOME_EXPRESSION_LEGACY)
