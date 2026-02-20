from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from libs.common.audit import append_audit
from libs.common.browser_policy import BrowserActionClass, classify_browser_action, normalize_browser_action
from libs.common.browser_runner import run_browser_action
from libs.common.config import get_settings
from libs.common.db import AsyncSessionLocal, engine as db_engine
from libs.common.enums import TaskStatus, TaskType
from libs.common.metrics import (
    REQUEST_COUNTER,
    SHELL_DENIED_NO_GRANT_COUNTER,
    TASK_COUNTER,
    metrics_response,
)
from libs.common.models import Base
from libs.common.repositories import CoreRepository
from libs.common.shell_policy import ShellPolicyDecision, classify_shell_command
from libs.common.schemas import TaskResult
from libs.common.skill_store import SkillStore
from libs.common.state_machine import can_transition
from libs.common.task_bus import get_task_bus
from libs.common.telegram_client import TelegramClient

settings = get_settings()
bus = get_task_bus()
skill_store = SkillStore()
telegram = TelegramClient()
WORK_DIR = Path(settings.shell_work_dir).expanduser()
WORK_DIR.mkdir(parents=True, exist_ok=True)
SHELL_MUTATION_SCOPE = 'shell_mutation'
BROWSER_MUTATION_SCOPE = 'browser_mutation'
logger = logging.getLogger(__name__)
_REMOTE_HOST_RE = re.compile(r'^[A-Za-z0-9._:\-\[\]]+$')


class NonRetriableExecutionError(RuntimeError):
    """Raised for policy/configuration denials that retries cannot recover from."""


class BrowserActionRequest(BaseModel):
    tenant_id: str
    user_id: str
    conversation_id: str | None = None
    chat_id: str | None = None
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    task_id: str | None = None


def _command_hash(command: str) -> str:
    return hashlib.sha256(command.encode('utf-8')).hexdigest()[:16] if command else ''


def _shell_env() -> dict[str, str]:
    allowed = [name.strip() for name in settings.shell_env_allowlist.split(',') if name.strip()]
    env: dict[str, str] = {}
    for name in allowed:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def _validate_remote_host(remote_host: str) -> None:
    if not remote_host:
        raise NonRetriableExecutionError('Missing remote host.')
    if remote_host.startswith('-') or not _REMOTE_HOST_RE.fullmatch(remote_host):
        raise NonRetriableExecutionError('Invalid remote host.')


