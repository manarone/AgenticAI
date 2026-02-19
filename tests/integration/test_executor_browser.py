from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from libs.common.db import AsyncSessionLocal
from libs.common.enums import RiskTier, TaskStatus, TaskType
from libs.common.repositories import CoreRepository
from libs.common.schemas import TaskEnvelope


def test_internal_browser_action_auth(monkeypatch):
    from services.executor.main import app, settings

    async def fake_run_browser_action(action, args, *, session_id=None):
        return {'ok': True, 'action': action, 'summary': 'done', 'artifacts': []}

    monkeypatch.setattr(settings, 'browser_enabled', True)
    monkeypatch.setattr(settings, 'executor_internal_token', 'secret-token')
    monkeypatch.setattr('services.executor.main.run_browser_action', fake_run_browser_action)

    payload = {
        'tenant_id': 'tenant-1',
        'user_id': 'user-1',
        'action': 'open',
        'args': {'url': 'https://example.com'},
        'session_id': 'abc',
        'chat_id': '100',
    }

    with TestClient(app) as client:
        unauthorized = client.post('/internal/browser/action', json=payload)
        assert unauthorized.status_code == 401

        forbidden = client.post(
            '/internal/browser/action',
            json=payload,
            headers={'Authorization': 'Bearer wrong-token'},
        )
        assert forbidden.status_code == 403

        ok = client.post(
            '/internal/browser/action',
            json=payload,
            headers={'Authorization': 'Bearer secret-token'},
        )
        assert ok.status_code == 200
        data = ok.json()
        assert data['ok'] is True
        assert data['mode'] == 'sync'


async def _create_browser_task(*, action: str, payload_args: dict, chat_id: str = '2001', status: TaskStatus = TaskStatus.QUEUED):
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, user, convo = await repo.get_or_create_default_tenant_user()
        payload = {'action': action, 'args': payload_args, 'session_id': 'session-1', 'chat_id': chat_id}
        task = await repo.create_task(
            tenant_id=tenant.id,
            user_id=user.id,
            conversation_id=convo.id,
            task_type='browser',
            risk_tier='L3',
            payload=payload,
            status=status,
        )
        await db.commit()
        return task, tenant.id, user.id


async def _task_state(task_id: str):
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        return await repo.get_task(task_id)


async def _latest_bus_result():
    from services.executor.main import bus

    results = await bus.read_results(consumer_name='test-browser', count=10, block_ms=10)
    assert results
    return results[-1][1]


async def test_executor_browser_task_sends_screenshot_artifact(monkeypatch, tmp_path: Path):
    from services.executor.main import _process_task_once, settings, telegram

    sent = []
    screenshot = tmp_path / 'browser-shot.png'
    screenshot.write_bytes(b'png')

    async def fake_run_browser_action(action, args, *, session_id=None):
        return {'ok': True, 'action': action, 'summary': 'shot captured', 'artifacts': [{'path': str(screenshot)}]}

    async def fake_send_photo(chat_id, photo_path, caption=None):
        sent.append({'chat_id': chat_id, 'photo_path': photo_path, 'caption': caption})

    monkeypatch.setattr(settings, 'browser_enabled', True)
    monkeypatch.setattr('services.executor.main.run_browser_action', fake_run_browser_action)
    monkeypatch.setattr(telegram, 'send_photo', fake_send_photo)

    task, tenant_id, user_id = await _create_browser_task(action='screenshot', payload_args={})
    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant_id),
        user_id=UUID(user_id),
        task_type=TaskType.BROWSER,
        payload=task.payload,
        risk_tier=RiskTier.L3,
        created_at=datetime.utcnow(),
    )
    await _process_task_once('browser-shot-1', envelope)

    result = await _latest_bus_result()
    assert result.success is True
    assert 'sent 1 browser artifact' in (result.output or '').lower()
    assert sent and sent[0]['chat_id'] == '2001'


async def test_executor_browser_task_fails_on_unsupported_action(monkeypatch):
    from services.executor.main import _process_task_once, settings

    monkeypatch.setattr(settings, 'browser_enabled', True)

    task, tenant_id, user_id = await _create_browser_task(action='bad-action', payload_args={})
    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(tenant_id),
        user_id=UUID(user_id),
        task_type=TaskType.BROWSER,
        payload=task.payload,
        risk_tier=RiskTier.L3,
        created_at=datetime.utcnow(),
    )
    await _process_task_once('browser-bad-1', envelope)

    result = await _latest_bus_result()
    assert result.success is False
    assert 'unsupported browser action' in (result.error or '').lower()
    updated = await _task_state(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.FAILED
    assert updated.attempts == 1
