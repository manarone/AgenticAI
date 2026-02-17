from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Protocol

from libs.common.config import get_settings

logger = logging.getLogger(__name__)


class MemoryBackend(Protocol):
    async def remember(self, tenant_id: str, user_id: str, content: str) -> None: ...

    async def recall(self, tenant_id: str, user_id: str, query: str, limit: int = 5) -> list[str]: ...


class LocalMemoryStore:
    def __init__(self) -> None:
        self._store: dict[str, list[str]] = defaultdict(list)

    async def remember(self, tenant_id: str, user_id: str, content: str) -> None:
        self._store[f'{tenant_id}:{user_id}'].append(content)

    async def recall(self, tenant_id: str, user_id: str, query: str, limit: int = 5) -> list[str]:
        data = self._store.get(f'{tenant_id}:{user_id}', [])
        return data[-limit:]


class Mem0ApiMemoryStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.user_prefix = settings.mem0_user_prefix

        if not settings.mem0_api_key:
            raise ValueError('MEM0_API_KEY is required when MEMORY_BACKEND=mem0_api')

        from mem0 import AsyncMemoryClient

        kwargs: dict[str, str] = {'api_key': settings.mem0_api_key}
        if settings.mem0_org_id:
            kwargs['org_id'] = settings.mem0_org_id
        if settings.mem0_project_id:
            kwargs['project_id'] = settings.mem0_project_id

        self.client = AsyncMemoryClient(**kwargs)

    def _mem0_user_id(self, tenant_id: str, user_id: str) -> str:
        return f'{self.user_prefix}:{tenant_id}:{user_id}'

    async def remember(self, tenant_id: str, user_id: str, content: str) -> None:
        mem0_user_id = self._mem0_user_id(tenant_id, user_id)
        await self.client.add(
            messages=[{'role': 'user', 'content': content}],
            user_id=mem0_user_id,
            metadata={'tenant_id': tenant_id, 'user_id': user_id},
        )

    async def recall(self, tenant_id: str, user_id: str, query: str, limit: int = 5) -> list[str]:
        mem0_user_id = self._mem0_user_id(tenant_id, user_id)
        result = await self.client.search(query=query, user_id=mem0_user_id, limit=limit)
        rows = result if isinstance(result, list) else result.get('results', [])

        memories: list[str] = []
        for row in rows:
            memory_text = row.get('memory') or row.get('text') or row.get('content')
            if memory_text:
                memories.append(str(memory_text))
        return memories[:limit]


class Mem0LocalMemoryStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.user_prefix = settings.mem0_user_prefix

        if not settings.openai_api_key:
            raise ValueError('OPENAI_API_KEY is required when MEMORY_BACKEND=mem0_local')

        from mem0 import Memory

        config: dict[str, Any] = {
            'vector_store': {
                'provider': 'qdrant',
                'config': {
                    'host': settings.mem0_qdrant_host,
                    'port': settings.mem0_qdrant_port,
                    'collection_name': settings.mem0_qdrant_collection,
                    'embedding_model_dims': settings.mem0_embedding_dims,
                },
            },
            'llm': {
                'provider': 'openai',
                'config': {
                    'api_key': settings.openai_api_key,
                    'openai_base_url': settings.openai_base_url,
                    'model': settings.mem0_llm_model,
                },
            },
            'embedder': {
                'provider': 'openai',
                'config': {
                    'api_key': settings.openai_api_key,
                    'openai_base_url': settings.openai_base_url,
                    'model': settings.mem0_embedding_model,
                    'embedding_dims': settings.mem0_embedding_dims,
                },
            },
            'history_db_path': settings.mem0_history_db_path,
            'version': 'v1.1',
        }

        self.client = Memory.from_config(config)

    def _mem0_user_id(self, tenant_id: str, user_id: str) -> str:
        return f'{self.user_prefix}:{tenant_id}:{user_id}'

    async def remember(self, tenant_id: str, user_id: str, content: str) -> None:
        mem0_user_id = self._mem0_user_id(tenant_id, user_id)

        def _add() -> None:
            self.client.add(
                messages=[{'role': 'user', 'content': content}],
                user_id=mem0_user_id,
                metadata={'tenant_id': tenant_id, 'user_id': user_id},
                infer=False,
            )

        await asyncio.to_thread(_add)

    async def recall(self, tenant_id: str, user_id: str, query: str, limit: int = 5) -> list[str]:
        mem0_user_id = self._mem0_user_id(tenant_id, user_id)

        def _search() -> Any:
            return self.client.search(query=query, user_id=mem0_user_id, limit=limit, rerank=False)

        result = await asyncio.to_thread(_search)
        rows = result if isinstance(result, list) else result.get('results', [])

        memories: list[str] = []
        for row in rows:
            memory_text = row.get('memory') or row.get('text') or row.get('content')
            if memory_text:
                memories.append(str(memory_text))
        return memories[:limit]


_MEMORY_BACKEND: MemoryBackend | None = None


def get_memory_backend() -> MemoryBackend:
    global _MEMORY_BACKEND
    if _MEMORY_BACKEND is not None:
        return _MEMORY_BACKEND

    settings = get_settings()
    backend = settings.memory_backend.lower().strip()

    try:
        if backend == 'mem0_api':
            _MEMORY_BACKEND = Mem0ApiMemoryStore()
        elif backend == 'mem0_local':
            _MEMORY_BACKEND = Mem0LocalMemoryStore()
        elif backend == 'mem0':
            # Backward compatibility: interpret old `mem0` as API mode.
            _MEMORY_BACKEND = Mem0ApiMemoryStore()
        else:
            _MEMORY_BACKEND = LocalMemoryStore()
    except Exception:
        # Keep service available even if external memory backend is misconfigured.
        logger.exception('Failed to initialize memory backend %s, falling back to local', backend)
        _MEMORY_BACKEND = LocalMemoryStore()

    return _MEMORY_BACKEND
