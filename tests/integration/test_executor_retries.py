from datetime import datetime
from uuid import UUID

from libs.common.db import AsyncSessionLocal
from libs.common.enums import RiskTier, TaskStatus, TaskType
from libs.common.repositories import CoreRepository
from libs.common.schemas import TaskEnvelope


async def test_executor_retries_then_fails():
    from services.executor.main import _process_task_once, bus

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, convo = await repo.get_or_create_default_tenant_user()
        task = await repo.create_task(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=convo.id,
            task_type='shell',
            risk_tier='L2',
            payload={'command': 'uname -a'},
            status=TaskStatus.QUEUED,
        )
        await db.commit()

    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant.id),
        user_id=UUID(user.id),
        task_type=TaskType.SHELL,
        payload={'command': 'uname -a'},
        risk_tier=RiskTier.L2,
        created_at=datetime.utcnow(),
    )

    await _process_task_once('1-0', envelope)
    await _process_task_once('2-0', envelope)

    results = await bus.read_results(consumer_name='test', count=10, block_ms=10)
    assert results
    _, failure = results[-1]
    assert failure.success is False
    assert failure.error

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts >= 2
