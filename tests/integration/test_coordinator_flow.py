import asyncio
import time
from datetime import datetime
from uuid import UUID

from fastapi.testclient import TestClient

from libs.common.db import AsyncSessionLocal
from libs.common.enums import TaskStatus
from libs.common.llm import LLMToolChatResult, ToolExecutionRecord
from libs.common.repositories import CoreRepository
from libs.common.schemas import TaskResult
from libs.common.web_search import WebSearchUnavailableError


async def _prepare_invite_code() -> str:
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        tenant, _, _ = await repo.get_or_create_default_tenant_user()
        invite = await repo.create_invite_code(tenant_id=tenant.id, ttl_hours=24)
        await db.commit()
        return invite.code


async def _has_active_shell_grant(telegram_user_id: int) -> bool:
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        identity = await repo.get_identity(str(telegram_user_id))
        if identity is None:
            return False
        return await repo.has_active_approval_grant(identity.tenant_id, identity.user_id, 'shell_mutation')


async def _latest_task_for_user(telegram_user_id: int):
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        identity = await repo.get_identity(str(telegram_user_id))
        if identity is None:
            return None
        tasks = await repo.list_user_tasks(identity.tenant_id, identity.user_id, limit=1)
        return tasks[0] if tasks else None


async def _create_task_for_user(telegram_user_id: int, *, status: TaskStatus, payload: dict):
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        identity = await repo.get_identity(str(telegram_user_id))
        assert identity is not None
        convo = await repo.get_or_create_conversation(identity.tenant_id, identity.user_id)
        task = await repo.create_task(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            conversation_id=convo.id,
            task_type='shell',
            risk_tier='L2',
            payload=payload,
            status=status,
        )
        await db.commit()
        return task, identity


def test_start_and_direct_response(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        start_payload = {
            'message': {
                'text': f'/start {invite_code}',
                'from': {'id': 101},
                'chat': {'id': 101},
            }
        }
        resp = client.post('/telegram/webhook', json=start_payload)
        assert resp.status_code == 200

        message_payload = {
            'message': {
                'text': 'hello coordinator',
                'from': {'id': 101},
                'chat': {'id': 101},
            }
        }
        resp2 = client.post('/telegram/webhook', json=message_payload)
        assert resp2.status_code == 200

    assert any('accepted' in m['text'].lower() for m in sent_messages)
    assert any('mvp fallback response' in m['text'].lower() for m in sent_messages)


def test_destructive_flow_waits_for_approval(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 201}, 'chat': {'id': 201}}},
        )

        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: delete /tmp/a', 'from': {'id': 201}, 'chat': {'id': 201}}},
        )
        assert resp.status_code == 200

    approval_msgs = [m for m in sent_messages if m['reply_markup']]
    assert approval_msgs
    assert 'needs approval' in approval_msgs[0]['text'].lower()
    assert 'delete /tmp/a' in approval_msgs[0]['text'].lower()


def test_blocked_shell_command_is_rejected(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 301}, 'chat': {'id': 301}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: rm -rf /', 'from': {'id': 301}, 'chat': {'id': 301}}},
        )
        assert resp.status_code == 200

    assert any('blocked by safety policy' in m['text'].lower() for m in sent_messages)
    assert not any(m['reply_markup'] for m in sent_messages if 'blocked' in m['text'].lower())


def test_shell_session_grant_skips_reapproval(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_answer_callback_query(callback_query_id, text):
        return None

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(telegram, 'answer_callback_query', fake_answer_callback_query)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 401}, 'chat': {'id': 401}}},
        )
        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: systemctl restart nginx', 'from': {'id': 401}, 'chat': {'id': 401}}},
        )

        approval_msgs = [m for m in sent_messages if m['reply_markup']]
        assert approval_msgs
        callback_data = approval_msgs[0]['reply_markup']['inline_keyboard'][0][0]['callback_data']

        client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-1', 'data': callback_data, 'from': {'id': 401}}},
        )

        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: systemctl restart redis', 'from': {'id': 401}, 'chat': {'id': 401}}},
        )

    approval_msgs = [m for m in sent_messages if m['reply_markup']]
    assert len(approval_msgs) == 1
    assert any('approved and queued' in m['text'].lower() for m in sent_messages)


