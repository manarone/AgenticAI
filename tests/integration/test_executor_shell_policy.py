from datetime import datetime
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


async def test_remote_shell_uses_sanitized_env(monkeypatch):
    from services.executor import main as executor_main

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b'ok', b''

    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured['env'] = kwargs.get('env')
        return _FakeProc()

    monkeypatch.setattr(executor_main, '_shell_env', lambda: {'PATH': '/usr/bin'})
    monkeypatch.setattr(executor_main.asyncio, 'create_subprocess_exec', fake_create_subprocess_exec)

    output = await executor_main._run_remote_shell('example-host', 'uname -a')
    assert output == 'ok'
    assert captured['env'] == {'PATH': '/usr/bin'}
