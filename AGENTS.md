# Agent Workflow Rules

## PR Review Loop (Required)
1. After opening or updating a PR, post comments that explicitly ping both review bots:
   `@greptile review latest head <short_sha> please`
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

## Branch Consistency Loop (Required)
1. At session start and before final handoff, run:
   `git fetch origin --prune`
   `git remote prune origin`
2. Keep local `main` aligned with GitHub using fast-forward only:
   `git switch main`
   `git merge --ff-only origin/main`
3. Verify local branches track an upstream and have zero drift:
   `git for-each-ref refs/heads --format='%(refname:short) %(upstream:short)'`
   `git rev-list --left-right --count <local_branch>...<upstream_branch>`
4. Treat `[gone]` upstreams as stale and resolve immediately:
   if merged: delete local branch.
   if not merged: push branch or create a backup tag before deleting.
5. After a PR merges, clean up branch state:
   delete remote feature branch.
   delete local feature branch.
   sync `main` again.
6. If git warns about broken refs (for example duplicate branch files like `<name> 2` under `.git/refs/heads`), remove the malformed ref file and repeat step 1.

## Consistency Guardrails
- Use branch names that match `codex/<topic>` with no spaces.
- Avoid direct commits on `main`; do feature work on branches only.
- If a sync command fails, stop and fix repository health before continuing feature work.
