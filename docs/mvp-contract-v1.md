# AgentAI MVP Contract v1 (Private Beta)

## Purpose
This document is the single source of truth for MVP parity and private beta readiness.

Sources merged:
- `README.md`
- `docs/idea-to-first-customer.md` (Step 4)
- `docs/idea-to-first-customer.md` (Step 6)
- `docs/high-level.md` (Phase 1)
- `docs/high-level.md` (Phase 2)

Conflict resolution:
1. Step-4 `Explicitly NOT in MVP` is authoritative for beta blockers.
2. `README.md` capabilities remain in-scope when they improve core reliability/safety.
3. `docs/high-level.md` Phase 1 capabilities are included only if not blocked by Step-4 exclusions.
4. Heartbeat and basic RAG are post-beta.

## Beta-Blocking
- Telegram bot onboarding via invite code (`/start <invite>`)
- Core coordinator -> executor -> result loop
- Task lifecycle tracking in DB with enforced legal transitions
- `/status` and `/cancel` commands
- Shell safety policy with approval gates for mutating commands (for local shell execution paths)
- Input sanitization and prompt-injection blocking
- Conversation context assembly with bounded window (recent full + older summarized)
- Conversation memory integration (local or mem0 backends)
- Deterministic web routing for time-sensitive queries (`today`, `latest`, `current`, etc.)
- Admin operations needed for 3-5 user beta:
  - tenant creation/listing
  - tenant-scoped invite issuance
  - tenant limits read/write
  - tenant health and recent failures visibility
- Token budget alerts and soft guardrails (configurable per-tenant limits with graceful refusal and audit)
- CI gates:
  - PR: fast smoke + safety-critical tests
  - full suite: nightly/release gate

## Experimental / Non-Blocking
- Browser tooling and browser mutation approvals
- Remote shell execution path
- Rich admin dashboard UX polish
- Optional advanced model/tool fallback tuning

## Post-Beta
- Heartbeat scheduler and `HEARTBEAT.md`
- Basic document upload/retrieval RAG
- Event listeners
- Voice in/out pipeline
- Billing and marketing website
- Full per-tenant namespace provisioning/mTLS stack

## Exit Criteria (Private Beta Ready)
- All tests marked `beta_blocking` pass
- No unresolved failures in tests marked `safety_critical`
- PR gate and full-suite gate are green
- All tests marked `mvp_smoke` pass and smoke runbook succeeds end-to-end
- Known gaps are only in Experimental or Post-Beta sections

## Test Marker Standard
- `beta_blocking`, `safety_critical`, and `mvp_smoke` are required pytest markers.
- PR-02 establishes marker registration and CI gating behavior.
