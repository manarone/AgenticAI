from pathlib import Path
import subprocess

import pytest

from libs.common import browser_runner


def _fake_exec_result(returncode: int, stdout: str = '', stderr: str = '', timed_out: bool = False):
    return returncode, stdout.encode('utf-8'), stderr.encode('utf-8'), timed_out


def test_run_browser_command_timeout_branch(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='agent-browser', timeout=1, output=b'partial-out', stderr=b'partial-err')

    monkeypatch.setattr(browser_runner.subprocess, 'run', fake_run)
    returncode, out, err, timed_out = browser_runner._run_browser_command(['agent-browser', 'snapshot', '--json'])
    assert returncode == -1
    assert timed_out is True
    assert out == b'partial-out'
    assert err == b'partial-err'


@pytest.mark.asyncio
async def test_browser_runner_success(monkeypatch):
    monkeypatch.setattr(browser_runner.settings, 'agent_browser_bin', 'agent-browser')
    monkeypatch.setattr(
        browser_runner,
        '_run_browser_command',
        lambda _command: _fake_exec_result(0, '{"text":"ok"}'),
    )

    result = await browser_runner.run_browser_action('open', {'url': 'https://example.com'}, session_id='abc')
    assert result['ok'] is True
    assert result['action'] == 'open'
    assert result['summary'] == 'ok'


@pytest.mark.asyncio
async def test_browser_runner_invalid_json(monkeypatch):
    monkeypatch.setattr(
        browser_runner,
        '_run_browser_command',
        lambda _command: _fake_exec_result(0, 'not-json'),
    )
    result = await browser_runner.run_browser_action('snapshot', {})
    assert result['ok'] is False
    assert 'non-JSON' in result['error']


@pytest.mark.asyncio
async def test_browser_runner_non_zero_exit(monkeypatch):
    monkeypatch.setattr(
        browser_runner,
        '_run_browser_command',
        lambda _command: _fake_exec_result(1, '', 'failed'),
    )
    result = await browser_runner.run_browser_action('close', {})
    assert result['ok'] is False
    assert result['error'] == 'failed'


@pytest.mark.asyncio
async def test_browser_runner_timeout(monkeypatch):
    monkeypatch.setattr(
        browser_runner,
        '_run_browser_command',
        lambda _command: _fake_exec_result(0, '{}', '', timed_out=True),
    )
    result = await browser_runner.run_browser_action('snapshot', {})
    assert result['ok'] is False
    assert 'timed out' in result['error']


@pytest.mark.asyncio
async def test_browser_runner_binary_missing(monkeypatch):
    def raise_missing(_command):
        raise FileNotFoundError

    monkeypatch.setattr(browser_runner, '_run_browser_command', raise_missing)
    monkeypatch.setattr(browser_runner.settings, 'agent_browser_bin', 'missing-browser-bin')
    result = await browser_runner.run_browser_action('snapshot', {})
    assert result['ok'] is False
    assert 'not found' in result['error']


@pytest.mark.asyncio
async def test_browser_runner_collects_screenshot_artifact(monkeypatch, tmp_path: Path):
    shot = tmp_path / 'shot.png'
    shot.write_bytes(b'png')

    monkeypatch.setattr(
        browser_runner,
        '_run_browser_command',
        lambda _command: _fake_exec_result(0, '{"path":"' + str(shot) + '"}'),
    )
    result = await browser_runner.run_browser_action('screenshot', {})
    assert result['ok'] is True
    assert result['artifacts'] == [{'path': str(shot)}]


@pytest.mark.asyncio
async def test_browser_runner_rejects_empty_fill_text():
    result = await browser_runner.run_browser_action('fill', {'selector': '#email', 'text': '   '})
    assert result['ok'] is False
    assert 'non-empty `text`' in result['error']
