from __future__ import annotations

import asyncio
from dataclasses import dataclass

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
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized = ' '.join(query.strip().split())
        return normalized[:512]

    def _clamp_results(self, requested: int | None) -> int:
        if requested is None:
            return self.max_results
        return max(1, min(int(requested), self.max_results))

    async def search(self, query: str, *, depth: str = 'balanced', max_results: int | None = None) -> dict:
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            raise ValueError('Search query cannot be empty.')

        limit = self._clamp_results(max_results)
        params = {
            'q': normalized_query,
            'format': 'json',
            'safesearch': '1',
        }

        try:
            async with self._semaphore:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.get(
                        f'{self.base_url}/search',
                        params=params,
                        headers={'Accept': 'application/json'},
                    )
                    response.raise_for_status()
                    payload = response.json()
        except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
            raise WebSearchUnavailableError() from exc

        raw_results = payload.get('results') if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raw_results = []

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
                    'published_at': str(item.get('publishedDate', '') or '').strip() or None,
                }
            )
            if len(normalized_results) >= limit:
                break

        return {
            'query': normalized_query,
            'depth': depth,
            'results': normalized_results,
        }
