from __future__ import annotations

import httpx

from libs.common.config import get_settings


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def chat(self, system_prompt: str, user_prompt: str, memory: list[str] | None = None) -> tuple[str, int, int]:
        memory_text = '\n'.join(memory or [])
        if not self.settings.openai_api_key:
            # Local fallback for development.
            text = f'MVP fallback response. Memory used: {len(memory or [])}. Request: {user_prompt[:120]}'
            return text, 100, 100

        payload = {
            'model': self.settings.openai_model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': f'Memory:\n{memory_text}\n\nUser: {user_prompt}'},
            ],
        }
        headers = {
            'Authorization': f'Bearer {self.settings.openai_api_key}',
            'Content-Type': 'application/json',
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.settings.openai_base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data['choices'][0]['message']['content']
        usage = data.get('usage', {})
        return content, int(usage.get('prompt_tokens', 0)), int(usage.get('completion_tokens', 0))
