import sys
import types

import pytest

from libs.common.config import get_settings
from libs.common.memory import Mem0LocalMemoryStore


class _DummyClient:
    last_add = None
    last_search = None

    def add(self, **kwargs):
        self.__class__.last_add = kwargs
        return None

    def search(self, **kwargs):
        self.__class__.last_search = kwargs
        return {'results': []}


class _DummyMemoryClass:
    last_config = None

    @classmethod
    def from_config(cls, config):
        cls.last_config = config
        return _DummyClient()


def _install_mem0_stub(monkeypatch):
    stub = types.ModuleType('mem0')
    stub.Memory = _DummyMemoryClass
    monkeypatch.setitem(sys.modules, 'mem0', stub)


def test_mem0_local_fastembed_does_not_require_openai_key(monkeypatch):
    _install_mem0_stub(monkeypatch)
    monkeypatch.setenv('MEM0_EMBEDDER_PROVIDER', 'fastembed')
    monkeypatch.setenv('MEM0_LLM_PROVIDER', 'lmstudio')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    get_settings.cache_clear()

    Mem0LocalMemoryStore()

    assert _DummyMemoryClass.last_config['embedder']['provider'] == 'fastembed'
    assert _DummyMemoryClass.last_config['llm']['provider'] == 'lmstudio'
    assert _DummyMemoryClass.last_config['llm']['config']['base_url'] == 'http://localhost:1234/v1'
    assert _DummyMemoryClass.last_config['llm']['config']['openai_base_url'] == 'http://localhost:1234/v1'
    assert 'api_key' not in _DummyMemoryClass.last_config['llm']['config']


def test_mem0_local_openai_embedder_requires_openai_key(monkeypatch):
    _install_mem0_stub(monkeypatch)
    monkeypatch.setenv('MEM0_EMBEDDER_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    get_settings.cache_clear()

    with pytest.raises(ValueError, match='MEM0_EMBEDDER_PROVIDER=openai'):
        Mem0LocalMemoryStore()


def test_mem0_local_openai_llm_requires_openai_key(monkeypatch):
    _install_mem0_stub(monkeypatch)
    monkeypatch.setenv('MEM0_EMBEDDER_PROVIDER', 'fastembed')
    monkeypatch.setenv('MEM0_LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    get_settings.cache_clear()

    with pytest.raises(ValueError, match='MEM0_LLM_PROVIDER=openai'):
        Mem0LocalMemoryStore()


def test_mem0_local_openai_embedder_uses_openai_key_when_set(monkeypatch):
    _install_mem0_stub(monkeypatch)
    monkeypatch.setenv('MEM0_EMBEDDER_PROVIDER', 'openai')
    monkeypatch.setenv('MEM0_LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    get_settings.cache_clear()

    Mem0LocalMemoryStore()

    assert _DummyMemoryClass.last_config['llm']['provider'] == 'openai'
    assert _DummyMemoryClass.last_config['llm']['config']['api_key'] == 'test-key'


@pytest.mark.asyncio
async def test_mem0_local_read_write_disable_llm_infer_and_rerank(monkeypatch):
    _install_mem0_stub(monkeypatch)
    monkeypatch.setenv('MEM0_EMBEDDER_PROVIDER', 'fastembed')
    monkeypatch.setenv('MEM0_LLM_PROVIDER', 'lmstudio')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    get_settings.cache_clear()

    store = Mem0LocalMemoryStore()
    await store.remember(tenant_id='t1', user_id='u1', content='hello')
    await store.recall(tenant_id='t1', user_id='u1', query='hello')

    assert _DummyClient.last_add['infer'] is False
    assert _DummyClient.last_search['rerank'] is False
