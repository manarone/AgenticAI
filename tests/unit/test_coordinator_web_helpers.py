from libs.common.llm import ToolExecutionRecord
from services.coordinator.main import _collect_web_failure_notice, _ensure_sources_section, _shell_approval_message


def test_ensure_sources_section_does_not_duplicate_inline_sources_header():
    text = 'Answer text\nSources: [Existing](https://existing.example)'
    updated = _ensure_sources_section(text, [('New Source', 'https://new.example')])
    assert updated == text


def test_ensure_sources_section_appends_when_sources_header_is_only_in_code_block():
    text = "Answer text\n```md\nSources: [Example](https://example.com)\n```"
    updated = _ensure_sources_section(text, [('New Source', 'https://new.example')])
    assert updated.count('Sources:') == 2
    assert '- [New Source](https://new.example)' in updated


def test_ensure_sources_section_appends_when_sources_header_is_only_in_tilde_code_block():
    text = "Answer text\n~~~md\nSources: [Example](https://example.com)\n~~~"
    updated = _ensure_sources_section(text, [('New Source', 'https://new.example')])
    assert updated.count('Sources:') == 2
    assert '- [New Source](https://new.example)' in updated


def test_collect_web_failure_notice_treats_missing_ok_as_failure():
    records = [
        ToolExecutionRecord(
            name='web_search',
            result={'user_notice': 'Live web search is currently unavailable.'},
        )
    ]
    assert _collect_web_failure_notice(records) == 'Live web search is currently unavailable.'


def test_shell_approval_message_escapes_code_delimiters():
    text = _shell_approval_message('12345678-1234', {'command': 'echo `whoami` && cat /tmp/a'})
    assert 'echo \\`whoami\\` && cat /tmp/a' in text


def test_shell_approval_message_escapes_remote_host_for_markdown():
    text = _shell_approval_message(
        '12345678-1234',
        {'command': 'ls -la', 'remote_host': 'db_node[01]'},
    )
    assert 'on db\\_node\\[01\\]' in text
