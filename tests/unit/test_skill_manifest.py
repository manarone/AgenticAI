import pytest

from libs.common.skill_store import SkillStore


def test_skill_manifest_validation_rejects_unknown_risk_tier():
    text = '''
---
name: invalid
version: "1.0.0"
risk_tier: LX
permissions:
  files:
    read: []
    write: []
  network:
    allow_domains: []
  env:
    allow: []
requires_approval_actions: []
---
Invalid skill.
'''
    with pytest.raises(Exception):
        SkillStore.validate_skill_text(text)
