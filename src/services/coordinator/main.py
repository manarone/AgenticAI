from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.audit import append_audit
from libs.common.config import get_settings
from libs.common.db import AsyncSessionLocal, get_db
from libs.common.enums import ApprovalDecision, RiskTier, TaskStatus, TaskType
from libs.common.k8s import ExecutorJobLauncher
from libs.common.llm import LLMClient
from libs.common.memory import get_memory_backend
from libs.common.metrics import (
    REQUEST_COUNTER,
    REQUEST_LATENCY,
    SHELL_POLICY_ALLOW_COUNTER,
    SHELL_POLICY_APPROVAL_COUNTER,
    SHELL_POLICY_BLOCK_COUNTER,
    TOKEN_COUNTER,
    metrics_response,
)
from libs.common.models import Base
from libs.common.risk import classify_risk, requires_approval
from libs.common.shell_policy import SHELL_MUTATION_SCOPE, ShellPolicyDecision, classify_shell_command
from libs.common.schemas import TaskEnvelope
from libs.common.sanitizer import sanitize_input
from libs.common.state_machine import can_transition
from libs.common.task_bus import get_task_bus
from libs.common.telegram_client import TelegramClient
from libs.common.repositories import CoreRepository

settings = get_settings()
telegram = TelegramClient()
memory = get_memory_backend()
llm = LLMClient()
bus = get_task_bus()
job_launcher = ExecutorJobLauncher()
logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LEN = 3900


def _maybe_launch_executor_job(task_id: str) -> None:
    if not settings.launch_executor_job:
        return
    try:
        job_launcher.create_job(task_id)
    except Exception:
        # Executor deployment mode still processes stream messages, so do not fail request path.
        logger.exception('Failed to launch executor job for task %s', task_id)
        return


