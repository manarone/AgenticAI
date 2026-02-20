import os
from pathlib import Path

import pytest

# Ensure settings are resolved from test env before app modules import.
os.environ['APP_ENV'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:////tmp/agentai_test.db'
os.environ['BUS_BACKEND'] = 'inmemory'
os.environ['MEMORY_BACKEND'] = 'local'
os.environ['TELEGRAM_BOT_TOKEN'] = ''
os.environ['OPENAI_API_KEY'] = ''
os.environ['ADMIN_TOKEN'] = 'test-admin-token'
os.environ['OPENAI_MODEL'] = 'openrouter/kimi-k2.5'
os.environ['MAX_EXECUTOR_RETRIES'] = '1'


_MVP_SMOKE_TEST_PATHS = (
    'tests/integration/test_coordinator_flow.py',
    'tests/integration/test_executor_retries.py',
    'tests/integration/test_mvp_acceptance.py',
)
_SAFETY_CRITICAL_TEST_PATHS = (
    'tests/unit/test_shell_policy.py',
    'tests/integration/test_executor_shell_policy.py',
    'tests/unit/test_approval_grants.py',
    'tests/unit/test_sanitizer.py',
    'tests/integration/test_coordinator_flow.py',
    'tests/integration/test_executor_retries.py',
    'tests/unit/test_state_machine.py',
)
_BETA_BLOCKING_TEST_PATH_PREFIXES = ('tests/integration/',)
_BETA_BLOCKING_TEST_PATHS = (
    'tests/unit/test_memory_backend_config.py',
    'tests/unit/test_invite_codes.py',
    'tests/unit/test_approval_grants.py',
    'tests/unit/test_shell_policy.py',
)


def _assert_paths_exist(paths: tuple[str, ...], *, marker_name: str) -> None:
    missing = [path for path in paths if not Path(path).exists()]
    if missing:
        raise RuntimeError(f'Marker path configuration for {marker_name} references missing tests: {missing}')


def _assert_prefix_dirs_exist(prefixes: tuple[str, ...], *, marker_name: str) -> None:
    missing = [prefix for prefix in prefixes if not Path(prefix).is_dir()]
    if missing:
        raise RuntimeError(f'Marker prefix configuration for {marker_name} references missing directories: {missing}')


_assert_paths_exist(_MVP_SMOKE_TEST_PATHS, marker_name='mvp_smoke')
_assert_paths_exist(_SAFETY_CRITICAL_TEST_PATHS, marker_name='safety_critical')
_assert_paths_exist(_BETA_BLOCKING_TEST_PATHS, marker_name='beta_blocking')
_assert_prefix_dirs_exist(_BETA_BLOCKING_TEST_PATH_PREFIXES, marker_name='beta_blocking')


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        nodeid = item.nodeid
        path_only = nodeid.split('::', 1)[0]

        if path_only in _MVP_SMOKE_TEST_PATHS:
            item.add_marker(pytest.mark.mvp_smoke)
        if path_only in _SAFETY_CRITICAL_TEST_PATHS:
            item.add_marker(pytest.mark.safety_critical)
        if (
            path_only in _BETA_BLOCKING_TEST_PATHS
            or any(path_only.startswith(prefix) for prefix in _BETA_BLOCKING_TEST_PATH_PREFIXES)
        ):
            item.add_marker(pytest.mark.beta_blocking)


@pytest.fixture(autouse=True)
async def reset_state():
    from libs.common.config import get_settings
    from libs.common.db import engine
    from libs.common.models import Base
    from libs.common.task_bus import reset_inmemory_bus

    get_settings.cache_clear()
    await reset_inmemory_bus()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def prepare_invite_code():
    async def _prepare() -> str:
        from libs.common.db import AsyncSessionLocal
        from libs.common.repositories import CoreRepository

        async with AsyncSessionLocal() as db:
            repo = CoreRepository(db)
            tenant, _, _ = await repo.get_or_create_default_tenant_user()
            invite = await repo.create_invite_code(tenant_id=tenant.id, ttl_hours=24)
            await db.commit()
            return invite.code

    return _prepare


@pytest.fixture
def latest_task_for_user():
    async def _latest(telegram_user_id: int):
        from libs.common.db import AsyncSessionLocal
        from libs.common.repositories import CoreRepository

        async with AsyncSessionLocal() as db:
            repo = CoreRepository(db)
            identity = await repo.get_identity(str(telegram_user_id))
            if identity is None:
                return None
            tasks = await repo.list_user_tasks(identity.tenant_id, identity.user_id, limit=1)
            return tasks[0] if tasks else None

    return _latest
