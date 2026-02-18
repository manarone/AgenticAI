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
    'stat',
    'df',
    'du',
    'ps',
    'uname',
    'id',
    'whoami',
    'date',
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

_SERVICE_MANAGERS = {'systemctl', 'service', 'supervisorctl'}
_PACKAGE_MANAGERS = {'apt', 'apt-get', 'yum', 'dnf', 'apk', 'brew', 'pip', 'pip3', 'poetry', 'npm', 'pnpm', 'yarn', 'gem', 'cargo'}
_CONTAINER_MUTATING_SUBCOMMANDS = {'run', 'exec', 'build', 'compose', 'rm', 'stop', 'restart', 'kill'}
_KUBECTL_MUTATING_SUBCOMMANDS = {'apply', 'delete', 'patch', 'edit', 'replace', 'scale', 'rollout', 'drain', 'cordon', 'uncordon'}
_DEPLOY_TOOLS = {'terraform', 'ansible', 'helm'}
_DB_MIGRATION_TOOLS = {'flyway', 'liquibase', 'alembic', 'migrate', 'prisma'}
_GIT_MUTATING_SUBCOMMANDS = {'commit', 'push', 'merge', 'rebase', 'reset', 'cherry-pick', 'stash', 'tag', 'checkout', 'switch', 'clean'}
_NETWORK_MUTATING_TOOLS = {'iptables', 'ufw', 'ifconfig', 'route', 'nmcli'}

_FIND_MUTATING_TOKENS = {
    '-delete',
    '-exec',
    '-execdir',
    '-ok',
    '-okdir',
    '-fprint',
    '-fprintf',
    '-fprint0',
    '-fls',
}
_CONTROL_OPERATORS = {'&&', '||', ';', '|'}


def _segments(command: str) -> list[str]:
    stripped = command.strip()
    if not stripped:
        return []

    try:
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars='|&;')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        fallback_segments = [segment.strip() for segment in re.split(r'(?:&&|\|\||;|\|)', stripped) if segment.strip()]
        return fallback_segments or [stripped]

    segments: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in _CONTROL_OPERATORS:
            if current:
                segments.append(' '.join(current))
                current = []
            continue
        current.append(token)

    if current:
        segments.append(' '.join(current))

    return segments


def _tokens(segment: str) -> list[str] | None:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def _command_name(token: str) -> str:
    stripped = (token or '').strip()
    if not stripped:
        return ''
    if '/' in stripped:
        stripped = stripped.rstrip('/').rsplit('/', 1)[-1]
    return stripped.lower()


def _first_two_tokens(parts: list[str] | None) -> tuple[str, str]:
    if not parts:
        return '', ''
    first = _command_name(parts[0])
    second = parts[1].lower() if len(parts) > 1 else ''
    return first, second


def _find_has_mutating_action(parts: list[str] | None) -> bool:
    if not parts:
        return False
    for token in parts[1:]:
        lowered = token.lower()
        if lowered in _FIND_MUTATING_TOKENS:
            return True
        if lowered.startswith('-exec') or lowered.startswith('-ok') or lowered.startswith('-fprint') or lowered.startswith('-fls'):
            return True
    return False


def _env_subcommand(parts: list[str] | None) -> list[str]:
    if not parts or _command_name(parts[0]) != 'env':
        return []

    i = 1
    while i < len(parts):
        token = parts[i]
        lowered = token.lower()

        if token == '--':
            i += 1
            break

        if lowered in {'-u', '--unset', '-c', '--chdir', '-s', '--split-string'}:
            i += 2
            continue

        if token.startswith('-'):
            i += 1
            continue

        if '=' in token and not token.startswith('='):
            i += 1
            continue

        break

    return parts[i:] if i < len(parts) else []


def _readonly_reason(command: str) -> str | None:
    segments = _segments(command)
    if not segments:
        return None

    for segment in segments:
        parts = _tokens(segment)
        if parts is None:
            return None

        first = _command_name(parts[0])
        second = parts[1].lower() if len(parts) > 1 else ''

        if not first:
            return None
        if first in _READ_ONLY_COMMANDS:
            continue
        if first == 'find' and not _find_has_mutating_action(parts):
            continue
        if first == 'env':
            env_subcommand = _env_subcommand(parts)
            if not env_subcommand:
                continue
            return None
        if first == 'git' and second in {'status', 'log', 'show', 'diff'}:
            continue
        return None

    return 'readonly_diagnostics'


