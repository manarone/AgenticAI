from __future__ import annotations

from enum import Enum


class BrowserActionClass(str, Enum):
    READ_ONLY = 'read_only'
    MUTATING = 'mutating'
    UNSUPPORTED = 'unsupported'


READ_ONLY_ACTIONS = frozenset({'open', 'snapshot', 'get_text', 'screenshot', 'wait_for', 'close'})
MUTATING_ACTIONS = frozenset({'click', 'type', 'fill', 'run'})


def normalize_browser_action(action: str) -> str:
    normalized = (action or '').strip().lower()
    if normalized.startswith('browser_'):
        normalized = normalized[len('browser_') :]
    return normalized


def classify_browser_action(action: str) -> BrowserActionClass:
    normalized = normalize_browser_action(action)
    if normalized in READ_ONLY_ACTIONS:
        return BrowserActionClass.READ_ONLY
    if normalized in MUTATING_ACTIONS:
        return BrowserActionClass.MUTATING
    return BrowserActionClass.UNSUPPORTED


def is_browser_mutating(action: str) -> bool:
    return classify_browser_action(action) == BrowserActionClass.MUTATING

