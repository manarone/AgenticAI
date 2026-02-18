from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx


class WebSearchUnavailableError(RuntimeError):
    def __init__(self, message: str = 'Live web search is currently unavailable.') -> None:
        super().__init__(message)
        self.user_message = message


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    engine: str | None = None
    published_at: str | None = None


class SearxNGClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        max_results: int,
        max_concurrent: int,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.max_results = max(1, int(max_results))
        self._max_concurrent = max(1, int(max_concurrent))
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None
        self._client_lock: asyncio.Lock | None = None
        self._client_lock_loop: asyncio.AbstractEventLoop | None = None
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized = ' '.join(query.strip().split())
        return normalized[:512]

    def _clamp_results(self, requested: int | None) -> int:
        if requested is None:
            return self.max_results
        return max(1, min(int(requested), self.max_results))

    @staticmethod
    def _normalize_published_at(item: dict) -> str | None:
        if not isinstance(item, dict):
            return None
        for key in ('publishedDate', 'published_at', 'publishedAt', 'pubdate', 'date'):
            value = str(item.get(key, '') or '').strip()
            if value:
                return value
        return None

    @staticmethod
    def _normalize_results(raw_results: list, *, limit: int) -> list[dict]:
        seen_urls: set[str] = set()
        normalized_results: list[dict] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get('url', '')).strip()
            title = str(item.get('title', '')).strip()
            if not url or not title:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            normalized_results.append(
                {
                    'title': title,
                    'url': url,
                    'snippet': str(item.get('content', '') or '').strip(),
                    'engine': str(item.get('engine', '') or '').strip() or None,
                    'published_at': SearxNGClient._normalize_published_at(item),
                }
            )
            if len(normalized_results) >= limit:
                break
        return normalized_results

    def _get_client_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._client_lock is None or self._client_lock_loop is not loop:
            self._client_lock = asyncio.Lock()
            self._client_lock_loop = loop
        return self._client_lock

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._get_client_lock():
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
            return self._client

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
            self._semaphore_loop = loop
        return self._semaphore

    async def aclose(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def search(
        self,
        query: str,
        *,
        depth: str = 'balanced',
        max_results: int | None = None,
        time_range: Literal['day', 'week', 'month', 'year'] | None = None,
        categories: str | None = None,
    ) -> dict:
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            raise ValueError('Search query cannot be empty.')

        limit = self._clamp_results(max_results)
        normalized_depth = 'deep' if depth == 'deep' else 'balanced'
        pages = 2 if normalized_depth == 'deep' else 1

        try:
            async with self._get_semaphore():
                client = await self._get_client()
                aggregated: list[dict] = []
                for page in range(1, pages + 1):
                    params = {
                        'q': normalized_query,
                        'format': 'json',
                        'safesearch': '1',
                        'pageno': page,
                    }
                    if time_range in {'day', 'week', 'month', 'year'}:
                        params['time_range'] = time_range
                    normalized_categories = (categories or '').strip()
                    if normalized_categories:
                        params['categories'] = normalized_categories
                    response = await client.get(
                        f'{self.base_url}/search',
                        params=params,
                        headers={'Accept': 'application/json'},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw_results = payload.get('results') if isinstance(payload, dict) else None
                    if not isinstance(raw_results, list):
                        continue
                    aggregated.extend(raw_results)
                    if len(self._normalize_results(aggregated, limit=limit)) >= limit:
                        break
        except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
            raise WebSearchUnavailableError() from exc

        normalized_results = self._normalize_results(aggregated, limit=limit)

        return {
            'query': normalized_query,
            'depth': normalized_depth,
            'time_range': time_range,
            'categories': (categories or '').strip() or None,
            'results': normalized_results,
        }
