from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from agenticai.db.base import Base
from agenticai.db.models import (
    Approval,
    ApprovalDecision,
    AuditEvent,
    BypassMode,
    Organization,
    RiskTier,
    Task,
    TaskStatus,
    User,
    UserPolicyOverride,
)
from agenticai.db.session import build_engine
from alembic import command


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _index_names(engine, table_name: str) -> set[str]:
    inspector = inspect(engine)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def test_phase2_schema_upgrade_downgrade_and_queryability(tmp_path: Path) -> None:
    """Track B migration should upgrade/downgrade cleanly with queryable new schema."""
    database_url = f"sqlite:///{tmp_path}/phase2-schema.db"
    config = _alembic_config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert {"approvals", "audit_events", "user_policy_overrides"} <= table_names

    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    assert {
        "risk_tier",
        "approval_required",
        "approval_decision",
        "approval_requested_at",
        "approval_decided_at",
        "approved_by_user_id",
        "execution_backend",
        "execution_metadata",
        "execution_attempts",
        "execution_last_heartbeat_at",
    } <= task_columns

    assert {"ix_tasks_status_updated_at", "ix_tasks_org_approval_required_status"} <= _index_names(
        engine,
        "tasks",
    )
    assert {"ix_approvals_org_decision_created_at"} <= _index_names(engine, "approvals")
    assert {"ix_audit_events_org_created_at"} <= _index_names(engine, "audit_events")
    assert {"ix_user_policy_overrides_org_bypass_mode"} <= _index_names(
        engine,
        "user_policy_overrides",
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, slug, name) "
                "VALUES (:id, :slug, :name)"
            ),
            {"id": "org-1", "slug": "org-one", "name": "Org One"},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, org_id, telegram_user_id, display_name) "
                "VALUES (:id, :org_id, :telegram_user_id, :display_name)"
            ),
            {
                "id": "user-1",
                "org_id": "org-1",
                "telegram_user_id": 1234567,
                "display_name": "Schema Tester",
            },
        )
        connection.execute(
            text(
                "INSERT INTO tasks (id, org_id, requested_by_user_id, status, prompt) "
                "VALUES (:id, :org_id, :requested_by_user_id, :status, :prompt)"
            ),
            {
                "id": "task-1",
                "org_id": "org-1",
                "requested_by_user_id": "user-1",
                "status": "QUEUED",
                "prompt": "check schema",
            },
        )
        connection.execute(
            text(
                "INSERT INTO approvals "
                "(id, org_id, task_id, requested_by_user_id, risk_tier, decision, reason) "
                "VALUES (:id, :org_id, :task_id, :requested_by_user_id, :risk_tier, :decision, "
                ":reason)"
            ),
            {
                "id": "approval-1",
                "org_id": "org-1",
                "task_id": "task-1",
                "requested_by_user_id": "user-1",
                "risk_tier": "HIGH",
                "decision": "PENDING",
                "reason": "Needs confirmation",
            },
        )
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id, org_id, task_id, actor_user_id, event_type, event_payload) "
                "VALUES (:id, :org_id, :task_id, :actor_user_id, :event_type, :event_payload)"
            ),
            {
                "id": "audit-1",
                "org_id": "org-1",
                "task_id": "task-1",
                "actor_user_id": "user-1",
                "event_type": "task.lifecycle.created",
                "event_payload": "{\"source\":\"migration-test\"}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO user_policy_overrides "
                "(id, org_id, user_id, bypass_mode, reason) "
                "VALUES (:id, :org_id, :user_id, :bypass_mode, :reason)"
            ),
            {
                "id": "policy-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "bypass_mode": "LOW_RISK_ONLY",
                "reason": "Temporary exception",
            },
        )

        assert connection.execute(text("SELECT COUNT(*) FROM approvals")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT COUNT(*) FROM user_policy_overrides")).scalar_one()
            == 1
        )

    engine.dispose()

    command.downgrade(config, "20260222_0005")
    downgraded_engine = create_engine(database_url)
    downgraded_tables = set(inspect(downgraded_engine).get_table_names())
    assert "approvals" not in downgraded_tables
    assert "audit_events" not in downgraded_tables
    assert "user_policy_overrides" not in downgraded_tables

    downgraded_task_columns = {
        column["name"] for column in inspect(downgraded_engine).get_columns("tasks")
    }
    assert "risk_tier" not in downgraded_task_columns
    assert "approval_required" not in downgraded_task_columns
    assert "execution_backend" not in downgraded_task_columns
    downgraded_engine.dispose()


def test_track_b_models_relationships_are_queryable(tmp_path: Path) -> None:
    """ORM models should support Track B enums and table relationships."""
    database_url = f"sqlite:///{tmp_path}/phase2-models.db"
    engine = build_engine(database_url)
    Base.metadata.create_all(bind=engine)

    with Session(bind=engine) as session:
        org = Organization(id="org-a", slug="org-a", name="Org A")
        user = User(
            id="user-a",
            org_id=org.id,
            telegram_user_id=99887766,
            display_name="Model Tester",
        )
        task = Task(
            id="task-a",
            org_id=org.id,
            requested_by_user_id=user.id,
            status=TaskStatus.WAITING_APPROVAL.value,
            prompt="risky task",
            risk_tier=RiskTier.HIGH.value,
            approval_required=True,
            approval_decision=ApprovalDecision.PENDING.value,
            execution_backend="docker",
            execution_attempts=1,
        )
        approval = Approval(
            id="approval-a",
            org_id=org.id,
            task_id=task.id,
            requested_by_user_id=user.id,
            risk_tier=RiskTier.HIGH.value,
            decision=ApprovalDecision.PENDING.value,
            reason="Awaiting approval",
        )
        audit_event = AuditEvent(
            id="audit-a",
            org_id=org.id,
            task_id=task.id,
            actor_user_id=user.id,
            event_type="task.lifecycle.waiting_approval",
            event_payload='{"state":"WAITING_APPROVAL"}',
        )
        override = UserPolicyOverride(
            id="override-a",
            org_id=org.id,
            user_id=user.id,
            bypass_mode=BypassMode.LOW_RISK_ONLY.value,
            reason="Pilot rollout",
        )
        session.add_all([org, user, task, approval, audit_event, override])
        session.commit()

    with Session(bind=engine) as session:
        loaded_task = session.execute(select(Task).where(Task.id == "task-a")).scalar_one()
        assert loaded_task.risk_tier == RiskTier.HIGH.value
        assert loaded_task.approval_required is True
        assert loaded_task.approval_decision == ApprovalDecision.PENDING.value
        assert loaded_task.execution_backend == "docker"
        assert loaded_task.approvals[0].reason == "Awaiting approval"
        assert loaded_task.audit_events[0].event_type == "task.lifecycle.waiting_approval"

        loaded_user = session.execute(select(User).where(User.id == "user-a")).scalar_one()
        assert loaded_user.policy_override is not None
        assert loaded_user.policy_override.bypass_mode == BypassMode.LOW_RISK_ONLY.value

    engine.dispose()