def _mutating_reason(command: str) -> str | None:
    normalized = command.lower().strip()
    if not normalized:
        return 'empty_command'

    if _tokens(command) is None:
        return 'shell_parse_error'

    if _contains_shell_substitution(command):
        return 'shell_command_substitution'

    if _has_output_redirection(command):
        return 'output_redirection'

    for segment in _segments(normalized):
        parts = _tokens(segment)
        if parts is None:
            return 'shell_parse_error'

        first, second = _first_two_tokens(parts)
        if first in _MUTATING_PREFIXES:
            return f'mutating_prefix_{first}'
        if first == 'find' and _find_has_mutating_action(parts):
            return 'find_mutating_action'
        if first == 'env' and _env_subcommand(parts):
            return 'env_invokes_subcommand'
        if first == 'sed' and second == '-i':
            return 'in_place_edit'
        if first in _SERVICE_MANAGERS:
            return f'mutating_tool_{first}'
        if first in _PACKAGE_MANAGERS:
            return f'mutating_tool_{first}'
        if first in {'docker', 'podman'} and second in _CONTAINER_MUTATING_SUBCOMMANDS:
            return f'mutating_tool_{first}_{second}'
        if first == 'kubectl' and second in _KUBECTL_MUTATING_SUBCOMMANDS:
            return f'mutating_tool_{first}_{second}'
        if first in _DEPLOY_TOOLS:
            return f'mutating_tool_{first}'
        if first in _DB_MIGRATION_TOOLS:
            return f'mutating_tool_{first}'
        if first == 'git' and second in _GIT_MUTATING_SUBCOMMANDS:
            return f'mutating_tool_{first}_{second}'
        if first in _NETWORK_MUTATING_TOOLS:
            return f'mutating_tool_{first}'

    return None


def _contains_shell_substitution(command: str) -> bool:
    in_single = False
    in_double = False
    escaped = False

    for index, ch in enumerate(command):
        if escaped:
            escaped = False
            continue

        if ch == '\\':
            if not in_single:
                escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            continue

        if ch == '"' and not in_single:
            in_double = not in_double
            continue

        if in_single:
            continue

        if ch == '`':
            return True
        if ch == '$' and index + 1 < len(command) and command[index + 1] == '(':
            return True

    return False


def _has_output_redirection(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='<>|')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False

    return any(token in {'>', '>>'} for token in tokens)


def _blocked_reason(command: str) -> str | None:
    if _is_root_delete_command(command):
        return 'rm_rf_root'
    if _is_fork_bomb_command(command):
        return 'fork_bomb'

    for segment in _segments(command):
        parts = _tokens(segment)
        if parts is None:
            continue

        first = _command_name(parts[0])
        blocked_parts = parts if first != 'env' else _env_subcommand(parts)
        if not blocked_parts:
            continue

        blocked_command = _command_name(blocked_parts[0])
        if not blocked_command:
            continue

        if blocked_command.startswith('mkfs') or blocked_command in {'fdisk', 'parted', 'sfdisk'}:
            return 'disk_format_tool'
        if blocked_command in {'shutdown', 'reboot', 'poweroff', 'halt'}:
            return 'power_operation'
        if blocked_command == 'init':
            second = blocked_parts[1].lower() if len(blocked_parts) > 1 else ''
            if second in {'0', '6'}:
                return 'init_power_operation'
        if blocked_command == 'dd':
            has_source = any(token.lower() in {'if=/dev/zero', 'if=/dev/random', 'if=/dev/urandom'} for token in blocked_parts[1:])
            has_device_sink = any(token.lower().startswith('of=/dev/') for token in blocked_parts[1:])
            if has_source and has_device_sink:
                return 'dd_device_wipe'

    return None


def _is_fork_bomb_command(command: str) -> bool:
    parts = _tokens(command)
    if not parts:
        return False

    candidates = [parts]
    if _command_name(parts[0]) == 'env':
        env_subcommand = _env_subcommand(parts)
        if env_subcommand:
            candidates.append(env_subcommand)

    for candidate in candidates:
        if not candidate:
            continue

        if candidate[0] != ':(){':
            continue

        has_pipe_ampersand = any(token == ':|:&' for token in candidate[1:])
        has_terminator = any(token == '};:' for token in candidate[1:])
        if has_pipe_ampersand and has_terminator:
            return True

    return False


def _is_root_delete_command(command: str) -> bool:
    for segment in _segments(command):
        parts = _tokens(segment)
        if parts is None:
            continue
        first = _command_name(parts[0])
        rm_parts = parts if first == 'rm' else (_env_subcommand(parts) if first == 'env' else [])
        if not rm_parts or _command_name(rm_parts[0]) != 'rm':
            continue

        has_recursive = False
        has_force = False
        root_targeted = False

        for token in rm_parts[1:]:
            lowered = token.lower()
            if token.startswith('-'):
                if lowered.startswith('--'):
                    if lowered == '--recursive':
                        has_recursive = True
                    if lowered == '--force':
                        has_force = True
                else:
                    short_flags = token[1:].lower()
                    if 'r' in short_flags:
                        has_recursive = True
                    if 'f' in short_flags:
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
        if readonly_reason and not mutating_reason:
            return ShellPolicyResult(decision=ShellPolicyDecision.ALLOW_AUTORUN, reason=readonly_reason)
        return ShellPolicyResult(
            decision=ShellPolicyDecision.REQUIRE_APPROVAL,
            reason=mutating_reason or 'permissive_mode_unknown_command',
        )

    if normalized_mode == 'strict':
        if readonly_reason and not mutating_reason:
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
