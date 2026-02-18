import httpx
import pytest

from libs.common.llm import LLMClient


@pytest.mark.asyncio
async def test_chat_with_tools_happy_path(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client.settings, 'openai_api_key', 'test-key')

    calls = {'count': 0}

    async def fake_post(payload):
        calls['count'] += 1
        if calls['count'] == 1:
            return {
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
                'choices': [
                    {
                        'message': {
                            'role': 'assistant',
                            'content': '',
                            'tool_calls': [
                                {
                                    'id': 'call_1',
                                    'type': 'function',
                                    'function': {'name': 'web_search', 'arguments': '{"query":"latest"}'},
                                }
                            ],
                        }
                    }
                ],
            }
        return {
            'usage': {'prompt_tokens': 8, 'completion_tokens': 6},
            'choices': [{'message': {'role': 'assistant', 'content': 'Here is the answer.'}}],
        }

    async def fake_executor(name, args):
        assert name == 'web_search'
        assert args['query'] == 'latest'
        return {'ok': True, 'results': [{'title': 'A', 'url': 'https://a.example'}]}

    monkeypatch.setattr(client, '_post_chat_completion', fake_post)

    result = await client.chat_with_tools(
        system_prompt='sys',
        user_prompt='user',
        memory=[],
        tools=[{'type': 'function', 'function': {'name': 'web_search'}}],
        tool_executor=fake_executor,
    )

    assert result.text == 'Here is the answer.'
    assert result.prompt_tokens == 18
    assert result.completion_tokens == 11
    assert len(result.tool_records) == 1
    assert result.tool_records[0].name == 'web_search'


@pytest.mark.asyncio
async def test_chat_with_tools_provider_fallback(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client.settings, 'openai_api_key', 'test-key')

    request = httpx.Request('POST', 'https://example.com/chat/completions')
    response = httpx.Response(400, request=request)

    async def fake_post(payload):
        raise httpx.HTTPStatusError('tools not supported', request=request, response=response)

    async def fake_chat(system_prompt, user_prompt, memory=None):
        return 'fallback response', 3, 2

    monkeypatch.setattr(client, '_post_chat_completion', fake_post)
    monkeypatch.setattr(client, 'chat', fake_chat)

    result = await client.chat_with_tools(
        system_prompt='sys',
        user_prompt='user',
        memory=[],
        tools=[{'type': 'function', 'function': {'name': 'web_search'}}],
        tool_executor=lambda name, args: None,
    )

    assert result.text == 'fallback response'
    assert result.prompt_tokens == 3
    assert result.completion_tokens == 2


@pytest.mark.asyncio
async def test_chat_with_tools_plain_response_without_tool_call(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client.settings, 'openai_api_key', 'test-key')

    async def fake_post(payload):
        return {
            'usage': {'prompt_tokens': 7, 'completion_tokens': 4},
            'choices': [{'message': {'role': 'assistant', 'content': 'no tools needed'}}],
        }

    monkeypatch.setattr(client, '_post_chat_completion', fake_post)

    result = await client.chat_with_tools(
        system_prompt='sys',
        user_prompt='user',
        memory=[],
        tools=[{'type': 'function', 'function': {'name': 'web_search'}}],
        tool_executor=lambda name, args: None,
    )

    assert result.text == 'no tools needed'
    assert result.prompt_tokens == 7
    assert result.completion_tokens == 4
    assert result.tool_records == []
