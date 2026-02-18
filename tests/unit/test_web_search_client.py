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
        self.calls = []
        self.is_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        self.calls.append({'args': args, 'kwargs': kwargs})
        if self._exc is not None:
            raise self._exc
        if isinstance(self._response, list):
            if not self._response:
                return _FakeResponse({'results': []})
            return self._response.pop(0)
        return self._response

    async def aclose(self):
        self.is_closed = True


@pytest.mark.asyncio
async def test_search_success_and_result_clamp(monkeypatch):
    payload = {
        'results': [
            {'title': 'A', 'url': 'https://a.example', 'content': 'first', 'engine': 'duckduckgo'},
            {'title': 'B', 'url': 'https://b.example', 'content': 'second', 'engine': 'bing'},
            {'title': 'C', 'url': 'https://c.example', 'content': 'third', 'engine': 'google'},
        ]
    }
    fake_client = _FakeClient(response=_FakeResponse(payload))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kwargs: fake_client)

    client = SearxNGClient(base_url='http://searxng:8080', timeout_seconds=3, max_results=2, max_concurrent=4)
    result = await client.search('   latest ai news   ', max_results=10)

    assert result['query'] == 'latest ai news'
    assert len(result['results']) == 2
    assert result['results'][0]['title'] == 'A'
    assert fake_client.calls[0]['kwargs']['params']['pageno'] == 1


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


@pytest.mark.asyncio
async def test_deep_search_fetches_multiple_pages(monkeypatch):
    fake_client = _FakeClient(
        response=[
            _FakeResponse({'results': [{'title': 'A', 'url': 'https://a.example', 'content': 'a'}]}),
            _FakeResponse({'results': [{'title': 'B', 'url': 'https://b.example', 'content': 'b'}]}),
        ]
    )
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kwargs: fake_client)

    client = SearxNGClient(base_url='http://searxng:8080', timeout_seconds=3, max_results=5, max_concurrent=2)
    result = await client.search('query', depth='deep', max_results=2)

    assert [call['kwargs']['params']['pageno'] for call in fake_client.calls] == [1, 2]
    assert [item['title'] for item in result['results']] == ['A', 'B']


@pytest.mark.asyncio
async def test_client_reused_across_calls(monkeypatch):
    fake_client = _FakeClient(response=_FakeResponse({'results': []}))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda *args, **kwargs: fake_client)

    client = SearxNGClient(base_url='http://searxng:8080', timeout_seconds=3, max_results=3, max_concurrent=2)
    await client.search('first')
    await client.search('second')
    assert len(fake_client.calls) == 2
    await client.aclose()
