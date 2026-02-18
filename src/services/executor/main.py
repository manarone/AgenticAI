from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI

from libs.common.audit import append_audit
from libs.common.config import get_settings
from libs.common.db import AsyncSessionLocal
from libs.common.enums import TaskStatus, TaskType
from libs.common.metrics import (
    REQUEST_COUNTER,
    SHELL_DENIED_NO_GRANT_COUNTER,
    TASK_COUNTER,
    metrics_response,
)
from libs.common.models import Base
from libs.common.repositories import CoreRepository
from libs.common.shell_policy import SHELL_MUTATION_SCOPE, ShellPolicyDecision, classify_shell_command
from libs.common.schemas import TaskResult
from libs.common.skill_store import SkillStore
from libs.common.state_machine import can_transition
from libs.common.task_bus import get_task_bus

settings = get_settings()
bus = get_task_bus()
skill_store = SkillStore()
WORK_DIR = Path(settings.shell_work_dir).expanduser()
WORK_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)
_REMOTE_HOST_RE = re.compile(r'^[A-Za-z0-9._:\-\[\]]+$')


class NonRetriableExecutionError(RuntimeError):
    """Raised for policy/configuration denials that retries cannot recover from."""


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
        queued_grant_id = str(task.payload.get('grant_id', '')).strip()
        has_queued_grant_proof = False
        if queued_grant_id:
            queued_grant = await repo.get_approval_grant(queued_grant_id)
            if (
                queued_grant is not None
                and queued_grant.tenant_id == task.tenant_id
                and queued_grant.user_id == task.user_id
                and queued_grant.scope == SHELL_MUTATION_SCOPE
                and queued_grant.revoked_at is None
                and task.created_at <= queued_grant.expires_at
            ):
                has_queued_grant_proof = True

        has_direct_approval = envelope.approval_id is not None
        if not has_grant and not has_direct_approval and not has_queued_grant_proof:
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
    raise RuntimeError(f'Unsupported task type: {task_type.value}')


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

        if task.status not in {TaskStatus.QUEUED, TaskStatus.DISPATCHING, TaskStatus.RUNNING}:
            await bus.ack_task(message_id)
            return

        if can_transition(task.status, TaskStatus.DISPATCHING):
            await repo.update_task_status(task.id, TaskStatus.DISPATCHING)

        attempts = await repo.increment_task_attempt(task.id)
        await repo.update_task_status(task.id, TaskStatus.RUNNING)
        await db.commit()

        try:
            output = await _execute_task(task, envelope, repo)
            await db.commit()
            await bus.publish_result(
                TaskResult(
                    task_id=envelope.task_id,
                    tenant_id=envelope.tenant_id,
                    user_id=envelope.user_id,
                    success=True,
                    output=output,
                    created_at=datetime.utcnow(),
                )
            )
            TASK_COUNTER.labels(status='success').inc()
        except NonRetriableExecutionError as exc:
            await repo.update_task_status(task.id, TaskStatus.FAILED, error=str(exc))
            await db.commit()
            await bus.publish_result(
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
        except Exception as exc:
            if attempts <= settings.max_executor_retries:
                await repo.update_task_status(task.id, TaskStatus.QUEUED, error=f'Retry {attempts}: {exc}')
                await db.commit()
                await bus.publish_task(envelope)
                TASK_COUNTER.labels(status='retry').inc()
            else:
                await repo.update_task_status(task.id, TaskStatus.FAILED, error=str(exc))
                await db.commit()
                await bus.publish_result(
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
    async with AsyncSessionLocal() as db:
        async with db.bind.begin() as conn:
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


@app.get('/')
async def root() -> dict:
    return {'service': 'executor', 'status': 'ready'}
