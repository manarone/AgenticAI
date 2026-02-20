import os

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


_MVP_SMOKE_PATH_KEYWORDS = (
    'tests/integration/test_coordinator_flow.py',
    'tests/integration/test_executor_retries.py',
)
_SAFETY_CRITICAL_PATH_KEYWORDS = (
    'test_shell_policy.py',
    'test_executor_shell_policy.py',
    'test_approval_grants.py',
    'test_sanitizer.py',
    'test_coordinator_flow.py',
    'test_executor_retries.py',
    'test_state_machine.py',
)
_BETA_BLOCKING_PATH_KEYWORDS = (
    'tests/integration/',
    'test_memory_backend_config.py',
    'test_context_compaction.py',
    'test_invite_codes.py',
    'test_approval_grants.py',
    'test_shell_policy.py',
    'test_executor_shell_policy.py',
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        nodeid = item.nodeid
        path_only = nodeid.split('::', 1)[0]

        if any(keyword in path_only for keyword in _MVP_SMOKE_PATH_KEYWORDS):
            item.add_marker(pytest.mark.mvp_smoke)
        if any(keyword in path_only for keyword in _SAFETY_CRITICAL_PATH_KEYWORDS):
            item.add_marker(pytest.mark.safety_critical)
        if any(keyword in path_only for keyword in _BETA_BLOCKING_PATH_KEYWORDS):
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
