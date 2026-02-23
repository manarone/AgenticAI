"""Background coordinator loop for queued task execution."""

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agenticai.bus.base import TASK_QUEUE, EventBus, QueuedMessage
from agenticai.bus.exceptions import BUS_EXCEPTIONS
from agenticai.coordinator.risk import RiskAssessment, classify_task_risk
from agenticai.core.observability import log_event
from agenticai.db.audit import add_audit_event
from agenticai.db.models import Approval, ApprovalDecision, BypassMode, Task, TaskStatus
from agenticai.db.policy import bypass_allows_risk, resolve_effective_bypass_mode

logger = logging.getLogger(__name__)
WORKER_EXCEPTIONS = BUS_EXCEPTIONS


@dataclass(frozen=True)
class PlannerExecutorHandoff:
    """Minimal handoff envelope from coordinator to planner/executor."""

    task_id: str
    org_id: str
    requested_by_user_id: str
    prompt: str | None
    approved_resume: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    """Adapter execution result consumed by the coordinator lifecycle logic."""

    success: bool
    error_message: str | None = None


class PlannerExecutorAdapter(Protocol):
    """Adapter contract for planner/executor invocation."""

    def execute(
        self,
        handoff: PlannerExecutorHandoff,
    ) -> ExecutionResult | Awaitable[ExecutionResult]:
        """Execute one handoff and return a success/failure result."""


