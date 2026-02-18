# Agent Workflow Rules

## PR Review Loop (Required)
1. After opening or updating a PR, post comments that explicitly ping all review bots:
   `@greptile review latest head <short_sha> please`
   `@codex review latest head <short_sha> please`
   `@claude review latest head <short_sha> please`
2. Wait for all bot responses on the latest head commit before merging or re-pinging.
3. Treat bot findings and CI failures as blockers.
4. Fix actionable findings in new commits pushed to the same PR branch.
5. Re-run review by pinging all bots again after fixes.
6. After each ping wave, wait at least 5 minutes before checking or re-pinging.
7. Only re-ping a bot if no review appears for the latest head commit after the wait window.
8. Repeat fix -> push -> re-review until all bots are clear (or no blocking findings remain) and required checks are green.
9. Then stop the loop and hand off for final merge decision.

## Review Scope Guidance
- Prioritize correctness, security, reliability, and regression risk.
- Ignore purely stylistic suggestions unless they improve safety or maintainability.
- Keep PR scope tight; do not add unrelated refactors while resolving review feedback.

## Merge Gate
- Required CI checks: pass.
- No unresolved blocking bot/human review findings.
- If uncertain whether a finding is blocking, treat it as blocking until clarified.
