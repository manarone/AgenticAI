from libs.common.shell_policy import ShellPolicyDecision, classify_shell_command


def test_readonly_command_is_autorun():
    result = classify_shell_command('ls -la')
    assert result.decision == ShellPolicyDecision.ALLOW_AUTORUN
    assert result.reason == 'readonly_diagnostics'


def test_mutating_command_requires_approval():
    result = classify_shell_command('systemctl restart nginx')
    assert result.decision == ShellPolicyDecision.REQUIRE_APPROVAL


def test_hard_block_is_denied_by_default():
    result = classify_shell_command('rm -rf /')
    assert result.decision == ShellPolicyDecision.BLOCKED
    assert result.reason == 'rm_rf_root'


def test_root_glob_delete_is_hard_blocked():
    result = classify_shell_command('rm -rf /*')
    assert result.decision == ShellPolicyDecision.BLOCKED
    assert result.reason == 'rm_rf_root'


def test_hard_block_can_be_overridden_to_approval():
    result = classify_shell_command('rm -rf /', allow_hard_block_override=True)
    assert result.decision == ShellPolicyDecision.REQUIRE_APPROVAL
    assert result.reason.startswith('hard_block_overridden_')


def test_quoted_redirection_symbol_does_not_trigger_mutation():
    result = classify_shell_command("grep 'a>b' /tmp/file.txt")
    assert result.decision == ShellPolicyDecision.ALLOW_AUTORUN


def test_find_readonly_path_is_autorun():
    result = classify_shell_command('find /tmp -type f -name "*.log"')
    assert result.decision == ShellPolicyDecision.ALLOW_AUTORUN


def test_find_delete_requires_approval():
    result = classify_shell_command('find /tmp -delete')
    assert result.decision == ShellPolicyDecision.REQUIRE_APPROVAL
    assert result.reason == 'find_mutating_action'


def test_env_subcommand_requires_approval():
    result = classify_shell_command('env -i rm -rf /tmp/work')
    assert result.decision == ShellPolicyDecision.REQUIRE_APPROVAL
    assert result.reason == 'env_invokes_subcommand'


def test_env_wrapped_rm_rf_root_is_hard_blocked():
    result = classify_shell_command('env -i rm -rf /')
    assert result.decision == ShellPolicyDecision.BLOCKED
    assert result.reason == 'rm_rf_root'


def test_absolute_path_rm_rf_root_is_hard_blocked():
    result = classify_shell_command('/bin/rm -rf /')
    assert result.decision == ShellPolicyDecision.BLOCKED
    assert result.reason == 'rm_rf_root'


def test_env_wrapped_absolute_path_rm_rf_root_is_hard_blocked():
    result = classify_shell_command('env -i /bin/rm -rf /')
    assert result.decision == ShellPolicyDecision.BLOCKED
    assert result.reason == 'rm_rf_root'


def test_command_substitution_requires_approval():
    result = classify_shell_command('ls $(touch /tmp/pwn)')
    assert result.decision == ShellPolicyDecision.REQUIRE_APPROVAL
    assert result.reason == 'shell_command_substitution'


def test_backtick_substitution_requires_approval():
    result = classify_shell_command('cat `touch /tmp/pwn`')
    assert result.decision == ShellPolicyDecision.REQUIRE_APPROVAL
    assert result.reason == 'shell_command_substitution'
