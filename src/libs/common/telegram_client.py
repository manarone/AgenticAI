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

    async def _post(self, method: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f'{self._base_url()}/{method}', json=payload)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and not data.get('ok', False):
            raise RuntimeError(f'Telegram {method} failed: {data}')

    async def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> None:
        if not self.enabled:
            return
        payload = {'chat_id': chat_id, 'text': text}
        if reply_markup:
            payload['reply_markup'] = reply_markup
        await self._post('sendMessage', payload)

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        if not self.enabled:
            return
        payload = {'callback_query_id': callback_query_id, 'text': text}
        await self._post('answerCallbackQuery', payload)

    async def send_chat_action(self, chat_id: str | int, action: str = 'typing') -> None:
        if not self.enabled:
            return
        payload = {'chat_id': chat_id, 'action': action}
        await self._post('sendChatAction', payload)
