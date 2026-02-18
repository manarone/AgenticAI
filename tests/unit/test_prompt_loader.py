from pathlib import Path

from libs.common import prompt_loader


def test_load_runtime_prompt_combines_sections(tmp_path, monkeypatch):
    (tmp_path / 'AGENTS.md').write_text('Agent prompt', encoding='utf-8')
    (tmp_path / 'SKILLS.md').write_text('Skill prompt', encoding='utf-8')
    (tmp_path / 'TOOLS.md').write_text('Tool prompt', encoding='utf-8')

    monkeypatch.setattr(prompt_loader, 'prompt_dir_candidates', lambda _settings: [Path(tmp_path)])

    loaded = prompt_loader.load_runtime_prompt()
    assert loaded == 'Agent prompt\n\nSkill prompt\n\nTool prompt'


def test_load_runtime_prompt_handles_missing_files(tmp_path, monkeypatch):
    (tmp_path / 'AGENTS.md').write_text('Agent prompt', encoding='utf-8')
    monkeypatch.setattr(prompt_loader, 'prompt_dir_candidates', lambda _settings: [Path(tmp_path)])

    loaded = prompt_loader.load_runtime_prompt()
    assert loaded == 'Agent prompt'
