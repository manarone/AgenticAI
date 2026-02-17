import asyncio

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from libs.common.db import engine
from libs.common.models import Base


async def main() -> None:
    for attempt in range(30):
        try:
            async with engine.begin() as conn:
                await conn.execute(text('SELECT 1'))
                await conn.run_sync(Base.metadata.create_all)
            return
        except OperationalError:
            if attempt == 29:
                raise
            await asyncio.sleep(1)


if __name__ == '__main__':
    asyncio.run(main())
