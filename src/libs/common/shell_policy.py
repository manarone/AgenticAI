from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum


class ShellPolicyDecision(str, Enum):
    ALLOW_AUTORUN = 'ALLOW_AUTORUN'
    REQUIRE_APPROVAL = 'REQUIRE_APPROVAL'
    BLOCKED = 'BLOCKED'


@dataclass(frozen=True)
class ShellPolicyResult:
    decision: ShellPolicyDecision
    reason: str


_READ_ONLY_COMMANDS = {
    'ls',
    'pwd',
    'cat',
    'head',
    'tail',
    'rg',
    'grep',
    'find',
    'stat',
    'df',
    'du',
    'ps',
    'uname',
    'id',
    'whoami',
    'date',
    'env',
    'printenv',
}

_MUTATING_PREFIXES = {
    'rm',
    'mv',
    'cp',
    'chmod',
    'chown',
    'chgrp',
    'touch',
    'mkdir',
    'rmdir',
    'truncate',
    'ln',
    'tee',
}

_MUTATING_KEYWORDS = [
    r'\b(systemctl|service|supervisorctl)\b',
    r'\b(apt|apt-get|yum|dnf|apk|brew|pip|pip3|poetry|npm|pnpm|yarn|gem|cargo)\b',
    r'\b(docker|podman)\s+(run|exec|build|compose|rm|stop|restart|kill)\b',
    r'\bkubectl\s+(apply|delete|patch|edit|replace|scale|rollout|drain|cordon|uncordon)\b',
    r'\b(terraform|ansible|helm)\b',
    r'\b(flyway|liquibase|alembic|migrate|prisma)\b',
    r'\bgit\s+(commit|push|merge|rebase|reset|cherry-pick|stash|tag|checkout|switch|clean)\b',
    r'\b(iptables|ufw|ifconfig|route|nmcli)\b',
]

_BLOCK_PATTERNS = [
    (r':\(\)\s*\{\s*:\|:\s*&\s*\};:', 'fork_bomb'),
    (r'\b(mkfs(\.\w+)?|fdisk|parted|sfdisk)\b', 'disk_format_tool'),
    (r'\bdd\s+if=/dev/(zero|random|urandom)\s+of=/dev/', 'dd_device_wipe'),
    (r'\b(shutdown|reboot|poweroff|halt)\b', 'power_operation'),
    (r'\binit\s+[06]\b', 'init_power_operation'),
]


def _segments(command: str) -> list[str]:
    return [segment.strip() for segment in re.split(r'(?:&&|\|\||;|\|)', command) if segment.strip()]


def _first_two_tokens(segment: str) -> tuple[str, str]:
    try:
        parts = shlex.split(segment, posix=True)
    except ValueError:
        return '', ''
    if not parts:
        return '', ''
    first = parts[0].lower()
    second = parts[1].lower() if len(parts) > 1 else ''
    return first, second


def _readonly_reason(command: str) -> str | None:
    segments = _segments(command)
    if not segments:
        return None

    for segment in segments:
        first, second = _first_two_tokens(segment)
        if not first:
            return None
        if first in _READ_ONLY_COMMANDS:
            continue
        if first == 'git' and second in {'status', 'log', 'show', 'diff'}:
            continue
        return None

    return 'readonly_diagnostics'


def _mutating_reason(command: str) -> str | None:
    normalized = command.lower().strip()
    if not normalized:
        return 'empty_command'

    if _has_output_redirection(command):
        return 'output_redirection'

    for segment in _segments(normalized):
        first, second = _first_two_tokens(segment)
        if first in _MUTATING_PREFIXES:
            return f'mutating_prefix_{first}'
        if first == 'sed' and second == '-i':
            return 'in_place_edit'

    for pattern in _MUTATING_KEYWORDS:
        if re.search(pattern, normalized):
            return f'keyword_{pattern}'

    return None


def _has_output_redirection(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='<>|')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Treat parse errors conservatively.
        return True

    return any(token in {'>', '>>'} for token in tokens)


def _blocked_reason(command: str) -> str | None:
    if _is_root_delete_command(command):
        return 'rm_rf_root'

    normalized = command.lower().strip()
    for pattern, reason in _BLOCK_PATTERNS:
        if re.search(pattern, normalized):
            return reason
    return None


def _is_root_delete_command(command: str) -> bool:
    for segment in _segments(command):
        try:
            parts = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if not parts or parts[0].lower() != 'rm':
            continue

        has_recursive = False
        has_force = False
        root_targeted = False

        for token in parts[1:]:
            lowered = token.lower()
            if token.startswith('-'):
                if lowered in {'--recursive', '-r', '-R'} or ('r' in token and token.startswith('-')):
                    has_recursive = True
                if lowered in {'--force', '-f'} or ('f' in token and token.startswith('-')):
                    has_force = True
                if lowered == '--no-preserve-root':
                    root_targeted = True
                continue

            if token in {'/', '/*', '/.*', '/.', '/..'}:
                root_targeted = True
            elif token.startswith('/*') or token.startswith('/.*'):
                root_targeted = True

        if has_recursive and has_force and root_targeted:
            return True

    return False


def classify_shell_command(
    command: str,
    *,
    mode: str = 'balanced',
    allow_hard_block_override: bool = False,
) -> ShellPolicyResult:
    normalized_mode = (mode or 'balanced').strip().lower()
    blocked_reason = _blocked_reason(command)
    if blocked_reason:
        if allow_hard_block_override:
            return ShellPolicyResult(
                decision=ShellPolicyDecision.REQUIRE_APPROVAL,
                reason=f'hard_block_overridden_{blocked_reason}',
            )
        return ShellPolicyResult(decision=ShellPolicyDecision.BLOCKED, reason=blocked_reason)

    readonly_reason = _readonly_reason(command)
    mutating_reason = _mutating_reason(command)

    if normalized_mode == 'permissive':
        if mutating_reason:
            return ShellPolicyResult(decision=ShellPolicyDecision.REQUIRE_APPROVAL, reason=mutating_reason)
        return ShellPolicyResult(decision=ShellPolicyDecision.ALLOW_AUTORUN, reason='permissive_mode')

    if normalized_mode == 'strict':
        if readonly_reason:
            return ShellPolicyResult(decision=ShellPolicyDecision.ALLOW_AUTORUN, reason=readonly_reason)
        return ShellPolicyResult(
            decision=ShellPolicyDecision.REQUIRE_APPROVAL,
            reason=mutating_reason or 'strict_mode_non_allowlisted',
        )

    # balanced (default)
    if readonly_reason and not mutating_reason:
        return ShellPolicyResult(decision=ShellPolicyDecision.ALLOW_AUTORUN, reason=readonly_reason)
    return ShellPolicyResult(
        decision=ShellPolicyDecision.REQUIRE_APPROVAL,
        reason=mutating_reason or 'non_readonly_command',
    )