class NoOpPlannerExecutorAdapter:
    """Default adapter that marks tasks as successful."""

    def execute(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
        """Return a successful result without external execution."""
        _ = handoff
        return ExecutionResult(success=True)


class CoordinatorWorker:
    """Non-blocking worker that orchestrates queued task lifecycle transitions."""

    def __init__(
        self,
        *,
        bus: EventBus,
        session_factory: sessionmaker[Session],
        adapter: PlannerExecutorAdapter | None = None,
        poll_interval_seconds: float = 0.1,
        batch_size: int = 10,
        recovery_scan_interval_seconds: float = 30.0,
        recovery_batch_size: int = 100,
        queued_recovery_age_seconds: float = 30.0,
        running_timeout_seconds: float = 1800.0,
    ) -> None:
        """Initialize worker loop settings and runtime dependencies."""
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if recovery_scan_interval_seconds <= 0:
            raise ValueError("recovery_scan_interval_seconds must be > 0")
        if recovery_batch_size < 1:
            raise ValueError("recovery_batch_size must be >= 1")
        if queued_recovery_age_seconds <= 0:
            raise ValueError("queued_recovery_age_seconds must be > 0")
        if running_timeout_seconds <= 0:
            raise ValueError("running_timeout_seconds must be > 0")

        self._bus = bus
        self._session_factory = session_factory
        self._adapter = adapter or NoOpPlannerExecutorAdapter()
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._recovery_scan_interval_seconds = recovery_scan_interval_seconds
        self._recovery_batch_size = recovery_batch_size
        self._queued_recovery_age_seconds = queued_recovery_age_seconds
        self._running_timeout_seconds = running_timeout_seconds
        self._last_recovery_scan_monotonic = 0.0
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the coordinator run loop if it is not already running."""
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event.clear()
        self._runner_task = asyncio.create_task(
            self.run(),
            name="agenticai-coordinator-worker",
        )

    async def stop(self) -> None:
        """Request graceful stop and await loop shutdown."""
        self._stop_event.set()
        if self._runner_task is None:
            return

        try:
            await self._runner_task
        finally:
            self._runner_task = None

    @property
    def is_running(self) -> bool:
        """Return True while the coordinator background loop task is active."""
        return self._runner_task is not None and not self._runner_task.done()

    async def run(self) -> None:
        """Poll queue messages forever until stop is requested."""
        while not self._stop_event.is_set():
            processed_count = await self.run_once()
            if processed_count == 0:
                await asyncio.sleep(self._poll_interval_seconds)
            else:
                await asyncio.sleep(0)

    async def run_once(self) -> int:
        """Process at most one batch of queued task messages."""
        try:
            await asyncio.to_thread(self._run_recovery_if_due)
        except asyncio.CancelledError:
            raise
        except WORKER_EXCEPTIONS:
            logger.exception("Recovery scan failed; continuing with dequeue loop")
        except Exception:
            logger.exception("Recovery scan failed with unexpected error; continuing")
        try:
            messages = await asyncio.to_thread(
                self._bus.dequeue,
                TASK_QUEUE,
                limit=self._batch_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to dequeue messages from queue '%s'", TASK_QUEUE)
            return 0

        if messages:
            log_event(
                logger,
                event="queue.tasks.dequeued",
                queue=TASK_QUEUE,
                count=len(messages),
            )

        processed_count = 0
        for message in messages:
            try:
                await self._process_message(message)
                processed_count += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to process queued message: %s", message)
        return processed_count

    def _run_recovery_if_due(self) -> None:
        """Run periodic stale-task recovery checks."""
        now_monotonic = time.monotonic()
        elapsed = now_monotonic - self._last_recovery_scan_monotonic
        if elapsed < self._recovery_scan_interval_seconds:
            return
        self._last_recovery_scan_monotonic = now_monotonic
        try:
            self._recover_stale_queued_tasks()
        except WORKER_EXCEPTIONS:
            logger.exception("Stale QUEUED task recovery failed")
        except Exception:
            logger.exception("Stale QUEUED task recovery failed with unexpected error")
        try:
            self._recover_stale_running_tasks()
        except WORKER_EXCEPTIONS:
            logger.exception("Stale RUNNING task recovery failed")
        except Exception:
            logger.exception("Stale RUNNING task recovery failed with unexpected error")

    def _recover_stale_queued_tasks(self) -> None:
        """Re-enqueue long-stale QUEUED tasks that may have missed queue publish."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._queued_recovery_age_seconds)
        with self._session_factory() as session:
            tasks = (
                session.execute(
                    select(Task)
                    .where(Task.status == TaskStatus.QUEUED.value, Task.updated_at <= cutoff)
                    .order_by(Task.updated_at.asc())
                    .limit(self._recovery_batch_size)
                )
                .scalars()
                .all()
            )
            if not tasks:
                return

            recovered_count = 0
            touched_count = 0
            touched_at = datetime.now(UTC)
            for task in tasks:
                payload = {
                    "task_id": task.id,
                    "org_id": task.org_id,
                    "requested_by_user_id": task.requested_by_user_id,
                    "status": task.status,
                }
                try:
                    accepted = self._bus.enqueue(TASK_QUEUE, task.id, payload)
                except WORKER_EXCEPTIONS:
                    logger.exception(
                        "Queued task recovery failed while enqueueing task %s",
                        task.id,
                    )
                    break
                if accepted:
                    recovered_count += 1
                    log_event(
                        logger,
                        event="task.recovery.queued_reenqueued",
                        task_id=task.id,
                    )
                task.updated_at = touched_at
                session.add(task)
                touched_count += 1

            if touched_count > 0:
                session.commit()
            if recovered_count > 0:
                log_event(
                    logger,
                    event="task.recovery.queued_summary",
                    recovered_count=recovered_count,
                    scanned_count=len(tasks),
                )

    def _recover_stale_running_tasks(self) -> None:
        """Fail stale RUNNING tasks so they do not remain stranded forever."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._running_timeout_seconds)
        with self._session_factory() as session:
            stale_tasks = (
                session.execute(
                    select(Task)
                    .where(Task.status == TaskStatus.RUNNING.value, Task.updated_at <= cutoff)
                    .order_by(Task.updated_at.asc())
                    .limit(self._recovery_batch_size)
                )
                .scalars()
                .all()
            )
            if not stale_tasks:
                return

            marked_at = datetime.now(UTC)
            for task in stale_tasks:
                task.status = TaskStatus.TIMED_OUT.value
                task.error_message = "Coordinator recovery timed out a stale RUNNING task"
                task.completed_at = marked_at
                task.updated_at = marked_at
                session.add(task)
                add_audit_event(
                    session,
                    org_id=task.org_id,
                    task_id=task.id,
                    actor_user_id=task.requested_by_user_id,
                    event_type="task.lifecycle.timed_out",
                    event_payload={"status": task.status},
                    created_at=marked_at,
                )
                log_event(
                    logger,
                    event="task.recovery.running_timed_out",
                    task_id=task.id,
                )
            session.commit()
            log_event(
                logger,
                event="task.recovery.running_summary",
                timed_out_count=len(stale_tasks),
            )

    async def _process_message(self, message: QueuedMessage) -> None:
        """Process one dequeued task message through risk and execution lifecycle."""
        payload = message.get("payload", {})
        raw_task_id = payload.get("task_id")
        if not isinstance(raw_task_id, str) or not raw_task_id:
            log_event(
                logger,
                level=logging.WARNING,
                event="queue.tasks.invalid_message",
                queue=TASK_QUEUE,
                message=message,
            )
            return

        try:
            handoff = await asyncio.to_thread(self._mark_task_running, raw_task_id)
        except Exception:
            logger.exception(
                "Failed to transition task %s to RUNNING; requeueing message",
                raw_task_id,
            )
            await asyncio.to_thread(self._requeue_message, message)
            return
        if handoff is None:
            return

        if not handoff.approved_resume:
            risk_assessment = classify_task_risk(handoff.prompt)
            if risk_assessment.requires_approval:
                bypass_mode = await asyncio.to_thread(
                    self._resolve_effective_bypass_mode,
                    handoff.org_id,
                    handoff.requested_by_user_id,
                )
                if bypass_allows_risk(mode=bypass_mode, risk_tier=risk_assessment.tier):
                    await asyncio.to_thread(
                        self._record_task_risk,
                        handoff.task_id,
                        risk_assessment,
                        bypass_mode,
                    )
                    log_event(
                        logger,
                        event="policy.bypass.applied",
                        task_id=handoff.task_id,
                        bypass_mode=bypass_mode.value,
                        risk_tier=risk_assessment.tier.value,
                    )
                else:
                    await asyncio.to_thread(
                        self._mark_task_waiting_approval,
                        handoff.task_id,
                        risk_assessment,
                        bypass_mode,
                    )
                    return
            else:
                await asyncio.to_thread(
                    self._record_task_risk,
                    handoff.task_id,
                    risk_assessment,
                )

        await asyncio.to_thread(
            self._mark_execution_started,
            handoff.task_id,
            handoff.approved_resume,
        )
        execution_started_at = time.perf_counter()
        try:
            result = await self._execute_handoff(handoff)
        except Exception:
            logger.exception("Planner/executor handoff failed for task %s", handoff.task_id)
            result = ExecutionResult(
                success=False,
                error_message="Planner/executor handoff failed",
            )
        duration_ms = round((time.perf_counter() - execution_started_at) * 1000, 2)
        log_event(
            logger,
            event="task.execution.completed",
            task_id=handoff.task_id,
            success=result.success,
            duration_ms=duration_ms,
            error_message=result.error_message,
        )

        await asyncio.to_thread(self._finalize_task, handoff.task_id, result)

    def _requeue_message(self, message: QueuedMessage) -> None:
        """Best-effort requeue for transient failures before execution starts."""
        job_id = message.get("job_id")
        payload = message.get("payload")
        if not isinstance(job_id, str) or not isinstance(payload, dict):
            log_event(
                logger,
                level=logging.WARNING,
                event="queue.tasks.requeue_invalid_message",
                queue=TASK_QUEUE,
                message=message,
            )
            return

        try:
            accepted = self._bus.enqueue(TASK_QUEUE, job_id, payload)
        except Exception:
            logger.exception(
                "Failed to requeue message for task %s after transition failure",
                payload.get("task_id"),
            )
            return

        if accepted:
            log_event(
                logger,
                event="queue.tasks.requeued",
                queue=TASK_QUEUE,
                task_id=payload.get("task_id"),
                job_id=job_id,
            )
            return
        log_event(
            logger,
            level=logging.WARNING,
            event="queue.tasks.requeue_duplicate",
            queue=TASK_QUEUE,
            task_id=payload.get("task_id"),
            job_id=job_id,
        )

    def _resolve_effective_bypass_mode(self, org_id: str, user_id: str) -> BypassMode:
        """Resolve effective bypass mode after applying org policy constraints."""
        with self._session_factory() as session:
            return resolve_effective_bypass_mode(session, org_id=org_id, user_id=user_id)

    def _mark_execution_started(self, task_id: str, approved_resume: bool) -> None:
        """Persist execution backend metadata before adapter handoff begins."""
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            if task.status != TaskStatus.RUNNING.value:
                return
            now = datetime.now(UTC)
            task.execution_backend = str(getattr(self._adapter, "backend_name", "noop"))
            task.execution_attempts = int(task.execution_attempts) + 1
            task.execution_last_heartbeat_at = now
            task.execution_metadata = json.dumps({"approved_resume": approved_resume})
            task.updated_at = now
            session.add(task)
            add_audit_event(
                session,
                org_id=task.org_id,
                task_id=task.id,
                actor_user_id=task.requested_by_user_id,
                event_type="task.execution.started",
                event_payload={
                    "execution_backend": task.execution_backend,
                    "execution_attempts": task.execution_attempts,
                },
                created_at=now,
            )
            session.commit()

    def _record_task_risk(
        self,
        task_id: str,
        assessment: RiskAssessment,
        bypass_mode: BypassMode = BypassMode.DISABLED,
    ) -> None:
        """Persist risk metadata when approval is not required or bypass is applied."""
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            if task.status != TaskStatus.RUNNING.value:
                return
            now = datetime.now(UTC)
            task.risk_tier = assessment.tier.value
            task.approval_required = False
            task.updated_at = now
            session.add(task)
            add_audit_event(
                session,
                org_id=task.org_id,
                task_id=task.id,
                actor_user_id=task.requested_by_user_id,
                event_type="task.lifecycle.risk_assessed",
                event_payload={
                    "risk_tier": assessment.tier.value,
                    "approval_required": False,
                },
                created_at=now,
            )
            if bypass_mode != BypassMode.DISABLED:
                add_audit_event(
                    session,
                    org_id=task.org_id,
                    task_id=task.id,
                    actor_user_id=task.requested_by_user_id,
                    event_type="policy.bypass.applied",
                    event_payload={
                        "bypass_mode": bypass_mode.value,
                        "risk_tier": assessment.tier.value,
                    },
                    created_at=now,
                )
            session.commit()

    def _mark_task_waiting_approval(
        self,
        task_id: str,
        assessment: RiskAssessment,
        bypass_mode: BypassMode = BypassMode.DISABLED,
    ) -> None:
        """Pause task execution and persist approval request state."""
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            if task.status != TaskStatus.RUNNING.value:
                return

            now = datetime.now(UTC)
            task.status = TaskStatus.WAITING_APPROVAL.value
            task.risk_tier = assessment.tier.value
            task.approval_required = True
            task.approval_decision = ApprovalDecision.PENDING.value
            task.approval_requested_at = now
            task.updated_at = now
            approval = Approval(
                org_id=task.org_id,
                task_id=task.id,
                requested_by_user_id=task.requested_by_user_id,
                risk_tier=assessment.tier.value,
                decision=ApprovalDecision.PENDING.value,
                reason=assessment.rationale,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            session.add(approval)
            add_audit_event(
                session,
                org_id=task.org_id,
                task_id=task.id,
                actor_user_id=task.requested_by_user_id,
                event_type="task.lifecycle.waiting_approval",
                event_payload={
                    "risk_tier": assessment.tier.value,
                    "approval_id": approval.id,
                    "bypass_mode": bypass_mode.value,
                },
                created_at=now,
            )
            session.commit()
            log_event(
                logger,
                event="task.lifecycle.waiting_approval",
                task_id=task.id,
                risk_tier=assessment.tier.value,
                approval_id=approval.id,
            )

    async def _execute_handoff(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
        """Invoke adapter execution and normalize sync/async return styles."""
        execute = self._adapter.execute
        if inspect.iscoroutinefunction(execute):
            result = await execute(handoff)
        else:
            result = await asyncio.to_thread(execute, handoff)
            if inspect.isawaitable(result):
                result = await result

        if not isinstance(result, ExecutionResult):
            raise TypeError("PlannerExecutorAdapter.execute must return ExecutionResult")
        return result

    def _mark_task_running(self, task_id: str) -> PlannerExecutorHandoff | None:
        """Transition a queued or approved-waiting task into RUNNING state."""
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                log_event(
                    logger,
                    level=logging.WARNING,
                    event="task.lifecycle.unknown_task",
                    task_id=task_id,
                )
                return None
            resumable_waiting_approval = (
                task.status == TaskStatus.WAITING_APPROVAL.value
                and task.approval_decision == ApprovalDecision.APPROVED.value
            )
            if task.status != TaskStatus.QUEUED.value and not resumable_waiting_approval:
                log_event(
                    logger,
                    level=logging.DEBUG,
                    event="task.lifecycle.skip_nonqueued",
                    task_id=task.id,
                    status=task.status,
                )
                return None
            org_id = task.org_id
            requested_by_user_id = task.requested_by_user_id
            prompt = task.prompt
            from_status = task.status

            now = datetime.now(UTC)
            task.status = TaskStatus.RUNNING.value
            task.started_at = task.started_at or now
            task.updated_at = now
            task.error_message = None
            session.add(task)
            add_audit_event(
                session,
                org_id=task.org_id,
                task_id=task.id,
                actor_user_id=task.requested_by_user_id,
                event_type="task.lifecycle.running",
                event_payload={"from_status": from_status},
                created_at=now,
            )
            session.commit()
            log_event(
                logger,
                event="task.lifecycle.transition",
                task_id=task.id,
                from_status=from_status,
                to_status=TaskStatus.RUNNING.value,
            )

            return PlannerExecutorHandoff(
                task_id=task.id,
                org_id=org_id,
                requested_by_user_id=requested_by_user_id,
                prompt=prompt,
                approved_resume=resumable_waiting_approval,
            )

    def _finalize_task(self, task_id: str, result: ExecutionResult) -> None:
        """Persist terminal status updates after adapter execution completes."""
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                log_event(
                    logger,
                    level=logging.WARNING,
                    event="task.lifecycle.finalize_missing_task",
                    task_id=task_id,
                )
                return
            if task.status == TaskStatus.CANCELED.value:
                log_event(
                    logger,
                    event="task.lifecycle.finalize_skipped_canceled",
                    task_id=task.id,
                    status=task.status,
                )
                return
            if task.status != TaskStatus.RUNNING.value:
                log_event(
                    logger,
                    level=logging.DEBUG,
                    event="task.lifecycle.finalize_skipped_nonrunning",
                    task_id=task.id,
                    status=task.status,
                )
                return

            now = datetime.now(UTC)
            final_status = TaskStatus.SUCCEEDED.value if result.success else TaskStatus.FAILED.value
            task.status = final_status
            task.error_message = None if result.success else result.error_message
            task.completed_at = now
            task.execution_last_heartbeat_at = now
            task.updated_at = now
            session.add(task)
            add_audit_event(
                session,
                org_id=task.org_id,
                task_id=task.id,
                actor_user_id=task.requested_by_user_id,
                event_type=f"task.lifecycle.{final_status.lower()}",
                event_payload={"status": final_status, "error_message": task.error_message},
                created_at=now,
            )
            session.commit()
            log_event(
                logger,
                event="task.lifecycle.transition",
                task_id=task.id,
                from_status=TaskStatus.RUNNING.value,
                to_status=final_status,
                error_message=task.error_message,
            )
