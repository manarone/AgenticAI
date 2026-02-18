from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


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
    return registry
