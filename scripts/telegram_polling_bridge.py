from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def run() -> None:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    coordinator_webhook_url = os.getenv(
        'COORDINATOR_WEBHOOK_URL', 'http://coordinator:8000/telegram/webhook'
    ).strip()

    if not token:
        raise RuntimeError('TELEGRAM_BOT_TOKEN is required for telegram polling bridge')

    telegram_base = f'https://api.telegram.org/bot{token}'
    offset = 0
    max_webhook_retries = int(os.getenv('TELEGRAM_WEBHOOK_MAX_RETRIES', '5'))
    retry_sleep_seconds = float(os.getenv('TELEGRAM_WEBHOOK_RETRY_SLEEP_SECONDS', '2'))

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
                    delivered = False
                    for attempt in range(1, max_webhook_retries + 1):
                        try:
                            webhook_response = await client.post(coordinator_webhook_url, json=update)
                            webhook_response.raise_for_status()
                            delivered = True
                            break
                        except Exception:
                            logger.exception(
                                'coordinator webhook delivery failed update_id=%s attempt=%s/%s',
                                update_id,
                                attempt,
                                max_webhook_retries,
                            )
                            await asyncio.sleep(retry_sleep_seconds)
                    if not delivered:
                        logger.error('dropping update_id=%s after %s delivery attempts', update_id, max_webhook_retries)
                    if update_id:
                        offset = update_id + 1
            except Exception:
                logger.exception('telegram polling bridge loop failed')
                await asyncio.sleep(2)


if __name__ == '__main__':
    asyncio.run(run())