def _validate_internal_auth(authorization: str | None) -> None:
    expected = settings.executor_internal_token.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Internal executor token is not configured.',
        )
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token.')
    provided = authorization[len('Bearer ') :].strip()
    if not secrets.compare_digest(expected, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid executor token.')


def _browser_summary(action: str, result: dict[str, Any]) -> str:
    summary = str(result.get('summary', '')).strip()
    if summary:
        return summary
    return f'Browser action `{normalize_browser_action(action)}` completed.'


async def _send_browser_artifacts(chat_id: str, action: str, artifacts: list[dict[str, Any]], *, task_id: str | None) -> int:
    sent = 0
    caption_prefix = f'Browser {normalize_browser_action(action)}'
    if task_id:
        caption_prefix += f' (task {task_id[:8]})'

    for artifact in artifacts:
        path_text = str(artifact.get('path', '')).strip()
        if not path_text:
            continue
        path = Path(path_text).expanduser().resolve()
        if not path.exists():
            continue

        caption = f'{caption_prefix}: {path.name}'
        suffix = path.suffix.lower()
        try:
            if suffix in {'.png', '.jpg', '.jpeg', '.webp'}:
                await telegram.send_photo(chat_id=chat_id, photo_path=str(path), caption=caption)
            else:
                await telegram.send_document(chat_id=chat_id, document_path=str(path), caption=caption)
            sent += 1
            with suppress(OSError):
                path.unlink()
        except Exception:
            logger.exception('Failed to send browser artifact %s; file retained on disk.', path)
    return sent


async def _run_browser(
    repo: CoreRepository,
    task,
    envelope,
) -> str:
    action = normalize_browser_action(str(task.payload.get('action', '')).strip())
    args = task.payload.get('args') if isinstance(task.payload.get('args'), dict) else {}
    session_id = str(task.payload.get('session_id', '')).strip() or None
    chat_id = str(task.payload.get('chat_id', '')).strip() or None
    action_class = classify_browser_action(action)

    if action_class == BrowserActionClass.UNSUPPORTED:
        raise NonRetriableExecutionError(f'Unsupported browser action: {action}')

    if action_class == BrowserActionClass.MUTATING:
        if not settings.browser_mutation_enabled:
            raise NonRetriableExecutionError('Mutating browser actions are disabled by policy.')
        has_grant = await repo.has_active_approval_grant(task.tenant_id, task.user_id, scope=BROWSER_MUTATION_SCOPE)
        has_direct_approval = envelope.approval_id is not None
        if not has_grant and not has_direct_approval:
            raise NonRetriableExecutionError('Browser action requires approval.')

    await append_audit(
        repo.db,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        actor='executor',
        action='browser_action_started',
        details={'task_id': task.id, 'action': action, 'session_id': session_id},
    )

    result = await run_browser_action(action=action, args=args, session_id=session_id)
    if not result.get('ok', False):
        error = str(result.get('error', '')).strip() or 'Browser action failed.'
        await append_audit(
            repo.db,
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            actor='executor',
            action='browser_action_failed',
            details={'task_id': task.id, 'action': action, 'error': error},
        )
        raise RuntimeError(error)

    sent = 0
    artifacts = result.get('artifacts')
    if chat_id and isinstance(artifacts, list):
        sent = await _send_browser_artifacts(chat_id, action, artifacts, task_id=task.id)

    summary = _browser_summary(action, result)
    if sent:
        summary = f'{summary}\nSent {sent} browser artifact(s) to Telegram.'
    await append_audit(
        repo.db,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        actor='executor',
        action='browser_action_completed',
        details={'task_id': task.id, 'action': action, 'artifacts_sent': sent},
    )
    return summary


async def _run_local_shell(command: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORK_DIR),
        env=_shell_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=settings.shell_timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        with suppress(ProcessLookupError):
            await proc.communicate()
        raise RuntimeError('Command timed out') from exc

    if proc.returncode != 0:
        raise RuntimeError(err.decode('utf-8', errors='ignore')[: settings.shell_max_output_chars])
    return out.decode('utf-8', errors='ignore')[: settings.shell_max_output_chars]


async def _run_remote_shell(remote_host: str, command: str) -> str:
    _validate_remote_host(remote_host)
    proc = await asyncio.create_subprocess_exec(
        'ssh',
        '-o',
        'BatchMode=yes',
        '--',
        remote_host,
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_shell_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=settings.shell_timeout_seconds)
    except TimeoutError as exc:
        proc.kill()
        with suppress(ProcessLookupError):
            await proc.communicate()
        raise RuntimeError('Remote command timed out') from exc

    if proc.returncode != 0:
        raise RuntimeError(err.decode('utf-8', errors='ignore')[: settings.shell_max_output_chars])
    return out.decode('utf-8', errors='ignore')[: settings.shell_max_output_chars]


async def _run_shell(repo: CoreRepository, task, envelope) -> str:
    command = str(task.payload.get('command', '')).strip()
    remote_host = str(task.payload.get('remote_host', '')).strip() or None
    shell_policy = classify_shell_command(
        command,
        mode=settings.shell_policy_mode,
        allow_hard_block_override=settings.shell_allow_hard_block_override,
    )
    command_hash = _command_hash(command)

    await append_audit(
        repo.db,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        actor='executor',
        action='command_classification_decision',
        details={
            'task_id': task.id,
            'decision': shell_policy.decision.value,
            'reason': shell_policy.reason,
            'command_hash': command_hash,
            'remote_host': remote_host,
        },
    )

    if shell_policy.decision == ShellPolicyDecision.BLOCKED:
        await append_audit(
            repo.db,
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            actor='executor',
            action='execution_blocked_by_policy',
            details={'task_id': task.id, 'reason': shell_policy.reason, 'command_hash': command_hash},
        )
        raise NonRetriableExecutionError('Command blocked by shell policy.')

    if shell_policy.decision == ShellPolicyDecision.REQUIRE_APPROVAL:
        has_grant = await repo.has_active_approval_grant(task.tenant_id, task.user_id, scope=SHELL_MUTATION_SCOPE)
        has_direct_approval = envelope.approval_id is not None
        if not has_grant and not has_direct_approval:
            SHELL_DENIED_NO_GRANT_COUNTER.inc()
            await append_audit(
                repo.db,
                tenant_id=task.tenant_id,
                user_id=task.user_id,
                actor='executor',
                action='execution_blocked_by_policy',
                details={
                    'task_id': task.id,
                    'reason': 'missing_shell_approval_grant',
                    'command_hash': command_hash,
                },
            )
            raise NonRetriableExecutionError('Command requires approval.')

    if remote_host and not settings.shell_remote_enabled:
        await append_audit(
            repo.db,
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            actor='executor',
            action='execution_blocked_by_policy',
            details={
                'task_id': task.id,
                'reason': 'remote_shell_disabled',
                'command_hash': command_hash,
                'remote_host': remote_host,
            },
        )
        raise NonRetriableExecutionError('Remote shell execution is disabled by policy.')

    await append_audit(
        repo.db,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        actor='executor',
        action='execution_started',
        details={'task_id': task.id, 'task_type': 'shell', 'command_hash': command_hash, 'remote_host': remote_host},
    )

    if remote_host:
        output = await _run_remote_shell(remote_host, command)
    else:
        output = await _run_local_shell(command)

    await append_audit(
        repo.db,
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        actor='executor',
        action='execution_completed',
        details={
            'task_id': task.id,
            'task_type': 'shell',
            'command_hash': command_hash,
            'remote_host': remote_host,
            'output_chars': len(output),
        },
    )
    return output


async def _run_file(instruction: str) -> str:
    if instruction.startswith('write '):
        _, _, rest = instruction.partition('write ')
        path_text, _, content = rest.partition('::')
        path = (WORK_DIR / path_text.strip()).resolve()
        if not str(path).startswith(str(WORK_DIR.resolve())):
            raise RuntimeError('Path denied.')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f'Wrote {len(content)} bytes to {path.name}'

    if instruction.startswith('read '):
        _, _, path_text = instruction.partition('read ')
        path = (WORK_DIR / path_text.strip()).resolve()
        if not str(path).startswith(str(WORK_DIR.resolve())):
            raise RuntimeError('Path denied.')
        return path.read_text()[:4000]

    raise RuntimeError('Unknown file instruction. Use `write path::content` or `read path`.')


async def _run_skill(tenant_id: str, skill_name: str, skill_input: str) -> str:
    manifest, body = skill_store.load_skill(tenant_id, skill_name)
    # MVP behavior: treat markdown content as instruction template and echo structured execution summary.
    snippet = body.strip().splitlines()[0] if body.strip() else 'No body'
    return (
        f"Skill `{manifest.name}` v{manifest.version} executed.\n"
        f"Risk: {manifest.risk_tier.value}\n"
        f"Input: {skill_input}\n"
        f"Instruction head: {snippet}"
    )


async def _execute_task(task, envelope, repo: CoreRepository) -> str:
    task_type = TaskType(task.task_type)
    if task_type == TaskType.SHELL:
        return await _run_shell(repo, task, envelope)
    if task_type == TaskType.FILE:
        return await _run_file(task.payload.get('instruction', '').strip())
    if task_type == TaskType.SKILL:
        return await _run_skill(
            tenant_id=task.tenant_id,
            skill_name=task.payload.get('skill_name', '').strip(),
            skill_input=task.payload.get('input', '').strip(),
        )
    if task_type == TaskType.BROWSER:
        if not settings.browser_enabled:
            raise NonRetriableExecutionError('Browser automation is disabled by configuration.')
        return await _run_browser(repo, task, envelope)
    if task_type == TaskType.WEB:
        raise NonRetriableExecutionError('Web tasks are handled inline by coordinator and should not reach executor.')
    raise NonRetriableExecutionError(f'Unsupported task type: {task_type.value}')


async def _set_task_status_if_legal(
    repo: CoreRepository,
    *,
    task_id: str,
    next_status: TaskStatus,
    result: str | None = None,
    error: str | None = None,
):
    current = await repo.get_task(task_id)
    if current is None:
        return False, None
    if current.status != next_status and not can_transition(current.status, next_status):
        logger.warning(
            'Blocked illegal task transition task_id=%s current=%s next=%s',
            task_id,
            current.status.value,
            next_status.value,
        )
        return False, current

    updated = await repo.update_task_status(task_id, next_status, result=result, error=error)
    return updated is not None, updated or current


async def _publish_result_safe(result: TaskResult) -> tuple[bool, str | None]:
    try:
        await bus.publish_result(result)
        return True, None
    except Exception as exc:
        logger.exception('Failed to publish task result task_id=%s', result.task_id)
        return False, str(exc)


async def _process_task_once(message_id: str, envelope) -> None:
    async with AsyncSessionLocal() as db:
        repo = CoreRepository(db)
        task = await repo.get_task(str(envelope.task_id))
        if not task:
            await bus.ack_task(message_id)
            return

        if task.status == TaskStatus.CANCELED:
            await bus.ack_task(message_id)
            return

        if task.status not in {TaskStatus.QUEUED, TaskStatus.DISPATCHING}:
            await bus.ack_task(message_id)
            return

        if task.status == TaskStatus.QUEUED:
            transitioned, updated = await _set_task_status_if_legal(
                repo,
                task_id=task.id,
                next_status=TaskStatus.DISPATCHING,
            )
            if not transitioned or updated is None:
                await db.commit()
                await bus.ack_task(message_id)
                return
            task = updated

        transitioned, updated = await _set_task_status_if_legal(
            repo,
            task_id=task.id,
            next_status=TaskStatus.RUNNING,
        )
        if not transitioned or updated is None:
            await db.commit()
            await bus.ack_task(message_id)
            return
        task = updated

        attempts = await repo.increment_task_attempt(task.id)
        await db.commit()

        try:
            output = await _execute_task(task, envelope, repo)
        except NonRetriableExecutionError as exc:
            transitioned, _ = await _set_task_status_if_legal(
                repo,
                task_id=task.id,
                next_status=TaskStatus.FAILED,
                error=str(exc),
            )
            await db.commit()
            if transitioned:
                await _publish_result_safe(
                    TaskResult(
                        task_id=envelope.task_id,
                        tenant_id=envelope.tenant_id,
                        user_id=envelope.user_id,
                        success=False,
                        output='Task failed',
                        error=str(exc),
                        created_at=datetime.utcnow(),
                    )
                )
                TASK_COUNTER.labels(status='failed').inc()
            await bus.ack_task(message_id)
            return
        except Exception as exc:
            should_retry = attempts <= settings.max_executor_retries
            if should_retry:
                transitioned, _ = await _set_task_status_if_legal(
                    repo,
                    task_id=task.id,
                    next_status=TaskStatus.QUEUED,
                    error=f'Retry {attempts}: {exc}',
                )
                await db.commit()
                if transitioned:
                    try:
                        await bus.publish_task(envelope)
                        TASK_COUNTER.labels(status='retry').inc()
                        await bus.ack_task(message_id)
                        return
                    except Exception as requeue_exc:
                        failure_error = f'Retry enqueue failed: {requeue_exc}'
                        transitioned, _ = await _set_task_status_if_legal(
                            repo,
                            task_id=task.id,
                            next_status=TaskStatus.FAILED,
                            error=failure_error,
                        )
                        await db.commit()
                        if transitioned:
                            await _publish_result_safe(
                                TaskResult(
                                    task_id=envelope.task_id,
                                    tenant_id=envelope.tenant_id,
                                    user_id=envelope.user_id,
                                    success=False,
                                    output='Task failed',
                                    error=failure_error,
                                    created_at=datetime.utcnow(),
                                )
                            )
                            TASK_COUNTER.labels(status='failed').inc()
                        await bus.ack_task(message_id)
                        return

            transitioned, _ = await _set_task_status_if_legal(
                repo,
                task_id=task.id,
                next_status=TaskStatus.FAILED,
                error=str(exc),
            )
            await db.commit()
            if transitioned:
                await _publish_result_safe(
                    TaskResult(
                        task_id=envelope.task_id,
                        tenant_id=envelope.tenant_id,
                        user_id=envelope.user_id,
                        success=False,
                        output='Task failed',
                        error=str(exc),
                        created_at=datetime.utcnow(),
                    )
                )
                TASK_COUNTER.labels(status='failed').inc()
            await bus.ack_task(message_id)
            return

        published, publish_error = await _publish_result_safe(
            TaskResult(
                task_id=envelope.task_id,
                tenant_id=envelope.tenant_id,
                user_id=envelope.user_id,
                success=True,
                output=output,
                created_at=datetime.utcnow(),
            )
        )
        if published:
            TASK_COUNTER.labels(status='success').inc()
        else:
            failure_error = f'Failed to publish task result ({publish_error})'
            transitioned, _ = await _set_task_status_if_legal(
                repo,
                task_id=task.id,
                next_status=TaskStatus.FAILED,
                error=failure_error,
            )
            await db.commit()
            if transitioned:
                await _publish_result_safe(
                    TaskResult(
                        task_id=envelope.task_id,
                        tenant_id=envelope.tenant_id,
                        user_id=envelope.user_id,
                        success=False,
                        output='Task failed',
                        error=failure_error,
                        created_at=datetime.utcnow(),
                    )
                )
                TASK_COUNTER.labels(status='failed').inc()

        await db.commit()
        await bus.ack_task(message_id)


async def _worker_forever() -> None:
    while True:
        try:
            messages = await bus.read_tasks(consumer_name='executor-worker', count=10, block_ms=1000)
            for message_id, envelope in messages:
                await _process_task_once(message_id, envelope)
        except Exception:
            logger.exception('Executor worker loop failure')
            await asyncio.sleep(1.0)


async def _run_single_task_if_set() -> None:
    # Optional hook for Kubernetes Job style launch where pod runs once for a specific task.
    target_task_id = os.getenv('EXECUTOR_ONCE_TASK_ID', '').strip()
    if not target_task_id:
        return

    while True:
        messages = await bus.read_tasks(consumer_name=f'executor-once-{target_task_id[:8]}', count=10, block_ms=500)
        found = False
        for message_id, envelope in messages:
            if str(envelope.task_id) != target_task_id:
                continue
            found = True
            await _process_task_once(message_id, envelope)
        if found:
            break


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.browser_enabled and not settings.executor_internal_token.strip():
        logger.warning('BROWSER_ENABLED=true but EXECUTOR_INTERNAL_TOKEN is empty; internal browser RPC will reject requests.')

    async with AsyncSessionLocal() as db:
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    if os.getenv('EXECUTOR_ONCE_TASK_ID', '').strip():
        once = asyncio.create_task(_run_single_task_if_set())
        app.state.worker = once
    else:
        worker = asyncio.create_task(_worker_forever())
        app.state.worker = worker

    yield

    app.state.worker.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.worker


app = FastAPI(title='agentai-executor', lifespan=lifespan)


@app.get('/healthz')
async def healthz() -> dict:
    REQUEST_COUNTER.labels(service='executor', endpoint='healthz').inc()
    return {'status': 'ok', 'service': 'executor'}


@app.get('/metrics')
async def metrics():
    return metrics_response()


@app.post('/internal/browser/action')
async def internal_browser_action(
    request: BrowserActionRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    REQUEST_COUNTER.labels(service='executor', endpoint='internal_browser_action').inc()
    _validate_internal_auth(authorization)

    if not settings.browser_enabled:
        return {
            'ok': False,
            'error': 'browser_disabled',
            'user_notice': 'Browser automation is disabled by configuration.',
        }

    action = normalize_browser_action(request.action)
    action_class = classify_browser_action(action)
    if action_class == BrowserActionClass.UNSUPPORTED:
        return {'ok': False, 'error': f'Unsupported browser action: {action}'}
    if action_class == BrowserActionClass.MUTATING:
        return {
            'ok': False,
            'error': 'mutating_browser_actions_must_be_queued',
            'user_notice': 'Mutating browser actions must be queued for approval.',
        }

    async with AsyncSessionLocal() as db:
        await append_audit(
            db,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            actor='coordinator',
            action='browser_sync_invoked',
            details={'action': action, 'session_id': request.session_id},
        )
        await db.commit()

    result = await run_browser_action(action=action, args=request.args, session_id=request.session_id)
    if not result.get('ok', False):
        notice = str(result.get('error', '')).strip() or 'Browser action failed.'
        return {'ok': False, 'error': notice, 'user_notice': notice}

    sent = 0
    artifacts = result.get('artifacts')
    if request.chat_id and isinstance(artifacts, list):
        sent = await _send_browser_artifacts(
            chat_id=request.chat_id,
            action=action,
            artifacts=artifacts,
            task_id=request.task_id,
        )

    summary = _browser_summary(action, result)
    if sent:
        summary = f'{summary}\nSent {sent} browser artifact(s) to Telegram.'
    return {
        **result,
        'summary': summary,
        'artifacts_sent': sent,
        'mode': 'sync',
    }


@app.get('/')
async def root() -> dict:
    return {'service': 'executor', 'status': 'ready'}
