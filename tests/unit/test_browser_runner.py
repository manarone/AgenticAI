import asyncio
from pathlib import Path

import pytest

from libs.common import browser_runner


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: str = '', stderr: str = '') -> None:
        self.returncode = returncode
        self._stdout = stdout.encode('utf-8')
        self._stderr = stderr.encode('utf-8')
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_browser_runner_success(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=0, stdout='{"text":"ok"}')

    monkeypatch.setattr(browser_runner.settings, 'agent_browser_bin', 'agent-browser')
    monkeypatch.setattr(browser_runner.asyncio, 'create_subprocess_exec', fake_exec)

    result = await browser_runner.run_browser_action('open', {'url': 'https://example.com'}, session_id='abc')
    assert result['ok'] is True
    assert result['action'] == 'open'
    assert result['summary'] == 'ok'


@pytest.mark.asyncio
async def test_browser_runner_invalid_json(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=0, stdout='not-json')

    monkeypatch.setattr(browser_runner.asyncio, 'create_subprocess_exec', fake_exec)
    result = await browser_runner.run_browser_action('snapshot', {})
    assert result['ok'] is False
    assert 'non-JSON' in result['error']


@pytest.mark.asyncio
async def test_browser_runner_non_zero_exit(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=1, stderr='failed')

    monkeypatch.setattr(browser_runner.asyncio, 'create_subprocess_exec', fake_exec)
    result = await browser_runner.run_browser_action('close', {})
    assert result['ok'] is False
    assert result['error'] == 'failed'


@pytest.mark.asyncio
async def test_browser_runner_timeout(monkeypatch):
    class _SlowProcess(_FakeProcess):
        async def communicate(self):  # pragma: no cover - covered by timeout path
            await asyncio.sleep(2)
            return await super().communicate()

    async def fake_exec(*args, **kwargs):
        return _SlowProcess(returncode=0, stdout='{}')

    monkeypatch.setattr(browser_runner.settings, 'browser_timeout_seconds', 1)
    monkeypatch.setattr(browser_runner.asyncio, 'create_subprocess_exec', fake_exec)
    result = await browser_runner.run_browser_action('snapshot', {})
    assert result['ok'] is False
    assert 'timed out' in result['error']


@pytest.mark.asyncio
async def test_browser_runner_collects_screenshot_artifact(monkeypatch, tmp_path: Path):
    shot = tmp_path / 'shot.png'
    shot.write_bytes(b'png')

    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=0, stdout='{"path":"' + str(shot) + '"}')

    monkeypatch.setattr(browser_runner.asyncio, 'create_subprocess_exec', fake_exec)
    result = await browser_runner.run_browser_action('screenshot', {})
    assert result['ok'] is True
    assert result['artifacts'] == [{'path': str(shot)}]
