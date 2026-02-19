import pytest

from libs.common.tool_registry import build_tool_registry


async def _web_handler(args):
    return {'ok': True}


async def _browser_handler(args):
    return {'ok': True, 'action': args.get('action')}


def _tool_names(registry) -> set[str]:
    return {entry['function']['name'] for entry in registry.schemas()}


def test_browser_tools_not_registered_when_disabled():
    registry = build_tool_registry(
        web_search_handler=_web_handler,
        web_search_enabled=True,
        browser_handler=_browser_handler,
        browser_enabled=False,
    )
    names = _tool_names(registry)
    assert 'web_search' in names
    assert 'browser_open' not in names


def test_browser_tools_registered_when_enabled():
    registry = build_tool_registry(
        web_search_handler=_web_handler,
        web_search_enabled=True,
        browser_handler=_browser_handler,
        browser_enabled=True,
    )
    names = _tool_names(registry)
    assert 'web_search' in names
    assert 'browser_open' in names
    assert 'browser_click' in names


@pytest.mark.asyncio
async def test_browser_tool_handler_injects_action():
    registry = build_tool_registry(
        web_search_handler=_web_handler,
        web_search_enabled=False,
        browser_handler=_browser_handler,
        browser_enabled=True,
    )
    result = await registry.execute('browser_open', {'url': 'https://example.com'})
    assert result['ok'] is True
    assert result['action'] == 'open'
