"""Background coordinator loop for queued task execution."""

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from agenticai.bus.base import TASK_QUEUE, EventBus, QueuedMessage
from agenticai.core.observability import log_event
from agenticai.db.models import Task, TaskStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannerExecutorHandoff:
    """Minimal handoff envelope from coordinator to planner/executor."""

    task_id: str
    org_id: str
    requested_by_user_id: str
    prompt: str | None


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
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        self._bus = bus
        self._session_factory = session_factory
        self._adapter = adapter or NoOpPlannerExecutorAdapter()
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
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

    async def _process_message(self, message: QueuedMessage) -> None:
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

        handoff = await asyncio.to_thread(self._mark_task_running, raw_task_id)
        if handoff is None:
            return

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

    async def _execute_handoff(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
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
            if task.status != TaskStatus.QUEUED.value:
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

            now = datetime.now(UTC)
            task.status = TaskStatus.RUNNING.value
            task.started_at = task.started_at or now
            task.updated_at = now
            task.error_message = None
            session.add(task)
            session.commit()
            log_event(
                logger,
                event="task.lifecycle.transition",
                task_id=task.id,
                from_status=TaskStatus.QUEUED.value,
                to_status=TaskStatus.RUNNING.value,
            )

            return PlannerExecutorHandoff(
                task_id=task.id,
                org_id=org_id,
                requested_by_user_id=requested_by_user_id,
                prompt=prompt,
            )

    def _finalize_task(self, task_id: str, result: ExecutionResult) -> None:
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
            task.updated_at = now
            session.add(task)
            session.commit()
            log_event(
                logger,
                event="task.lifecycle.transition",
                task_id=task.id,
                from_status=TaskStatus.RUNNING.value,
                to_status=final_status,
                error_message=task.error_message,
            )
