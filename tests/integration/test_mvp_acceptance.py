import asyncio
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from libs.common.db import AsyncSessionLocal
from libs.common.enums import RiskTier, TaskStatus, TaskType
from libs.common.llm import LLMToolChatResult
from libs.common.repositories import CoreRepository
from libs.common.schemas import TaskEnvelope


async def _prepare_invite_code() -> str:
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, _, _ = await repo.get_or_create_default_tenant_user()
        invite = await repo.create_invite_code(tenant_id=tenant.id, ttl_hours=24)
        await db.commit()
        return invite.code


async def _latest_task_for_user(telegram_user_id: int):
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        identity = await repo.get_identity(str(telegram_user_id))
        if identity is None:
            return None
        tasks = await repo.list_user_tasks(identity.tenant_id, identity.user_id, limit=1)
        return tasks[0] if tasks else None


def test_mvp_acceptance_start_invite_success_and_failure_paths(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': str(text), 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        invalid_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': '/start not-a-real-code', 'from': {'id': 3001}, 'chat': {'id': 3001}}},
        )
        assert invalid_resp.status_code == 200

        valid_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3001}, 'chat': {'id': 3001}}},
        )
        assert valid_resp.status_code == 200

        reused_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3002}, 'chat': {'id': 3002}}},
        )
        assert reused_resp.status_code == 200

    assert any('invite failed' in msg['text'].lower() for msg in sent_messages)
    assert any('invite code accepted' in msg['text'].lower() for msg in sent_messages)
    assert any('already used' in msg['text'].lower() for msg in sent_messages)


def test_mvp_acceptance_direct_response_flow(monkeypatch):
    from services.coordinator.main import app, llm, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': str(text), 'reply_markup': reply_markup})

    async def fake_chat_with_tools(**kwargs):
        return LLMToolChatResult(text='Acceptance direct reply.', prompt_tokens=1, completion_tokens=1, tool_records=[])

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(llm, 'chat_with_tools', fake_chat_with_tools)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        start_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3011}, 'chat': {'id': 3011}}},
        )
        assert start_resp.status_code == 200

        msg_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'hello acceptance test', 'from': {'id': 3011}, 'chat': {'id': 3011}}},
        )
        assert msg_resp.status_code == 200

    assert any('acceptance direct reply' in msg['text'].lower() for msg in sent_messages)


def test_mvp_acceptance_shell_approval_to_queue(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': str(text), 'reply_markup': reply_markup})

    async def fake_answer_callback_query(callback_query_id, text):
        return None

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(telegram, 'answer_callback_query', fake_answer_callback_query)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3021}, 'chat': {'id': 3021}}},
        )
        shell_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: systemctl restart nginx', 'from': {'id': 3021}, 'chat': {'id': 3021}}},
        )
        assert shell_resp.status_code == 200

        approval_msgs = [msg for msg in sent_messages if msg['reply_markup']]
        assert approval_msgs

        callback_data = approval_msgs[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        callback_resp = client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-mvp-approval', 'data': callback_data, 'from': {'id': 3021}}},
        )
        assert callback_resp.status_code == 200

    latest = asyncio.run(_latest_task_for_user(3021))
    assert latest is not None
    assert latest.status == TaskStatus.QUEUED
    assert any('approved and queued' in msg['text'].lower() for msg in sent_messages)


def test_mvp_acceptance_status_and_cancel_all(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': str(text), 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3031}, 'chat': {'id': 3031}}},
        )

        queue_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: ls -la', 'from': {'id': 3031}, 'chat': {'id': 3031}}},
        )
        assert queue_resp.status_code == 200

        status_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': '/status', 'from': {'id': 3031}, 'chat': {'id': 3031}}},
        )
        assert status_resp.status_code == 200

        cancel_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': '/cancel all', 'from': {'id': 3031}, 'chat': {'id': 3031}}},
        )
        assert cancel_resp.status_code == 200

    latest = asyncio.run(_latest_task_for_user(3031))
    assert latest is not None
    assert latest.status == TaskStatus.CANCELED

    assert any('recent tasks:' in msg['text'].lower() for msg in sent_messages)
    assert any('canceled 1 task' in msg['text'].lower() for msg in sent_messages)


def test_mvp_acceptance_executor_retry_exhaustion_notifies_failure(monkeypatch):
    from services.coordinator import main as coordinator_main
    from services.executor import main as executor_main

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': str(text), 'reply_markup': reply_markup})

    monkeypatch.setattr(executor_main, 'bus', coordinator_main.bus)
    monkeypatch.setattr(coordinator_main.telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(coordinator_main.app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3041}, 'chat': {'id': 3041}}},
        )
        queue_resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: cat /definitely/missing/file', 'from': {'id': 3041}, 'chat': {'id': 3041}}},
        )
        assert queue_resp.status_code == 200

        task = asyncio.run(_latest_task_for_user(3041))
        assert task is not None

        envelope = TaskEnvelope(
            task_id=UUID(task.id),
            tenant_id=UUID(task.tenant_id),
            user_id=UUID(task.user_id),
            task_type=TaskType(task.task_type),
            payload=task.payload,
            risk_tier=RiskTier(task.risk_tier),
            created_at=datetime.now(timezone.utc),
        )

    asyncio.run(executor_main._process_task_once('mvp-accept-1', envelope))
    asyncio.run(executor_main._process_task_once('mvp-accept-2', envelope))

    with TestClient(coordinator_main.app):
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if any(' failed' in msg['text'].lower() and 'task `' in msg['text'].lower() for msg in sent_messages):
                break
            time.sleep(0.05)

    latest = asyncio.run(_latest_task_for_user(3041))
    assert latest is not None
    assert latest.status == TaskStatus.FAILED
    assert any(f"Task `{latest.id}` failed" in msg['text'] for msg in sent_messages)


def test_mvp_acceptance_time_sensitive_web_route_uses_dated_sources(monkeypatch):
    from services.coordinator.main import app, llm, telegram, web_search_client

    sent_messages = []
    seen_queries = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': str(text), 'reply_markup': reply_markup})

    async def fake_search(query, *, depth='balanced', max_results=None, time_range=None, categories=None):
        seen_queries.append(
            {
                'query': query,
                'depth': depth,
                'max_results': max_results,
                'time_range': time_range,
                'categories': categories,
            }
        )
        return {
            'query': query,
            'depth': depth,
            'time_range': time_range,
            'categories': categories,
            'results': [
                {
                    'title': 'Acceptance News',
                    'url': 'https://example.com/acceptance-news',
                    'snippet': 'Acceptance test source with date.',
                    'published_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                }
            ],
        }

    async def fail_chat_with_tools(**kwargs):
        raise AssertionError('LLM tool mode should not run for forced time-sensitive NL web queries')

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(web_search_client, 'search', fake_search)
    monkeypatch.setattr(llm, 'chat_with_tools', fail_chat_with_tools)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 3051}, 'chat': {'id': 3051}}},
        )

        query_resp = client.post(
            '/telegram/webhook',
            json={
                'message': {
                    'text': 'tell me new ai news that came out today',
                    'from': {'id': 3051},
                    'chat': {'id': 3051},
                }
            },
        )
        assert query_resp.status_code == 200

    assert seen_queries
    assert seen_queries[-1]['time_range'] == 'day'
    assert seen_queries[-1]['categories'] == 'news'

    final_text = sent_messages[-1]['text']
    assert 'web summary for:' in final_text.lower()
    assert 'sources:' in final_text.lower()
    assert '(date:' in final_text.lower()
    assert 'https://example.com/acceptance-news' in final_text
