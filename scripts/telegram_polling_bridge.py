from __future__ import annotations

import asyncio
import os

import httpx


async def run() -> None:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    coordinator_webhook_url = os.getenv(
        'COORDINATOR_WEBHOOK_URL', 'http://coordinator:8000/telegram/webhook'
    ).strip()

    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN is required for telegram polling bridge')

    telegram_base = f'https://api.telegram.org/bot{token}'
    offset = 0

    async with httpx.AsyncClient(timeout=40) as client:
        while True:
            try:
                response = await client.get(
                    f'{telegram_base}/getUpdates',
                    params={'timeout': 25, 'offset': offset},
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get('ok'):
                    await asyncio.sleep(1)
                    continue

                for update in payload.get('result', []):
                    update_id = int(update.get('update_id', 0))
                    if update_id:
                        offset = update_id + 1

                    await client.post(coordinator_webhook_url, json=update)
            except Exception:
                await asyncio.sleep(2)


if __name__ == '__main__':
    asyncio.run(run())
