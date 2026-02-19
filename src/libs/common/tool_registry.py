from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


ToolHandler = Callable[[dict], Awaitable[dict]]


@dataclass
class ToolDefinition:
    name: str
    schema: dict
    handler: ToolHandler

    def __post_init__(self) -> None:
        if not callable(self.handler):
            raise TypeError(f'Invalid handler for tool {self.name}: not callable')


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def schemas(self) -> list[dict]:
        return [definition.schema for definition in self._tools.values()]

    async def execute(self, name: str, args: dict) -> dict:
        definition = self._tools.get(name)
        if definition is None:
            return {'ok': False, 'error': f'Unsupported tool: {name}'}
        return await definition.handler(args)


def build_default_tool_registry(web_search_handler: ToolHandler, *, web_search_enabled: bool) -> ToolRegistry:
    return build_tool_registry(
        web_search_handler=web_search_handler,
        web_search_enabled=web_search_enabled,
        browser_handler=None,
        browser_enabled=False,
    )


def _browser_handler_with_action(browser_handler: ToolHandler, action: str) -> ToolHandler:
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        payload = dict(args or {})
        payload['action'] = action
        return await browser_handler(payload)

    return _handler


def _register_browser_tools(registry: ToolRegistry, browser_handler: ToolHandler) -> None:
    # Intentionally does not expose browser_run/eval to the LLM tool surface in MVP.
    browser_tools = [
        (
            'browser_open',
            'Open a webpage URL in the browser.',
            {
                'type': 'object',
                'properties': {'url': {'type': 'string', 'description': 'URL to open.'}},
                'required': ['url'],
                'additionalProperties': False,
            },
            'open',
        ),
        (
            'browser_snapshot',
            'Capture an accessibility snapshot of the current page.',
            {
                'type': 'object',
                'properties': {
                    'interactive': {
                        'type': 'boolean',
                        'description': 'When true, snapshot focuses on interactive elements.',
                    }
                },
                'additionalProperties': False,
            },
            'snapshot',
        ),
        (
            'browser_get_text',
            'Get text content from an element.',
            {
                'type': 'object',
                'properties': {
                    'selector': {'type': 'string', 'description': 'CSS selector or element ref.'},
                    'ref': {'type': 'string', 'description': 'Accessibility ref from snapshot output.'},
                },
                'anyOf': [{'required': ['selector']}, {'required': ['ref']}],
                'additionalProperties': False,
            },
            'get_text',
        ),
        (
            'browser_screenshot',
            'Take a screenshot of the current page and return/send it.',
            {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': 'Optional output path for screenshot file.'},
                    'filename': {'type': 'string', 'description': 'Optional filename alias.'},
                },
                'additionalProperties': False,
            },
            'screenshot',
        ),
        (
            'browser_wait_for',
            'Wait for a selector, text, URL, or milliseconds.',
            {
                'type': 'object',
                'properties': {
                    'selector': {'type': 'string', 'description': 'Selector/ref to wait for.'},
                    'ref': {'type': 'string', 'description': 'Element ref to wait for.'},
                    'text': {'type': 'string', 'description': 'Text to wait for.'},
                    'url': {'type': 'string', 'description': 'URL pattern to wait for.'},
                    'milliseconds': {'type': 'integer', 'minimum': 1, 'description': 'Explicit wait duration.'},
                },
                'additionalProperties': False,
            },
            'wait_for',
        ),
        (
            'browser_close',
            'Close the current browser session.',
            {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'close',
        ),
        (
            'browser_click',
            'Click an element.',
            {
                'type': 'object',
                'properties': {
                    'selector': {'type': 'string', 'description': 'CSS selector or element ref.'},
                    'ref': {'type': 'string', 'description': 'Accessibility ref from snapshot output.'},
                },
                'anyOf': [{'required': ['selector']}, {'required': ['ref']}],
                'additionalProperties': False,
            },
            'click',
        ),
        (
            'browser_type',
            'Type text into an input element.',
            {
                'type': 'object',
                'properties': {
                    'selector': {'type': 'string', 'description': 'CSS selector or element ref.'},
                    'ref': {'type': 'string', 'description': 'Accessibility ref from snapshot output.'},
                    'text': {'type': 'string', 'description': 'Text to type.'},
                },
                'required': ['text'],
                'anyOf': [{'required': ['selector']}, {'required': ['ref']}],
                'additionalProperties': False,
            },
            'type',
        ),
        (
            'browser_fill',
            'Clear and fill an input element with text.',
            {
                'type': 'object',
                'properties': {
                    'selector': {'type': 'string', 'description': 'CSS selector or element ref.'},
                    'ref': {'type': 'string', 'description': 'Accessibility ref from snapshot output.'},
                    'text': {'type': 'string', 'description': 'Text to fill.'},
                },
                'required': ['text'],
                'anyOf': [{'required': ['selector']}, {'required': ['ref']}],
                'additionalProperties': False,
            },
            'fill',
        ),
    ]
    for name, description, parameters, action in browser_tools:
        registry.register(
            ToolDefinition(
                name=name,
                schema={
                    'type': 'function',
                    'function': {
                        'name': name,
                        'description': description,
                        'parameters': parameters,
                    },
                },
                handler=_browser_handler_with_action(browser_handler, action),
            )
        )


def build_tool_registry(
    *,
    web_search_handler: ToolHandler,
    web_search_enabled: bool,
    browser_handler: ToolHandler | None,
    browser_enabled: bool,
) -> ToolRegistry:
    registry = ToolRegistry()
    if web_search_enabled:
        registry.register(
            ToolDefinition(
                name='web_search',
                schema={
                    'type': 'function',
                    'function': {
                        'name': 'web_search',
                        'description': 'Search the public web for recent information.',
                        'parameters': {
                            'type': 'object',
                            'properties': {
                                'query': {'type': 'string', 'description': 'Search query text.'},
                                'depth': {
                                    'type': 'string',
                                    'enum': ['balanced', 'deep'],
                                    'description': 'Search depth hint.',
                                },
                                'max_results': {
                                    'type': 'integer',
                                    'minimum': 1,
                                    'maximum': 10,
                                    'description': 'Maximum number of results to return.',
                                },
                            },
                            'required': ['query'],
                            'additionalProperties': False,
                        },
                    },
                },
                handler=web_search_handler,
            )
        )
    if browser_enabled and browser_handler is not None:
        _register_browser_tools(registry, browser_handler)
    return registry
