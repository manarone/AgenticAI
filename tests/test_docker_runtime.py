from dataclasses import dataclass

from agenticai.coordinator import PlannerExecutorHandoff
from agenticai.executor.docker_runtime import (
    DockerException,
    DockerRuntimeConfig,
    DockerRuntimeExecutor,
)


@dataclass
class FakeContainer:
    status_code: int = 0
    wait_error: Exception | None = None
    logs_payload: bytes = b""
    killed: bool = False
    removed_force: bool | None = None
    wait_timeout: float | None = None

    def wait(self, *, timeout: float) -> dict[str, int]:
        self.wait_timeout = timeout
        if self.wait_error is not None:
            raise self.wait_error
        return {"StatusCode": self.status_code}

    def logs(self, **_kwargs: object) -> bytes:
        return self.logs_payload

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool) -> None:
        self.removed_force = force


class FakeContainers:
    def __init__(
        self,
        *,
        container: FakeContainer | None = None,
        run_error: Exception | None = None,
    ) -> None:
        self._container = container
        self._run_error = run_error
        self.last_run_kwargs: dict[str, object] | None = None

    def run(self, image: str, **kwargs: object) -> FakeContainer:
        self.last_run_kwargs = {"image": image, **kwargs}
        if self._run_error is not None:
            raise self._run_error
        assert self._container is not None
        return self._container


class FakeClient:
    def __init__(self, containers: FakeContainers) -> None:
        self.containers = containers


def _handoff(prompt: str = "do work") -> PlannerExecutorHandoff:
    return PlannerExecutorHandoff(
        task_id="task-1",
        org_id="org-1",
        requested_by_user_id="user-1",
        prompt=prompt,
    )


def _config() -> DockerRuntimeConfig:
    return DockerRuntimeConfig(
        image="python:3.12-slim",
        timeout_seconds=12.0,
        memory_limit="256m",
        nano_cpus=250_000_000,
    )


def test_docker_runtime_success_removes_container() -> None:
    container = FakeContainer(status_code=0)
    fake_client = FakeClient(FakeContainers(container=container))
    executor = DockerRuntimeExecutor(client=fake_client, config=_config())

    result = executor.execute(_handoff())

    assert result.success is True
    assert result.error_message is None
    assert container.killed is False
    assert container.removed_force is True


def test_docker_runtime_nonzero_exit_returns_failure_with_logs() -> None:
    container = FakeContainer(status_code=2, logs_payload=b"runtime failure")
    fake_client = FakeClient(FakeContainers(container=container))
    executor = DockerRuntimeExecutor(client=fake_client, config=_config())

    result = executor.execute(_handoff())

    assert result.success is False
    assert "status 2" in (result.error_message or "")
    assert "runtime failure" in (result.error_message or "")
    assert container.removed_force is True


def test_docker_runtime_timeout_kills_and_removes_container() -> None:
    container = FakeContainer(wait_error=TimeoutError("timed out"))
    fake_client = FakeClient(FakeContainers(container=container))
    executor = DockerRuntimeExecutor(client=fake_client, config=_config())

    result = executor.execute(_handoff())

    assert result.success is False
    assert "timed out" in (result.error_message or "")
    assert container.killed is True
    assert container.removed_force is True


def test_docker_runtime_run_error_returns_failure_without_container_cleanup() -> None:
    run_error = DockerException("socket unavailable")
    fake_client = FakeClient(FakeContainers(run_error=run_error))
    executor = DockerRuntimeExecutor(client=fake_client, config=_config())

    result = executor.execute(_handoff())

    assert result.success is False
    assert "socket unavailable" in (result.error_message or "")


def test_docker_runtime_uses_configured_limits_and_force_fail_marker() -> None:
    container = FakeContainer(status_code=0)
    fake_containers = FakeContainers(container=container)
    fake_client = FakeClient(fake_containers)
    config = _config()
    executor = DockerRuntimeExecutor(client=fake_client, config=config)

    result = executor.execute(_handoff(prompt="__force_fail__"))

    assert result.success is True
    assert fake_containers.last_run_kwargs is not None
    assert fake_containers.last_run_kwargs["image"] == config.image
    assert fake_containers.last_run_kwargs["mem_limit"] == config.memory_limit
    assert fake_containers.last_run_kwargs["nano_cpus"] == config.nano_cpus
    command = fake_containers.last_run_kwargs["command"]
    assert isinstance(command, list)
    assert "forced runtime failure" in " ".join(command)
