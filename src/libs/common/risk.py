from libs.common.enums import RiskTier


DESTRUCTIVE_KEYWORDS = {
    'delete',
    'remove',
    'destroy',
    'restart',
    'shutdown',
    'purchase',
    'pay',
    'send email',
    'send message',
    'change config',
}


def classify_risk(text: str) -> RiskTier:
    lowered = text.lower()
    if any(keyword in lowered for keyword in DESTRUCTIVE_KEYWORDS):
        return RiskTier.L3
    if 'write' in lowered or 'modify' in lowered or 'update' in lowered:
        return RiskTier.L2
    return RiskTier.L1


def requires_approval(text: str) -> bool:
    return classify_risk(text) == RiskTier.L3