def _chunk_telegram_text(text: str, max_len: int = MAX_TELEGRAM_MESSAGE_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        split_at = remaining.rfind('\n', 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip('\n')
    return chunks


def _shell_approval_message(task_id: str, payload: dict, max_command_len: int = 320) -> str:
    command = str(payload.get('command', '')).strip().replace('\n', ' ')
    if not command:
        return f'Task {task_id[:8]} needs approval before running this command. Approve?'

    if len(command) > max_command_len:
        command = command[: max_command_len - 3].rstrip() + '...'

    remote_host = str(payload.get('remote_host', '')).strip()
    target = f' on {remote_host}' if remote_host else ''
    return (
        f'Task {task_id[:8]} needs approval before running this shell command{target}:\n{command}\n'
        f'Approving starts a {settings.shell_mutation_grant_ttl_minutes}-minute shell-mutation session.\n'
        'Approve?'
    )


async def _send_telegram_message(chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    if reply_markup is not None:
        await telegram.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        return

    for chunk in _chunk_telegram_text(text):
        await telegram.send_message(chat_id=chat_id, text=chunk)


@asynccontextmanager
async def _typing_indicator(chat_id: str):
    min_visible_seconds = 0.9
    stop_event = asyncio.Event()
    first_pulse_sent = asyncio.Event()
    started_at = asyncio.get_running_loop().time()

    async def _pulse() -> None:
        while not stop_event.is_set():
            try:
                await telegram.send_chat_action(chat_id=chat_id, action='typing')
            except Exception:
                logger.exception('Failed to send typing indicator chat_id=%s', chat_id)
            finally:
                if not first_pulse_sent.is_set():
                    first_pulse_sent.set()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            except TimeoutError:
                continue

    task = asyncio.create_task(_pulse())
    try:
        await asyncio.wait_for(first_pulse_sent.wait(), timeout=1.0)
    except TimeoutError:
        logger.warning('Typing indicator first pulse timed out chat_id=%s', chat_id)
    try:
        yield
    finally:
        elapsed = asyncio.get_running_loop().time() - started_at
        if elapsed < min_visible_seconds:
            await asyncio.sleep(min_visible_seconds - elapsed)
        stop_event.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _format_recent_conversation(messages, max_chars_per_message: int = 500) -> list[str]:
    formatted: list[str] = []
    for msg in messages:
        content = (msg.content or '').strip()
        if not content:
            continue
        if len(content) > max_chars_per_message:
            content = content[:max_chars_per_message] + '...'
        formatted.append(f'{msg.role}: {content}')
    return formatted


async def _consume_results_forever() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as db:
                repo = CoreRepository(db)
                messages = await bus.read_results(consumer_name='coordinator-results', count=10, block_ms=1000)
                for message_id, result in messages:
                    task = await repo.get_task(str(result.task_id))
                    if task is None:
                        await bus.ack_result(message_id)
                        continue

                    next_status = TaskStatus.SUCCEEDED if result.success else TaskStatus.FAILED
                    if task.status == next_status:
                        # Executor may have already set terminal status; still process result side effects.
                        updated = task
                        if task.result != result.output or task.error != result.error:
                            updated = await repo.update_task_status(
                                task_id=task.id,
                                status=next_status,
                                result=result.output,
                                error=result.error,
                            )
                    elif can_transition(task.status, next_status):
                        updated = await repo.update_task_status(
                            task_id=task.id,
                            status=next_status,
                            result=result.output,
                            error=result.error,
                        )
                    else:
                        await bus.ack_result(message_id)
                        continue

                    if updated:
                        await repo.add_message(task.tenant_id, task.user_id, task.conversation_id, 'assistant', result.output)
                        identity = await repo.get_identity_by_user_id(task.user_id)
                        if identity:
                            await _send_telegram_message(
                                chat_id=identity.telegram_user_id,
                                text=f'Task `{task.id}` {next_status.value.lower()}:\n{result.output}',
                            )
                        await append_audit(
                            db,
                            tenant_id=task.tenant_id,
                            user_id=task.user_id,
                            actor='executor',
                            action='task_result_processed',
                            details={'task_id': task.id, 'status': next_status.value, 'error': result.error},
                        )
                    await db.commit()
                    await bus.ack_result(message_id)
        except Exception:
            logger.exception('Result consumer loop failure')
            await asyncio.sleep(1.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as db:
        async with db.bind.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        repo = CoreRepository(db)
        await repo.get_or_create_default_tenant_user()
        await db.commit()

    result_task = asyncio.create_task(_consume_results_forever())
    app.state.result_task = result_task
    yield
    result_task.cancel()
    with suppress(asyncio.CancelledError):
        await result_task


app = FastAPI(title='agentai-coordinator', lifespan=lifespan)


@app.get('/healthz')
async def healthz() -> dict:
    REQUEST_COUNTER.labels(service='coordinator', endpoint='healthz').inc()
    return {'status': 'ok', 'service': 'coordinator'}


@app.get('/metrics')
async def metrics():
    return metrics_response()


def _parse_task(user_text: str) -> tuple[TaskType | None, dict]:
    lowered = user_text.lower().strip()

    if lowered.startswith('shell@'):
        stripped = user_text.strip()
        shell_target = stripped[len('shell@') :].strip()
        parsed = _split_remote_shell_target(shell_target)
        if parsed:
            remote_host, command = parsed
            return TaskType.SHELL, {'command': command, 'remote_host': remote_host}
        raise ValueError('Invalid remote shell target. Use `shell@host:command`.')

    if lowered.startswith('skill:'):
        _, _, rest = user_text.partition(':')
        skill_name, _, arg = rest.strip().partition(' ')
        return TaskType.SKILL, {'skill_name': skill_name.strip(), 'input': arg.strip()}

    if lowered.startswith('shell:'):
        _, _, command = user_text.partition(':')
        return TaskType.SHELL, {'command': command.strip()}

    if lowered.startswith('file:'):
        _, _, instruction = user_text.partition(':')
        return TaskType.FILE, {'instruction': instruction.strip()}

    return None, {}


def _split_remote_shell_target(shell_target: str) -> tuple[str, str] | None:
    target = shell_target.strip()
    if not target:
        return None

    # Bracketed IPv6 (optionally with :port) has an unambiguous command separator.
    bracketed = re.match(r'^(?P<host>\[[^\]]+\](?::\d+)?):(?P<command>.+)$', target)
    if bracketed:
        host = bracketed.group('host').strip()
        command = bracketed.group('command').strip()
        if host and command:
            return host, command

    first_space = next((index for index, ch in enumerate(target) if ch.isspace()), -1)
    if first_space > 0:
        sep = target.rfind(':', 0, first_space)
        if sep > 0:
            host = target[:sep].strip()
            command = target[sep + 1 :].strip()
            if host and command and all(not ch.isspace() for ch in host):
                return host, command

    host_port = re.match(r'^(?P<host>[^:\s]+):(?P<port>\d+):(?P<command>.+)$', target)
    if host_port:
        host = f"{host_port.group('host')}:{host_port.group('port')}"
        command = host_port.group('command').strip()
        if command:
            return host, command

    # For unbracketed IPv6 hosts, prefer the right-most split whose host parses as IPv6.
    for index in range(len(target) - 1, -1, -1):
        if target[index] != ':':
            continue
        host = target[:index].strip()
        command = target[index + 1 :].strip()
        if not host or not command:
            continue
        try:
            ipaddress.IPv6Address(host)
            return host, command
        except ValueError:
            continue

    host, sep, command = target.partition(':')
    host = host.strip()
    command = command.strip()
    if sep and host and command:
        return host, command
    return None


async def _handle_start_command(
    repo: CoreRepository,
    db: AsyncSession,
    chat_id: str,
    telegram_user_id: str,
    text: str,
) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        await _send_telegram_message(chat_id, 'Usage: /start <invite_code>')
        return
    code = parts[1].strip()
    ok, detail = await repo.redeem_invite_code(code, telegram_user_id)
    await db.commit()
    if ok:
        await _send_telegram_message(chat_id, 'Invite code accepted. You are now registered.')
    else:
        await _send_telegram_message(chat_id, f'Invite failed: {detail}')


async def _handle_status_command(repo: CoreRepository, identity, chat_id: str, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        task = await repo.get_task(parts[1].strip())
        if not task:
            await _send_telegram_message(chat_id, 'Task not found.')
            return
        await _send_telegram_message(chat_id, f'Task {task.id}: {task.status.value}')
        return

    tasks = await repo.list_user_tasks(identity.tenant_id, identity.user_id)
    if not tasks:
        await _send_telegram_message(chat_id, 'No tasks yet.')
        return

    lines = [f"{t.id[:8]} | {t.status.value} | {t.task_type}" for t in tasks[:10]]
    await _send_telegram_message(chat_id, 'Recent tasks:\n' + '\n'.join(lines))


async def _handle_cancel_command(repo: CoreRepository, db: AsyncSession, identity, chat_id: str, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip().lower() in {'grant', 'grants'}:
        revoked = await repo.revoke_approval_grants(identity.tenant_id, identity.user_id, scope=SHELL_MUTATION_SCOPE)
        await append_audit(
            db,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            actor='user',
            action='approval_grant_revoked',
            details={'scope': SHELL_MUTATION_SCOPE, 'count': revoked},
        )
        await db.commit()
        await _send_telegram_message(chat_id, f'Revoked {revoked} shell approval grant(s).')
        return

    if len(parts) == 1 or parts[1].strip().lower() == 'all':
        task_ids = await repo.cancel_user_tasks(identity.tenant_id, identity.user_id)
        for task_id in task_ids:
            await bus.publish_cancel(task_id)
        await db.commit()
        await _send_telegram_message(chat_id, f'Canceled {len(task_ids)} task(s).')
        return

    task_id = parts[1].strip()
    task = await repo.cancel_task(task_id)
    if task:
        await bus.publish_cancel(task_id)
        await db.commit()
        await _send_telegram_message(chat_id, f'Task {task_id} canceled.')
    else:
        await _send_telegram_message(chat_id, 'Task not found.')


async def _handle_user_message(repo: CoreRepository, db: AsyncSession, identity, chat_id: str, text: str) -> None:
    with REQUEST_LATENCY.labels(service='coordinator', endpoint='telegram_webhook').time():
        async with _typing_indicator(chat_id):
            convo = await repo.get_or_create_conversation(identity.tenant_id, identity.user_id)
            sanitized, flagged, patterns = sanitize_input(text)
            await repo.add_message(identity.tenant_id, identity.user_id, convo.id, 'user', text)
            await append_audit(
                db,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                actor='coordinator',
                action='message_received',
                details={'flagged': flagged, 'patterns': patterns},
            )

            if flagged:
                await db.commit()
                await _send_telegram_message(chat_id, 'Input blocked due to suspected prompt injection.')
                return

            try:
                await memory.remember(identity.tenant_id, identity.user_id, sanitized)
            except Exception as exc:
                await append_audit(
                    db,
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    actor='coordinator',
                    action='memory_remember_failed',
                    details={'error': str(exc)},
                )
            try:
                task_type, payload = _parse_task(sanitized)
            except ValueError as exc:
                await db.commit()
                await _send_telegram_message(chat_id, str(exc))
                return
            risk = classify_risk(sanitized)
            shell_requires_approval = False

            if task_type is None:
                try:
                    recalled = await memory.recall(identity.tenant_id, identity.user_id, query=sanitized)
                except Exception as exc:
                    recalled = []
                    await append_audit(
                        db,
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        actor='coordinator',
                        action='memory_recall_failed',
                        details={'error': str(exc)},
                    )

                try:
                    recent_messages = await repo.list_conversation_messages(convo.id, limit=30)
                except Exception as exc:
                    recent_messages = []
                    await append_audit(
                        db,
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        actor='coordinator',
                        action='conversation_context_failed',
                        details={'error': str(exc)},
                    )

                context_blocks: list[str] = []
                recent_context = _format_recent_conversation(recent_messages)
                if recent_context:
                    context_blocks.append('Recent conversation:\n' + '\n'.join(recent_context))
                if recalled:
                    context_blocks.append('Long-term memory:\n' + '\n'.join(recalled))

                response, input_tokens, output_tokens = await llm.chat(
                    system_prompt='You are the AgentAI coordinator. Keep responses concise and useful.',
                    user_prompt=sanitized,
                    memory=context_blocks,
                )
                await repo.add_message(identity.tenant_id, identity.user_id, convo.id, 'assistant', response)
                await repo.increment_token_usage(identity.tenant_id, settings.openai_model, input_tokens, output_tokens)
                TOKEN_COUNTER.labels(tenant_id=identity.tenant_id, model=settings.openai_model).inc(input_tokens + output_tokens)
                await db.commit()
                await _send_telegram_message(chat_id, response)
                return

            if task_type == TaskType.SHELL:
                command = payload.get('command', '').strip()
                command_hash = hashlib.sha256(command.encode('utf-8')).hexdigest()[:16] if command else ''
                shell_policy = classify_shell_command(
                    command,
                    mode=settings.shell_policy_mode,
                    allow_hard_block_override=settings.shell_allow_hard_block_override,
                )

                await append_audit(
                    db,
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    actor='coordinator',
                    action='command_classification_decision',
                    details={
                        'task_type': 'shell',
                        'decision': shell_policy.decision.value,
                        'reason': shell_policy.reason,
                        'command_hash': command_hash,
                    },
                )

                if shell_policy.decision == ShellPolicyDecision.ALLOW_AUTORUN:
                    SHELL_POLICY_ALLOW_COUNTER.inc()
                elif shell_policy.decision == ShellPolicyDecision.REQUIRE_APPROVAL:
                    SHELL_POLICY_APPROVAL_COUNTER.inc()
                else:
                    SHELL_POLICY_BLOCK_COUNTER.inc()

                if shell_policy.decision == ShellPolicyDecision.BLOCKED:
                    await append_audit(
                        db,
                        tenant_id=identity.tenant_id,
                        user_id=identity.user_id,
                        actor='coordinator',
                        action='execution_blocked_by_policy',
                        details={'task_type': 'shell', 'reason': shell_policy.reason, 'command_hash': command_hash},
                    )
                    await db.commit()
                    await _send_telegram_message(chat_id, f'Shell command blocked by safety policy ({shell_policy.reason}).')
                    return

                if shell_policy.decision == ShellPolicyDecision.REQUIRE_APPROVAL:
                    active_grant = await repo.get_active_approval_grant(
                        identity.tenant_id, identity.user_id, scope=SHELL_MUTATION_SCOPE
                    )
                    shell_requires_approval = active_grant is None
                    if active_grant is not None:
                        payload['grant_id'] = active_grant.id
                        await append_audit(
                            db,
                            tenant_id=identity.tenant_id,
                            user_id=identity.user_id,
                            actor='coordinator',
                            action='approval_grant_reused',
                            details={
                                'scope': SHELL_MUTATION_SCOPE,
                                'grant_id': active_grant.id,
                                'command_hash': command_hash,
                            },
                        )

            if task_type == TaskType.SHELL:
                status = TaskStatus.WAITING_APPROVAL if shell_requires_approval else TaskStatus.QUEUED
            else:
                status = TaskStatus.WAITING_APPROVAL if requires_approval(sanitized) else TaskStatus.QUEUED
            task = await repo.create_task(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                conversation_id=convo.id,
                task_type=task_type.value,
                risk_tier=risk.value,
                payload=payload,
                status=status,
            )

            approval_id: str | None = None
            if status == TaskStatus.WAITING_APPROVAL:
                approval = await repo.create_approval(task.id, identity.tenant_id, identity.user_id)
                approval_id = approval.id
                buttons = {
                    'inline_keyboard': [
                        [
                            {'text': 'Approve', 'callback_data': f'approve:{approval.id}'},
                            {'text': 'Deny', 'callback_data': f'deny:{approval.id}'},
                        ]
                    ]
                }
                approval_text = (
                    _shell_approval_message(task.id, payload)
                    if task_type == TaskType.SHELL
                    else f'Task {task.id[:8]} needs approval before running this command. Approve?'
                )
                await _send_telegram_message(
                    chat_id,
                    approval_text,
                    reply_markup=buttons,
                )
                await db.commit()
                return

            envelope = TaskEnvelope(
                task_id=UUID(task.id),
                tenant_id=UUID(identity.tenant_id),
                user_id=UUID(identity.user_id),
                task_type=task_type,
                payload=payload,
                risk_tier=risk,
                approval_id=UUID(approval_id) if approval_id else None,
                created_at=datetime.utcnow(),
            )
            await db.commit()
            await bus.publish_task(envelope)
            _maybe_launch_executor_job(task.id)

            await append_audit(
                db,
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                actor='coordinator',
                action='task_enqueued',
                details={'task_id': task.id, 'task_type': task_type.value, 'risk_tier': risk.value},
            )
            await db.commit()
            await _send_telegram_message(chat_id, f'Task queued: {task.id}')


async def _queue_task_after_approval(repo: CoreRepository, db: AsyncSession, task, approval_id: str, chat_id: str) -> None:
    await repo.update_task_status(task.id, TaskStatus.QUEUED)
    await db.commit()
    try:
        risk_tier = RiskTier(task.risk_tier)
    except ValueError:
        risk_tier = classify_risk(str(task.payload))
    envelope = TaskEnvelope(
        task_id=UUID(task.id),
        tenant_id=UUID(task.tenant_id),
        user_id=UUID(task.user_id),
        task_type=TaskType(task.task_type),
        payload=task.payload,
        risk_tier=risk_tier,
        approval_id=UUID(approval_id),
        created_at=datetime.utcnow(),
    )
    await bus.publish_task(envelope)
    _maybe_launch_executor_job(task.id)
    await append_audit(
        db,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        actor='coordinator',
        action='task_enqueued',
        details={'task_id': task.id, 'task_type': task.task_type, 'risk_tier': risk_tier.value},
    )
    await db.commit()
    await _send_telegram_message(chat_id, f'Task {task.id[:8]} approved and queued.')


@app.post('/telegram/webhook')
async def telegram_webhook(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    REQUEST_COUNTER.labels(service='coordinator', endpoint='telegram_webhook').inc()
    repo = CoreRepository(db)

    callback_query = payload.get('callback_query')
    if callback_query:
        data = callback_query.get('data', '')
        callback_query_id = callback_query.get('id', '')
        chat_id = str(callback_query.get('from', {}).get('id', ''))
        actor_tg_id = str(callback_query.get('from', {}).get('id', ''))
        action, _, approval_id = data.partition(':')

        identity = await repo.get_identity(actor_tg_id)
        approval = await repo.get_approval(approval_id) if approval_id else None
        if not identity or not approval or identity.user_id != approval.user_id:
            await telegram.answer_callback_query(callback_query_id, 'Approval not found or unauthorized.')
            return {'ok': True}

        decision = ApprovalDecision.APPROVED if action == 'approve' else ApprovalDecision.DENIED
        approval = await repo.set_approval_decision(approval.id, decision)
        if approval is None:
            await telegram.answer_callback_query(callback_query_id, 'Approval already processed.')
            return {'ok': True}
        task = await repo.get_task(approval.task_id)

        if not task:
            await db.commit()
            await telegram.answer_callback_query(callback_query_id, 'Task not found')
            return {'ok': True}

        callback_text = decision.value

        if decision == ApprovalDecision.DENIED:
            await repo.update_task_status(task.id, TaskStatus.CANCELED, error='Denied by user')
            await bus.publish_cancel(task.id)
            await _send_telegram_message(chat_id, f'Task {task.id[:8]} denied and canceled.')
        else:
            if task.task_type == TaskType.SHELL.value:
                command = str(task.payload.get('command', '')).strip()
                command_hash = hashlib.sha256(command.encode('utf-8')).hexdigest()[:16] if command else ''
                shell_policy = classify_shell_command(
                    command,
                    mode=settings.shell_policy_mode,
                    allow_hard_block_override=settings.shell_allow_hard_block_override,
                )
                if shell_policy.decision == ShellPolicyDecision.BLOCKED:
                    callback_text = 'Blocked by safety policy'
                    await repo.update_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error=f'Blocked by shell policy during approval ({shell_policy.reason})',
                    )
                    await append_audit(
                        db,
                        tenant_id=task.tenant_id,
                        user_id=task.user_id,
                        actor='coordinator',
                        action='execution_blocked_by_policy',
                        details={
                            'task_id': task.id,
                            'task_type': 'shell',
                            'reason': shell_policy.reason,
                            'command_hash': command_hash,
                        },
                    )
                    await _send_telegram_message(
                        chat_id,
                        f'Task {task.id[:8]} blocked by safety policy ({shell_policy.reason}).',
                    )
                else:
                    if shell_policy.decision == ShellPolicyDecision.REQUIRE_APPROVAL:
                        grant, refreshed = await repo.issue_approval_grant(
                            tenant_id=task.tenant_id,
                            user_id=task.user_id,
                            scope=SHELL_MUTATION_SCOPE,
                            ttl_minutes=settings.shell_mutation_grant_ttl_minutes,
                        )
                        await append_audit(
                            db,
                            tenant_id=task.tenant_id,
                            user_id=task.user_id,
                            actor='coordinator',
                            action='approval_grant_refreshed' if refreshed else 'approval_grant_issued',
                            details={
                                'scope': SHELL_MUTATION_SCOPE,
                                'grant_id': grant.id,
                                'expires_at': grant.expires_at.isoformat(),
                                'command_hash': command_hash,
                            },
                        )

                    await _queue_task_after_approval(repo, db, task, approval.id, chat_id)
            else:
                await _queue_task_after_approval(repo, db, task, approval.id, chat_id)

        await append_audit(
            db,
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            actor='user',
            action='approval_decision',
            details={'approval_id': approval.id, 'decision': decision.value, 'task_id': task.id},
        )
        await db.commit()
        await telegram.answer_callback_query(callback_query_id, callback_text)
        return {'ok': True}

    message = payload.get('message', {})
    text = str(message.get('text', '')).strip()
    telegram_user_id = str(message.get('from', {}).get('id', ''))
    chat_id = str(message.get('chat', {}).get('id', telegram_user_id))

    if not text:
        return {'ok': True}

    if text.startswith('/start'):
        await _handle_start_command(repo, db, chat_id, telegram_user_id, text)
        return {'ok': True}

    identity = await repo.get_identity(telegram_user_id)
    if identity is None:
        await _send_telegram_message(chat_id, 'This bot is private. Use /start <invite_code> first.')
        return {'ok': True}

    if text.startswith('/status'):
        await _handle_status_command(repo, identity, chat_id, text)
        await db.commit()
        return {'ok': True}

    if text.startswith('/cancel'):
        await _handle_cancel_command(repo, db, identity, chat_id, text)
        return {'ok': True}

    await _handle_user_message(repo, db, identity, chat_id, text)
    return {'ok': True}


@app.get('/')
async def root() -> dict:
    return {'service': 'coordinator', 'status': 'ready'}
