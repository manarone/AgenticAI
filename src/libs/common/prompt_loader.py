from __future__ import annotations

import logging
from pathlib import Path

DEFAULT_PROMPT = 'You are the AgentAI coordinator. Keep responses concise and useful.'
PROMPT_FILENAMES = ('AGENTS.md', 'SKILLS.md', 'TOOLS.md')
logger = logging.getLogger(__name__)


def prompt_dir() -> Path:
    return Path(__file__).resolve().parents[2] / 'prompts'


def load_runtime_prompt() -> str:
    base = prompt_dir()
    sections: list[str] = []

    for filename in PROMPT_FILENAMES:
        path = base / filename
        if not path.exists():
            logger.warning('Runtime prompt file missing: %s', path)
            continue
        content = path.read_text(encoding='utf-8').strip()
        if not content:
            logger.warning('Runtime prompt file empty: %s', path)
            continue
        sections.append(content)

    if not sections:
        logger.warning('No runtime prompt files loaded from %s; falling back to default prompt.', base)
        return DEFAULT_PROMPT
    return '\n\n'.join(sections)
