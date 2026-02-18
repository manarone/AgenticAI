from libs.common.llm import ToolExecutionRecord
from services.coordinator.main import _collect_web_failure_notice, _ensure_sources_section


def test_ensure_sources_section_does_not_duplicate_inline_sources_header():
    text = 'Answer text\nSources: [Existing](https://existing.example)'
    updated = _ensure_sources_section(text, [('New Source', 'https://new.example')])
    assert updated == text


def test_ensure_sources_section_appends_when_sources_header_is_only_in_code_block():
    text = "Answer text\n```md\nSources: [Example](https://example.com)\n```"
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
