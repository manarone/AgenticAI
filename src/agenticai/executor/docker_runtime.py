"""Docker-backed execution adapter for coordinator task handoffs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agenticai.coordinator.worker import ExecutionResult, PlannerExecutorHandoff

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import branch depends on runtime environment
    import requests
except Exception:  # pragma: no cover - fallback used in tests/minimal envs
    requests = None  # type: ignore[assignment]

try:  # pragma: no cover - import branch depends on runtime environment
    import docker
    from docker.errors import APIError, DockerException, NotFound
except Exception:  # pragma: no cover - fallback used in tests/no-docker envs
    docker = None  # type: ignore[assignment]

    class DockerException(Exception):
        """Fallback Docker exception type when docker SDK is unavailable."""

    class APIError(DockerException):
        """Fallback Docker API exception type."""

    class NotFound(DockerException):
        """Fallback Docker not-found exception type."""


if requests is not None:
    TIMEOUT_EXCEPTIONS: tuple[type[Exception], ...] = (
        requests.exceptions.ReadTimeout,
        requests.exceptions.Timeout,
    )
else:
    TIMEOUT_EXCEPTIONS = (TimeoutError,)


@dataclass(frozen=True)
class DockerRuntimeConfig:
    """Configuration for one Docker runtime adapter instance."""

    image: str
    timeout_seconds: float
    memory_limit: str | None
    nano_cpus: int | None
    socket_url: str = "unix:///var/run/docker.sock"


class DockerRuntimeExecutor:
    """Execute tasks inside per-task Docker containers with hard time/resource limits."""

    backend_name = "docker"

    def __init__(self, *, client: Any, config: DockerRuntimeConfig) -> None:
        """Create a Docker runtime adapter from a configured docker SDK client."""
        self._client = client
        self._config = config

    @classmethod
    def from_config(cls, *, config: DockerRuntimeConfig) -> DockerRuntimeExecutor:
        """Build runtime executor from config, failing when Docker SDK is unavailable."""
        if docker is None:
            raise RuntimeError("docker SDK is not installed")
        client = docker.DockerClient(base_url=config.socket_url)
        client.ping()
        return cls(client=client, config=config)

    def execute(self, handoff: PlannerExecutorHandoff) -> ExecutionResult:
        """Run one handoff inside a container and map exit status to execution result."""
        container = None
        try:
            container = self._client.containers.run(
                self._config.image,
                command=self._build_command(),
                detach=True,
                auto_remove=False,
                network_disabled=True,
                mem_limit=self._config.memory_limit,
                nano_cpus=self._config.nano_cpus,
                environment={
                    "AGENTICAI_TASK_ID": handoff.task_id,
                    "AGENTICAI_ORG_ID": handoff.org_id,
                    "AGENTICAI_REQUESTED_BY_USER_ID": handoff.requested_by_user_id,
                },
                labels={
                    "agenticai.task_id": handoff.task_id,
                    "agenticai.org_id": handoff.org_id,
                    "agenticai.runtime": self.backend_name,
                },
            )
            wait_result = container.wait(timeout=self._config.timeout_seconds)
            status_code = self._extract_status_code(wait_result)
            if status_code != 0:
                return ExecutionResult(
                    success=False,
                    error_message=(
                        f"Docker runtime exited with status {status_code}. "
                        f"logs={self._tail_logs(container)}"
                    ),
                )
            return ExecutionResult(success=True)
        except TIMEOUT_EXCEPTIONS:
            self._safe_kill(container)
            return ExecutionResult(
                success=False,
                error_message=(
                    f"Docker runtime timed out after {self._config.timeout_seconds:.1f}s"
                ),
            )
        except (APIError, DockerException, OSError) as exc:
            return ExecutionResult(
                success=False,
                error_message=f"Docker runtime failure: {exc}",
            )
        finally:
            self._safe_remove(container)

    def _build_command(self) -> list[str]:
        """Build deterministic container command for scaffold execution."""
        timestamp = datetime.now(UTC).isoformat()
        return ["sh", "-lc", f"echo 'agenticai runtime task ok {timestamp}' >/tmp/task.log"]

    @staticmethod
    def _extract_status_code(wait_result: object) -> int:
        """Extract integer exit code from docker SDK wait response payload."""
        if isinstance(wait_result, dict):
            raw = wait_result.get("StatusCode", 1)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 1
        try:
            return int(wait_result)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _tail_logs(container: Any) -> str:
        """Return a compact single-line tail from container logs."""
        if container is None:
            return "unavailable"
        try:
            logs = container.logs(stdout=True, stderr=True, tail=20)
        except Exception:
            return "unavailable"
        if isinstance(logs, bytes):
            return logs.decode("utf-8", errors="replace").strip().replace("\n", " | ")
        return str(logs).strip().replace("\n", " | ")

    @staticmethod
    def _safe_kill(container: Any) -> None:
        """Best-effort container kill for timeout handling."""
        if container is None:
            return
        try:
            container.kill()
        except NotFound:
            return
        except Exception:
            logger.exception("Failed to kill timed-out runtime container")

    @staticmethod
    def _safe_remove(container: Any) -> None:
        """Best-effort force remove that always runs after execution attempts."""
        if container is None:
            return
        try:
            container.remove(force=True)
        except NotFound:
            return
        except Exception:
            logger.exception("Failed to remove runtime container")
