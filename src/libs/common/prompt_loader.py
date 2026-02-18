from __future__ import annotations

import logging
from pathlib import Path

from libs.common.config import Settings, get_settings

DEFAULT_PROMPT = 'You are the AgentAI coordinator. Keep responses concise and useful.'
PROMPT_FILENAMES = ('AGENTS.md', 'SKILLS.md', 'TOOLS.md')
logger = logging.getLogger(__name__)


def prompt_dir_candidates(settings: Settings) -> list[Path]:
    candidates: list[Path] = []
    if settings.prompt_dir.strip():
        candidates.append(Path(settings.prompt_dir).expanduser())
    candidates.append(Path('/app/src/prompts'))
    candidates.append(Path(__file__).resolve().parents[2] / 'prompts')

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def load_runtime_prompt(settings: Settings | None = None) -> str:
    resolved_settings = settings or get_settings()
    candidates = prompt_dir_candidates(resolved_settings)

    for base in candidates:
        sections: list[str] = []
        loaded_files: list[str] = []
        for filename in PROMPT_FILENAMES:
            path = base / filename
            if not path.exists():
                continue
            content = path.read_text(encoding='utf-8').strip()
            if not content:
                logger.warning('Runtime prompt file empty: %s', path)
                continue
            sections.append(content)
            loaded_files.append(filename)
        if sections:
            logger.info('Loaded runtime prompt from %s with files: %s', base, ', '.join(loaded_files))
            return '\n\n'.join(sections)

    logger.warning(
        'No runtime prompt files loaded from candidates=%s; falling back to default prompt.',
        ', '.join(str(path) for path in candidates),
    )
    return DEFAULT_PROMPT