def test_publish_task_failure_marks_task_failed_and_notifies(monkeypatch):
    from services.coordinator.main import app, bus, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_publish_task(envelope):
        raise RuntimeError('task bus unavailable')

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(bus, 'publish_task', fake_publish_task)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 421}, 'chat': {'id': 421}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: ls -la', 'from': {'id': 421}, 'chat': {'id': 421}}},
        )
        assert resp.status_code == 200

    task = asyncio.run(_latest_task_for_user(421))
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert 'failed to enqueue task' in (task.error or '').lower()
    assert any('could not be queued' in m['text'].lower() for m in sent_messages)


def test_approval_callback_replay_does_not_reissue_shell_grant(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []
    callback_answers = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_answer_callback_query(callback_query_id, text):
        callback_answers.append(str(text))
        return None

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(telegram, 'answer_callback_query', fake_answer_callback_query)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 501}, 'chat': {'id': 501}}},
        )
        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: systemctl restart nginx', 'from': {'id': 501}, 'chat': {'id': 501}}},
        )

        approval_msgs = [m for m in sent_messages if m['reply_markup']]
        assert approval_msgs
        callback_data = approval_msgs[0]['reply_markup']['inline_keyboard'][0][0]['callback_data']

        client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-allow', 'data': callback_data, 'from': {'id': 501}}},
        )
        assert asyncio.run(_has_active_shell_grant(501)) is True

        client.post(
            '/telegram/webhook',
            json={'message': {'text': '/cancel grant', 'from': {'id': 501}, 'chat': {'id': 501}}},
        )
        assert asyncio.run(_has_active_shell_grant(501)) is False

        client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-replay', 'data': callback_data, 'from': {'id': 501}}},
        )

    assert asyncio.run(_has_active_shell_grant(501)) is False
    assert sum(1 for m in sent_messages if 'approved and queued' in m['text'].lower()) == 1
    assert any('already processed' in text.lower() for text in callback_answers)


def test_result_consumer_notifies_when_executor_already_set_terminal_status(monkeypatch):
    from services.coordinator.main import app, bus, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 431}, 'chat': {'id': 431}}},
        )

        task, identity = asyncio.run(
            _create_task_for_user(431, status=TaskStatus.FAILED, payload={'command': 'shell: bad command'})
        )
        asyncio.run(
            bus.publish_result(
                TaskResult(
                    task_id=UUID(task.id),
                    tenant_id=UUID(identity.tenant_id),
                    user_id=UUID(identity.user_id),
                    success=False,
                    output='Task failed',
                    error='executor failed',
                    created_at=datetime.utcnow(),
                )
            )
        )

        deadline = time.time() + 2.0
        while time.time() < deadline and not any(f"Task `{task.id}` failed" in m['text'] for m in sent_messages):
            time.sleep(0.05)

    assert any(f"Task `{task.id}` failed" in m['text'] for m in sent_messages)
    latest = asyncio.run(_latest_task_for_user(431))
    assert latest is not None
    assert latest.status == TaskStatus.FAILED
    assert latest.result == 'Task failed'
    assert latest.error == 'executor failed'


def test_unknown_approval_callback_action_is_rejected(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []
    callback_answers = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_answer_callback_query(callback_query_id, text):
        callback_answers.append(str(text))
        return None

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(telegram, 'answer_callback_query', fake_answer_callback_query)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 551}, 'chat': {'id': 551}}},
        )
        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: systemctl restart nginx', 'from': {'id': 551}, 'chat': {'id': 551}}},
        )
        approval_msgs = [m for m in sent_messages if m['reply_markup']]
        assert approval_msgs
        approve_callback_data = approval_msgs[0]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        _, _, approval_id = approve_callback_data.partition(':')

        resp = client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-unknown', 'data': f'unknown:{approval_id}', 'from': {'id': 551}}},
        )
        assert resp.status_code == 200

    task = asyncio.run(_latest_task_for_user(551))
    assert task is not None
    assert task.status == TaskStatus.WAITING_APPROVAL
    assert any('unsupported approval action' in text.lower() for text in callback_answers)


