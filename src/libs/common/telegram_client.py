from __future__ import annotations

import httpx

from libs.common.config import get_settings


class TelegramClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_bot_token)

    def _base_url(self) -> str:
        return f'https://api.telegram.org/bot{self.settings.telegram_bot_token}'

    async def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> None:
        if not self.enabled:
            return
        payload = {'chat_id': chat_id, 'text': text}
        if reply_markup:
            payload['reply_markup'] = reply_markup
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f'{self._base_url()}/sendMessage', json=payload)

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        if not self.enabled:
            return
        payload = {'callback_query_id': callback_query_id, 'text': text}
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f'{self._base_url()}/answerCallbackQuery', json=payload)
