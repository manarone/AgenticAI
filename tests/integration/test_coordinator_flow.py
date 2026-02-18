import asyncio

from fastapi.testclient import TestClient

from libs.common.db import AsyncSessionLocal
from libs.common.repositories import CoreRepository


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


def test_start_and_direct_response(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []

    async def fake_send_message(chat_id, text, reply_markup=None):
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

    async def fake_send_message(chat_id, text, reply_markup=None):
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

    async def fake_send_message(chat_id, text, reply_markup=None):
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

    async def fake_send_message(chat_id, text, reply_markup=None):
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


def test_approval_callback_replay_does_not_reissue_shell_grant(monkeypatch):
    from services.coordinator.main import app, telegram

    sent_messages = []
    callback_answers = []

    async def fake_send_message(chat_id, text, reply_markup=None):
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


def test_shell_approval_recheck_blocks_when_policy_tightens(monkeypatch):
    from libs.common.enums import TaskStatus
    from services.coordinator.main import app, settings, telegram

    sent_messages = []
    callback_answers = []

    async def fake_send_message(chat_id, text, reply_markup=None):
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