def test_denied_callback_persists_canceled_status_when_cancel_publish_fails(monkeypatch):
    from services.coordinator.main import app, bus, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_publish_cancel(task_id):
        raise RuntimeError('cancel bus unavailable')

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(bus, 'publish_cancel', fake_publish_cancel)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 561}, 'chat': {'id': 561}}},
        )
        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: systemctl restart nginx', 'from': {'id': 561}, 'chat': {'id': 561}}},
        )
        approval_msgs = [m for m in sent_messages if m['reply_markup']]
        assert approval_msgs
        deny_callback_data = approval_msgs[0]['reply_markup']['inline_keyboard'][0][1]['callback_data']

        resp = client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-deny', 'data': deny_callback_data, 'from': {'id': 561}}},
        )
        assert resp.status_code == 200

    task = asyncio.run(_latest_task_for_user(561))
    assert task is not None
    assert task.status == TaskStatus.CANCELED
    assert 'denied by user' in (task.error or '').lower()
    assert any('cancel signal delivery failed' in m['text'].lower() for m in sent_messages)


def test_shell_approval_recheck_blocks_when_policy_tightens(monkeypatch):
    from services.coordinator.main import app, settings, telegram

    sent_messages = []
    callback_answers = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_answer_callback_query(callback_query_id, text):
        callback_answers.append(str(text))
        return None

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(telegram, 'answer_callback_query', fake_answer_callback_query)
    monkeypatch.setattr(settings, 'shell_allow_hard_block_override', True)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 601}, 'chat': {'id': 601}}},
        )
        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: rm -rf /', 'from': {'id': 601}, 'chat': {'id': 601}}},
        )

        approval_msgs = [m for m in sent_messages if m['reply_markup']]
        assert approval_msgs
        callback_data = approval_msgs[0]['reply_markup']['inline_keyboard'][0][0]['callback_data']

        monkeypatch.setattr(settings, 'shell_allow_hard_block_override', False)
        client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-blocked', 'data': callback_data, 'from': {'id': 601}}},
        )

    task = asyncio.run(_latest_task_for_user(601))
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert 'blocked by shell policy during approval' in (task.error or '').lower()
    assert any('blocked by safety policy' in m['text'].lower() for m in sent_messages)
    assert any('blocked by safety policy' in text.lower() for text in callback_answers)
    assert not any('approved and queued' in m['text'].lower() for m in sent_messages)


def test_shell_approval_block_persists_when_notification_send_fails(monkeypatch):
    from services.coordinator.main import app, settings, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        if 'blocked by safety policy' in str(text).lower():
            raise RuntimeError('telegram send failure')
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_answer_callback_query(callback_query_id, text):
        return None

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(telegram, 'answer_callback_query', fake_answer_callback_query)
    monkeypatch.setattr(settings, 'shell_allow_hard_block_override', True)

    invite_code = asyncio.run(_prepare_invite_code())

    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 602}, 'chat': {'id': 602}}},
        )
        client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell: rm -rf /', 'from': {'id': 602}, 'chat': {'id': 602}}},
        )

        approval_msgs = [m for m in sent_messages if m['reply_markup']]
        assert approval_msgs
        callback_data = approval_msgs[0]['reply_markup']['inline_keyboard'][0][0]['callback_data']

        monkeypatch.setattr(settings, 'shell_allow_hard_block_override', False)
        resp = client.post(
            '/telegram/webhook',
            json={'callback_query': {'id': 'cb-blocked-fail-send', 'data': callback_data, 'from': {'id': 602}}},
        )
        assert resp.status_code == 500

    task = asyncio.run(_latest_task_for_user(602))
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert 'blocked by shell policy during approval' in (task.error or '').lower()


def test_non_command_tool_response_appends_citations(monkeypatch):
    from services.coordinator.main import app, llm, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_chat_with_tools(**kwargs):
        return LLMToolChatResult(
            text='Here is what I found.',
            prompt_tokens=12,
            completion_tokens=8,
            tool_records=[
                ToolExecutionRecord(
                    name='web_search',
                    args={'query': 'latest ai news'},
                    result={
                        'ok': True,
                        'query': 'latest ai news',
                        'depth': 'balanced',
                        'results': [
                            {'title': 'Source A', 'url': 'https://a.example', 'snippet': 'a'},
                            {'title': 'Source B', 'url': 'https://b.example', 'snippet': 'b'},
                        ],
                    },
                )
            ],
        )

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(llm, 'chat_with_tools', fake_chat_with_tools)

    invite_code = asyncio.run(_prepare_invite_code())
    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 701}, 'chat': {'id': 701}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'what happened in ai today?', 'from': {'id': 701}, 'chat': {'id': 701}}},
        )
        assert resp.status_code == 200

    final_msg = sent_messages[-1]['text']
    assert 'sources:' in final_msg.lower()
    assert 'https://a.example' in final_msg
    assert 'https://b.example' in final_msg


