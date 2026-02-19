from libs.common.browser_policy import BrowserActionClass, classify_browser_action, normalize_browser_action


def test_normalize_browser_action_strips_prefix():
    assert normalize_browser_action('browser_open') == 'open'
    assert normalize_browser_action(' open ') == 'open'


def test_classify_browser_action_read_only():
    assert classify_browser_action('browser_snapshot') == BrowserActionClass.READ_ONLY


def test_classify_browser_action_mutating():
    assert classify_browser_action('fill') == BrowserActionClass.MUTATING


def test_classify_browser_action_unsupported():
    assert classify_browser_action('download') == BrowserActionClass.UNSUPPORTED
