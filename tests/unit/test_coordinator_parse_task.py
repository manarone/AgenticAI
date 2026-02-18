from libs.common.enums import TaskType
from services.coordinator.main import _parse_task


def test_parse_shell_remote_host_with_port():
    task_type, payload = _parse_task('shell@example-host:2222:uname -a')
    assert task_type == TaskType.SHELL
    assert payload['remote_host'] == 'example-host:2222'
    assert payload['command'] == 'uname -a'


def test_parse_shell_remote_ipv6_preserves_command():
    task_type, payload = _parse_task('shell@2001:db8::1:uname -a')
    assert task_type == TaskType.SHELL
    assert payload['remote_host'] == '2001:db8::1'
    assert payload['command'] == 'uname -a'


def test_parse_shell_remote_command_with_colon_not_truncated():
    task_type, payload = _parse_task('shell@example-host:echo key:value')
    assert task_type == TaskType.SHELL
    assert payload['remote_host'] == 'example-host'
    assert payload['command'] == 'echo key:value'


def test_parse_shell_remote_no_space_colon_command_not_truncated():
    task_type, payload = _parse_task('shell@example-host:echo:key:value')
    assert task_type == TaskType.SHELL
    assert payload['remote_host'] == 'example-host'
    assert payload['command'] == 'echo:key:value'


def test_parse_web_command():
    task_type, payload = _parse_task('web: latest ai news')
    assert task_type == TaskType.WEB
    assert payload['query'] == 'latest ai news'


def test_parse_shell_remote_invalid_target_sets_parse_error():
    task_type, payload = _parse_task('shell@not-a-valid-remote-target')
    assert task_type == TaskType.SHELL
    assert payload['remote_parse_error'] is True


def test_parse_shell_remote_invalid_host_chars_sets_parse_error():
    task_type, payload = _parse_task('shell@./badhost:uname -a')
    assert task_type == TaskType.SHELL
    assert payload['remote_parse_error'] is True