def test_web_command_returns_sources(monkeypatch):
    from services.coordinator.main import app, telegram, web_search_client

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_search(query, *, depth='balanced', max_results=None):
        return {
            'query': query,
            'depth': depth,
            'results': [
                {'title': 'Source A', 'url': 'https://a.example', 'snippet': 'a'},
                {'title': 'Source B', 'url': 'https://b.example', 'snippet': 'b'},
            ],
        }

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(web_search_client, 'search', fake_search)

    invite_code = asyncio.run(_prepare_invite_code())
    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 801}, 'chat': {'id': 801}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'web: latest ai news', 'from': {'id': 801}, 'chat': {'id': 801}}},
        )
        assert resp.status_code == 200

    final_msg = sent_messages[-1]['text']
    assert 'top web results for:' in final_msg.lower()
    assert 'sources:' in final_msg.lower()
    assert 'https://a.example' in final_msg


def test_web_command_fail_open_notice(monkeypatch):
    from services.coordinator.main import app, llm, telegram, web_search_client

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_search(query, *, depth='balanced', max_results=None):
        raise WebSearchUnavailableError('Live web search is currently unavailable.')

    async def fake_chat(system_prompt, user_prompt, memory=None):
        return 'Fallback answer without live data.', 2, 2

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(web_search_client, 'search', fake_search)
    monkeypatch.setattr(llm, 'chat', fake_chat)

    invite_code = asyncio.run(_prepare_invite_code())
    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 901}, 'chat': {'id': 901}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'web: latest ai news', 'from': {'id': 901}, 'chat': {'id': 901}}},
        )
        assert resp.status_code == 200

    final_msg = sent_messages[-1]['text']
    assert 'live web search is currently unavailable' in final_msg.lower()
    assert 'fallback answer without live data' in final_msg.lower()


def test_web_command_disabled(monkeypatch):
    from services.coordinator.main import app, settings, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(settings, 'web_search_enabled', False)

    invite_code = asyncio.run(_prepare_invite_code())
    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 1001}, 'chat': {'id': 1001}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'web: latest ai news', 'from': {'id': 1001}, 'chat': {'id': 1001}}},
        )
        assert resp.status_code == 200

    assert any('web search is disabled' in m['text'].lower() for m in sent_messages)


def test_web_tool_not_exposed_when_disabled(monkeypatch):
    from services.coordinator.main import app, llm, settings, telegram

    sent_messages = []
    seen_tools = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    async def fake_chat_with_tools(**kwargs):
        seen_tools.append(kwargs.get('tools', []))
        return LLMToolChatResult(text='No tools used.', prompt_tokens=1, completion_tokens=1, tool_records=[])

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)
    monkeypatch.setattr(llm, 'chat_with_tools', fake_chat_with_tools)
    monkeypatch.setattr(settings, 'web_search_enabled', False)

    invite_code = asyncio.run(_prepare_invite_code())
    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 1101}, 'chat': {'id': 1101}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'what happened today?', 'from': {'id': 1101}, 'chat': {'id': 1101}}},
        )
        assert resp.status_code == 200

    assert seen_tools and seen_tools[0] == []
    assert any('no tools used' in m['text'].lower() for m in sent_messages)


def test_invalid_remote_shell_syntax_is_rejected(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent_messages.append({'chat_id': str(chat_id), 'text': text, 'reply_markup': reply_markup})

    monkeypatch.setattr(telegram, 'send_message', fake_send_message)

    invite_code = asyncio.run(_prepare_invite_code())
    with TestClient(app) as client:
        client.post(
            '/telegram/webhook',
            json={'message': {'text': f'/start {invite_code}', 'from': {'id': 1201}, 'chat': {'id': 1201}}},
        )
        resp = client.post(
            '/telegram/webhook',
            json={'message': {'text': 'shell@invalid', 'from': {'id': 1201}, 'chat': {'id': 1201}}},
        )
        assert resp.status_code == 200

    assert any('invalid remote shell syntax' in m['text'].lower() for m in sent_messages)
