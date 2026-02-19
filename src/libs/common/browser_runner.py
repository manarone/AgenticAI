from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from libs.common.browser_policy import normalize_browser_action
from libs.common.config import get_settings

settings = get_settings()


def _as_non_empty(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _selector_from_args(args: dict[str, Any]) -> str | None:
    return (
        _as_non_empty(args.get('selector'))
        or _as_non_empty(args.get('ref'))
        or _as_non_empty(args.get('target'))
        or _as_non_empty(args.get('element'))
    )


def _build_action_argv(action: str, args: dict[str, Any]) -> list[str]:
    normalized = normalize_browser_action(action)
    if normalized == 'open':
        url = _as_non_empty(args.get('url')) or _as_non_empty(args.get('target'))
        if not url:
            raise ValueError('browser_open requires `url`.')
        return ['open', url]

    if normalized == 'snapshot':
        argv = ['snapshot']
        if bool(args.get('interactive', False)):
            argv.append('-i')
        return argv

    if normalized == 'get_text':
        selector = _selector_from_args(args)
        if not selector:
            raise ValueError('browser_get_text requires `selector` or `ref`.')
        return ['get', 'text', selector]

    if normalized == 'screenshot':
        path = _as_non_empty(args.get('path')) or _as_non_empty(args.get('filename'))
        return ['screenshot', path] if path else ['screenshot']

    if normalized == 'wait_for':
        wait_ms = args.get('milliseconds')
        if isinstance(wait_ms, int) and wait_ms > 0:
            return ['wait', str(wait_ms)]
        text = _as_non_empty(args.get('text'))
        if text:
            return ['wait', '--text', text]
        url = _as_non_empty(args.get('url'))
        if url:
            return ['wait', '--url', url]
        selector = _selector_from_args(args)
        if selector:
            return ['wait', selector]
        raise ValueError('browser_wait_for requires `selector`, `text`, `url`, or `milliseconds`.')

    if normalized == 'close':
        return ['close']

    if normalized == 'click':
        selector = _selector_from_args(args)
        if not selector:
            raise ValueError('browser_click requires `selector` or `ref`.')
        return ['click', selector]

    if normalized == 'type':
        selector = _selector_from_args(args)
        text = _as_non_empty(args.get('text'))
        if not selector or text is None or not text:
            raise ValueError('browser_type requires `selector`/`ref` and non-empty `text`.')
        return ['type', selector, text]

    if normalized == 'fill':
        selector = _selector_from_args(args)
        text = _as_non_empty(args.get('text'))
        if not selector or text is None or not text:
            raise ValueError('browser_fill requires `selector`/`ref` and non-empty `text`.')
        return ['fill', selector, text]

    if normalized == 'run':
        command = _as_non_empty(args.get('command'))
        if not command:
            raise ValueError('browser_run requires `command`.')
        return ['eval', command]

    raise ValueError(f'Unsupported browser action: {normalized}')


def _extract_artifact_paths(data: Any) -> list[str]:
    found: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.lower() in {'path', 'file', 'screenshot_path'} and isinstance(nested, str):
                    if nested.strip():
                        found.append(nested.strip())
                _walk(nested)
        elif isinstance(value, list):
            for nested in value:
                _walk(nested)

    _walk(data)
    deduped: list[str] = []
    seen: set[str] = set()
    for path in found:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _run_browser_command(command: list[str]) -> tuple[int, bytes, bytes, bool]:
    # May raise FileNotFoundError when settings.agent_browser_bin is unavailable.
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=settings.browser_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return -1, exc.stdout or b'', exc.stderr or b'', True

    return completed.returncode, completed.stdout or b'', completed.stderr or b'', False


async def run_browser_action(
    action: str,
    args: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    args = args or {}
    normalized = normalize_browser_action(action)
    try:
        action_argv = _build_action_argv(normalized, args)
    except ValueError as exc:
        return {'ok': False, 'action': normalized, 'error': str(exc), 'session_id': session_id}

    command = [settings.agent_browser_bin]
    if session_id:
        command.extend(['--session', session_id])
    command.extend(action_argv)
    command.append('--json')

    try:
        returncode, out, err, timed_out = await asyncio.to_thread(_run_browser_command, command)
    except FileNotFoundError:
        return {
            'ok': False,
            'action': normalized,
            'error': f'browser binary not found: {settings.agent_browser_bin}',
            'session_id': session_id,
        }

    if timed_out:
        return {
            'ok': False,
            'action': normalized,
            'error': 'browser command timed out',
            'session_id': session_id,
        }

    stdout_text = out.decode('utf-8', errors='ignore').strip()
    stderr_text = err.decode('utf-8', errors='ignore').strip()
    clipped_stderr = stderr_text[: settings.browser_max_output_chars]
    clipped_stdout = stdout_text[: settings.browser_max_output_chars]

    if returncode != 0:
        return {
            'ok': False,
            'action': normalized,
            'error': clipped_stderr or f'agent-browser exited with code {returncode}',
            'session_id': session_id,
        }

    try:
        parsed = json.loads(stdout_text) if stdout_text else {}
    except json.JSONDecodeError:
        return {
            'ok': False,
            'action': normalized,
            'error': 'agent-browser returned non-JSON output',
            'session_id': session_id,
            'raw_output': clipped_stdout,
        }

    data: dict[str, Any]
    if isinstance(parsed, dict):
        data = parsed
    else:
        data = {'value': parsed}

    artifacts = _extract_artifact_paths(data)
    if normalized == 'screenshot':
        configured_path = _as_non_empty(args.get('path')) or _as_non_empty(args.get('filename'))
        if configured_path:
            artifacts = [configured_path, *[path for path in artifacts if path != configured_path]]

    existing_artifacts: list[dict[str, str]] = []
    for artifact_path in artifacts:
        resolved = Path(artifact_path).expanduser()
        if resolved.exists():
            existing_artifacts.append({'path': str(resolved)})

    summary = clipped_stdout
    if isinstance(data.get('text'), str):
        summary = data['text'][: settings.browser_max_output_chars]
    elif isinstance(data.get('value'), str):
        summary = data['value'][: settings.browser_max_output_chars]

    return {
        'ok': True,
        'action': normalized,
        'session_id': session_id,
        'data': data,
        'summary': summary,
        'artifacts': existing_artifacts,
    }
