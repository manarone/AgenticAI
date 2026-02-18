import asyncio
from datetime import datetime, timedelta
from uuid import UUID

from libs.common.db import AsyncSessionLocal
from libs.common.enums import RiskTier, TaskStatus, TaskType
from libs.common.repositories import CoreRepository
from libs.common.schemas import TaskEnvelope


async def _create_shell_task(command: str, payload_extra: dict | None = None):
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, convo = await repo.get_or_create_default_tenant_user()
        payload = {'command': command}
        payload.update(payload_extra or {})
        task = await repo.create_task(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=convo.id,
            task_type='shell',
            risk_tier='L2',
            payload=payload,
            status=TaskStatus.QUEUED,
        )
        await db.commit()
    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant.id),
        user_id=UUID(user.id),
        task_type=TaskType.SHELL,
        payload=payload,
        risk_tier=RiskTier.L2,
        created_at=datetime.utcnow(),
    )
    return task, envelope


async def test_executor_allows_readonly_shell():
    from services.executor.main import _process_task_once, bus

    _, envelope = await _create_shell_task('uname -a')

    await _process_task_once('1-0', envelope)
    results = await bus.read_results(consumer_name='test-readonly', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is True
    assert result.output


async def test_executor_blocks_hard_blocked_command():
    from services.executor.main import _process_task_once, bus

    task, envelope = await _create_shell_task('rm -rf /')
    await _process_task_once('1-0', envelope)

    results = await bus.read_results(consumer_name='test-blocked', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is False
    assert 'blocked' in (result.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED


async def test_executor_remote_shell_disabled_by_default():
    from services.executor.main import _process_task_once, bus

    task, envelope = await _create_shell_task('uname -a', payload_extra={'remote_host': 'example-host'})
    await _process_task_once('1-0', envelope)

    results = await bus.read_results(consumer_name='test-remote-disabled', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is False
    assert 'remote shell execution is disabled' in (result.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED


async def test_executor_rejects_remote_host_option_injection(monkeypatch):
    from services.executor import main as executor_main

    monkeypatch.setattr(executor_main.settings, 'shell_remote_enabled', True)
    task, envelope = await _create_shell_task('uname -a', payload_extra={'remote_host': '-oProxyCommand=bad'})
    await executor_main._process_task_once('1-0', envelope)

    results = await executor_main.bus.read_results(consumer_name='test-remote-injection', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is False
    assert 'invalid remote host' in (result.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED


async def test_executor_denies_mutating_shell_without_grant_or_direct_approval():
    from services.executor.main import _process_task_once, bus

    task, envelope = await _create_shell_task('touch /tmp/agentai-no-grant.txt')
    await _process_task_once('1-0', envelope)

    results = await bus.read_results(consumer_name='test-no-grant', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is False
    assert 'requires approval' in (result.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1
        assert updated.status == TaskStatus.FAILED


async def test_executor_allows_mutating_shell_with_queue_time_grant_proof_after_expiry():
    from services.executor.main import _process_task_once, bus

    task, envelope = await _create_shell_task('touch /tmp/agentai-queued-grant.txt')

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        grant, _ = await repo.issue_approval_grant(
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            scope='shell_mutation',
            ttl_minutes=10,
        )
        stored = await repo.get_task(task.id)
        assert stored is not None
        stored.payload = {**(stored.payload or {}), 'grant_id': grant.id}
        grant.expires_at = stored.created_at + timedelta(seconds=1)
        await db.commit()

    await asyncio.sleep(1.1)
    await _process_task_once('1-0', envelope)

    results = await bus.read_results(consumer_name='test-queued-proof', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is True

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.attempts == 1


async def test_executor_rejects_empty_shell_command():
    from services.executor.main import _process_task_once, bus

    task, envelope = await _create_shell_task('')
    await _process_task_once('1-0', envelope)

    results = await bus.read_results(consumer_name='test-empty-shell', count=10, block_ms=10)
    assert results
    _, result = results[-1]
    assert result.success is False
    assert 'empty shell command' in (result.error or '').lower()

    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        updated = await repo.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.FAILED


def test_shell_env_ensures_default_path(monkeypatch):
    from services.executor import main as executor_main

    monkeypatch.setattr(executor_main.settings, 'shell_env_allowlist', 'PATH,HOME')
    monkeypatch.delenv('PATH', raising=False)
    monkeypatch.setenv('HOME', '/tmp/agentai-home')

    env = executor_main._shell_env()
    assert env['PATH'] == '/usr/local/bin:/usr/bin:/bin'
    assert env['HOME'] == '/tmp/agentai-home'
