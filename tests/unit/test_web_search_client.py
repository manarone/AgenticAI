import httpx
import pytest

from libs.common.web_search import SearxNGClient, WebSearchUnavailableError


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


@pytest.mark.asyncio
async def test_search_success_and_result_clamp(monkeypatch):
    payload = {
        'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'first', 'engine': 'duckduckgo'},
            {'title': 'B', 'url': 'https://b.example', 'content': 'second', 'engine': 'bing'},
            {'title': 'C', 'url': 'https://c.example', 'content': 'third', 'engine': 'google'},
        ]
    }
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kwargs: _FakeClient(response=_FakeResponse(payload)))

    client = SearxNGClient(base_url='http://searxng:8080', timeout_seconds=3, max_results=2, max_concurrent=4)
    result = await client.search('   latest ai news   ', max_results=10)

    assert result['query'] == 'latest ai news'
    assert len(result['results']) == 2
    assert result['results'][0]['title'] == 'A'


@pytest.mark.asyncio
async def test_search_timeout_raises_unavailable(monkeypatch):
    monkeypatch.setattr(
        httpx,
        'AsyncClient',
        lambda *args, **kwargs: _FakeClient(exc=httpx.TimeoutException('timeout')),
    )
    client = SearxNGClient(base_url='http://searxng:8080', timeout_seconds=1, max_results=3, max_concurrent=2)

    with pytest.raises(WebSearchUnavailableError):
        await client.search('something')


@pytest.mark.asyncio
async def test_search_malformed_payload_returns_empty_results(monkeypatch):
    payload = {'unexpected': 'shape'}
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kwargs: _FakeClient(response=_FakeResponse(payload)))

    client = SearxNGClient(base_url='http://searxng:8080', timeout_seconds=3, max_results=3, max_concurrent=2)
    result = await client.search('query')
    assert result['results'] == []
