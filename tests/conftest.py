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
