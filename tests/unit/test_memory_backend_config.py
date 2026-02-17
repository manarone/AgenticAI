import sys
import types

import pytest

from libs.common.config import get_settings
from libs.common.memory import Mem0LocalMemoryStore


class _DummyClient:
    def add(self, **kwargs):
        return None

    def search(self, **kwargs):
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
    monkeypatch.setenv('OPENAI_API_KEY', '')
    get_settings.cache_clear()

    Mem0LocalMemoryStore()

    assert _DummyMemoryClass.last_config['embedder']['provider'] == 'fastembed'


def test_mem0_local_openai_embedder_requires_openai_key(monkeypatch):
    _install_mem0_stub(monkeypatch)
    monkeypatch.setenv('MEM0_EMBEDDER_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    get_settings.cache_clear()

    with pytest.raises(ValueError, match='MEM0_EMBEDDER_PROVIDER=openai'):
        Mem0LocalMemoryStore()
