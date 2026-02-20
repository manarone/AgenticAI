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
            payload={'command': 'cat /definitely/missing/file'},
            status=TaskStatus.QUEUED,
        )
        await db.commit()

    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant.id),
        user_id=UUID(user.id),
        task_type=TaskType.SHELL,
        payload={'command': 'cat /definitely/missing/file'},
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
        assert updated.status == TaskStatus.FAILED


async def test_executor_web_task_fails_without_retry():
    from services.executor.main import _process_task_once, bus

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, convo = await repo.get_or_create_default_tenant_user()
        task = await repo.create_task(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=convo.id,
            task_type='web',
            risk_tier='L1',
            payload={'query': 'latest ai news'},
            status=TaskStatus.QUEUED,
        )
        await db.commit()

    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant.id),
        user_id=UUID(user.id),
        task_type=TaskType.WEB,
        payload={'query': 'latest ai news'},
        risk_tier=RiskTier.L1,
        created_at=datetime.utcnow(),
    )

    await _process_task_once('web-1', envelope)

    results = await bus.read_results(consumer_name='test-web', count=10, block_ms=10)
    assert results
    _, failure = results[-1]
    assert failure.success is False
    assert 'handled inline by coordinator' in (failure.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED


async def test_executor_retry_requeue_failure_marks_task_failed(monkeypatch):
    from services.executor import main as executor_main

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, convo = await repo.get_or_create_default_tenant_user()
        task = await repo.create_task(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=convo.id,
            task_type='shell',
            risk_tier='L2',
            payload={'command': 'cat /definitely/missing/file'},
            status=TaskStatus.QUEUED,
        )
        await db.commit()

    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant.id),
        user_id=UUID(user.id),
        task_type=TaskType.SHELL,
        payload={'command': 'cat /definitely/missing/file'},
        risk_tier=RiskTier.L2,
        created_at=datetime.utcnow(),
    )

    async def fail_publish_task(_):
        raise RuntimeError('requeue unavailable')

    monkeypatch.setattr(executor_main.bus, 'publish_task', fail_publish_task)
    await executor_main._process_task_once('retry-requeue-1', envelope)

    results = await executor_main.bus.read_results(consumer_name='test-requeue-fail', count=10, block_ms=10)
    assert results
    _, failure = results[-1]
    assert failure.success is False
    assert 'retry enqueue failed' in (failure.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED
        assert 'retry enqueue failed' in (updated.error or '').lower()


async def test_executor_result_publish_failure_marks_task_failed(monkeypatch):
    from services.executor import main as executor_main

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

    async def fail_publish_result(_):
        raise RuntimeError('result stream unavailable')

    monkeypatch.setattr(executor_main.bus, 'publish_result', fail_publish_result)
    await executor_main._process_task_once('publish-result-fail-1', envelope)

    results = await executor_main.bus.read_results(consumer_name='test-result-publish-fail', count=10, block_ms=10)
    assert results == []

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED
        assert 'failed to publish task result' in (updated.error or '').lower()
