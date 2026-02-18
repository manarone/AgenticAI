from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from libs.common.config import get_settings

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class ToolExecutionRecord:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    success: bool = True


@dataclass
class LLMToolChatResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _development_fallback(self, user_prompt: str, memory: list[str] | None) -> tuple[str, int, int]:
        text = f'MVP fallback response. Memory used: {len(memory or [])}. Request: {user_prompt[:120]}'
        return text, 100, 100

    async def _post_chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            'Authorization': f'Bearer {self.settings.openai_api_key}',
            'Content-Type': 'application/json',
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _memory_wrapped_user_prompt(user_prompt: str, memory: list[str] | None) -> str:
        memory_text = '\n'.join(memory or [])
        return f'Memory:\n{memory_text}\n\nUser: {user_prompt}'

    async def chat(self, system_prompt: str, user_prompt: str, memory: list[str] | None = None) -> tuple[str, int, int]:
        if not self.settings.openai_api_key:
            return self._development_fallback(user_prompt, memory)

        payload = {
            'model': self.settings.openai_model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': self._memory_wrapped_user_prompt(user_prompt, memory)},
            ],
        }

        data = await self._post_chat_completion(payload)
        content = self._extract_content(data)
        usage = data.get('usage', {})
        return content, int(usage.get('prompt_tokens', 0)), int(usage.get('completion_tokens', 0))

    async def chat_with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        memory: list[str] | None,
        tools: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        max_rounds: int = 3,
    ) -> LLMToolChatResult:
        if not self.settings.openai_api_key:
            text, in_tok, out_tok = self._development_fallback(user_prompt, memory)
            return LLMToolChatResult(text=text, prompt_tokens=in_tok, completion_tokens=out_tok)

        if not tools:
            text, in_tok, out_tok = await self.chat(system_prompt=system_prompt, user_prompt=user_prompt, memory=memory)
            return LLMToolChatResult(text=text, prompt_tokens=in_tok, completion_tokens=out_tok)

        messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': self._memory_wrapped_user_prompt(user_prompt, memory)},
        ]
        tool_records: list[ToolExecutionRecord] = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            for _ in range(max(1, max_rounds)):
                payload = {
                    'model': self.settings.openai_model,
                    'messages': messages,
                    'tools': tools,
                    'tool_choice': 'auto',
                }
                data = await self._post_chat_completion(payload)
                usage = data.get('usage', {})
                prompt_tokens += int(usage.get('prompt_tokens', 0))
                completion_tokens += int(usage.get('completion_tokens', 0))

                message = self._extract_message(data)
                if not message:
                    break

                tool_calls = message.get('tool_calls') if isinstance(message, dict) else None
                if isinstance(tool_calls, list) and tool_calls:
                    messages.append(
                        {
                            'role': 'assistant',
                            'content': self._coerce_content(message.get('content')),
                            'tool_calls': tool_calls,
                        }
                    )
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = str(call.get('id', ''))
                        function = call.get('function') if isinstance(call.get('function'), dict) else {}
                        tool_name = str(function.get('name', '')).strip()
                        args = self._parse_tool_args(str(function.get('arguments', '{}')))
                        result: dict[str, Any]
                        success = True
                        try:
                            result = await tool_executor(tool_name, args)
                        except Exception as exc:  # pragma: no cover - defensive, execution path covered via fallback tests.
                            logger.exception('Tool execution failed for %s', tool_name)
                            success = False
                            result = {'ok': False, 'error': str(exc)}

                        tool_records.append(ToolExecutionRecord(name=tool_name, args=args, result=result, success=success))
                        messages.append(
                            {
                                'role': 'tool',
                                'tool_call_id': call_id,
                                'name': tool_name,
                                'content': json.dumps(result),
                            }
                        )
                    continue

                text = self._coerce_content(message.get('content')) or self._extract_content(data)
                if text:
                    return LLMToolChatResult(
                        text=text,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        tool_records=tool_records,
                    )

            fallback_text, in_tok, out_tok = await self.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                memory=memory,
            )
            return LLMToolChatResult(
                text=fallback_text,
                prompt_tokens=prompt_tokens + in_tok,
                completion_tokens=completion_tokens + out_tok,
                tool_records=tool_records,
            )
        except (httpx.HTTPError, ValueError):
            # Provider may not support tools in this model/account. Fall back to plain chat.
            fallback_text, in_tok, out_tok = await self.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                memory=memory,
            )
            return LLMToolChatResult(
                text=fallback_text,
                prompt_tokens=prompt_tokens + in_tok,
                completion_tokens=completion_tokens + out_tok,
                tool_records=tool_records,
            )

    @staticmethod
    def _parse_tool_args(arguments: str) -> dict[str, Any]:
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _extract_message(data: dict[str, Any]) -> dict[str, Any] | None:
        choices = data.get('choices')
        if not isinstance(choices, list) or not choices:
            return None
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get('message')
        if isinstance(message, dict):
            return message
        return None

    @staticmethod
    def _coerce_content(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get('text')
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return '\n'.join(parts).strip()
        return ''

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        message = LLMClient._extract_message(data)
        if message:
            content = LLMClient._coerce_content(message.get('content'))
            if content:
                return content

        choices = data.get('choices')
        if not isinstance(choices, list) or not choices:
            return 'Model response was empty.'

        first = choices[0] if isinstance(choices[0], dict) else {}
        text = first.get('text')
        if isinstance(text, str) and text.strip():
            return text.strip()

        return 'Model response was empty.'
