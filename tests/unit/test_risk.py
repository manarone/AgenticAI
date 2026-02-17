from libs.common.enums import RiskTier
from libs.common.risk import classify_risk, requires_approval


def test_risk_classification_levels():
    assert classify_risk('delete this file') == RiskTier.L3
    assert classify_risk('update this config') == RiskTier.L2
    assert classify_risk('summarize this note') == RiskTier.L1
    assert requires_approval('delete this file') is True
