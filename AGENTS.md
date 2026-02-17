# Agent Workflow Rules

## PR Review Loop (Required)
1. After opening or updating a PR, post a comment that explicitly pings `@greptile` (example: `@greptile review`).
2. Wait for Greptile review output before merging.
3. Treat Greptile and CI failures as blockers.
4. Fix actionable findings in new commits pushed to the same PR branch.
5. Re-run review by pinging `@greptile` again after fixes.
6. Repeat fix -> push -> re-review until there are no blocking findings and required checks are green.
7. Then stop the loop and hand off for final merge decision.

## Review Scope Guidance
- Prioritize correctness, security, reliability, and regression risk.
- Ignore purely stylistic suggestions unless they improve safety or maintainability.
- Keep PR scope tight; do not add unrelated refactors while resolving review feedback.

## Merge Gate
- Required CI checks: pass.
- No unresolved blocking bot/human review findings.
- If uncertain whether a finding is blocking, treat it as blocking until clarified.
