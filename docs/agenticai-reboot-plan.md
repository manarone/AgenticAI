# AgenticAI Reboot Plan (Enterprise-Secure OpenClaw Alternative)

## Summary
AgenticAI will be a secure, enterprise-first agent platform focused on small DevOps teams.
It uses one Telegram bot per organization, single-tenant per org deployment, and per-user isolated execution sessions.
The core differentiator is OpenClaw-like capability with stronger security, governance, and reliability.

## Locked Decisions
1. Target customer: small DevOps teams.
2. Isolation model: single-tenant per org.
3. Telegram model: one bot per org.
4. Approval model: risk-tiered by default.
5. Bypass mode: persistent per-user toggle only if org policy allows; org admin can disable org-wide at any time.
6. GitHub/Coolify: internal engineering workflow, not end-user product scope.

## Product Scope

### In Scope
1. Telegram chat interface.
2. Coordinator/executor architecture (non-blocking chat while work runs).
3. Terminal and automation actions in isolated execution containers.
4. Skills system (`.md` + permission manifest).
5. Web search and browser automation.
6. Memory and context management.
7. App/workload spin-up and lifecycle controls.
8. Security controls (approvals, policy engine, sanitization, audit).
9. Admin control plane (org policy, user controls, audit visibility).

### Out of Scope (Initial)
1. Billing automation.
2. Public skill marketplace.
3. Voice I/O.
4. Consumer integrations (email/calendar) unless customer-blocking.

## Architecture

### Core Services
1. Coordinator Service
- Handles Telegram ingress, planning, user communication.
- Delegates execution and stays responsive.

2. Execution Manager
- Queues jobs and provisions isolated executors.
- Applies policy checks before execution.

3. Executor Runtime
- Ephemeral per-task container.
- Scoped credentials, isolated workspace.
- Streams progress/results back.

4. Policy and Approval Engine
- Assigns risk tiers to actions.
- Enforces approval and bypass rules.

5. Memory Service
- Sliding conversation window.
- Long-term semantic memory with summarization.

6. Admin API/UI
- Org/user policy management.
- Bypass governance, audit search, emergency controls.

### Data Stores
1. Postgres: orgs, users, tasks, approvals, audits, policy, skill metadata.
2. Redis: queue/events and transient state.
3. Object storage (S3/MinIO): skill files, artifacts, execution outputs.

## External Interface Contract

### Telegram Commands
1. `/start <invite_code>`
2. `/status`
3. `/tasks`
4. `/cancel <task_id|all>`
5. `/approvals`
6. `/approve <approval_id>`
7. `/deny <approval_id>`
8. `/bypass on`
9. `/bypass off`
10. `/help`

### API Endpoints
1. `POST /telegram/webhook`
2. `GET /healthz`
3. `GET /readyz`
4. `POST /v1/tasks`
5. `GET /v1/tasks/{task_id}`
6. `POST /v1/tasks/{task_id}/cancel`
7. `POST /v1/approvals/{approval_id}/decision`
8. `POST /v1/users/{user_id}/bypass-mode`
9. `GET /v1/audit-events`
10. `POST /v1/skills/install`
11. `POST /v1/skills/validate`

### Core Types
1. `TaskStatus`: `QUEUED | RUNNING | WAITING_APPROVAL | SUCCEEDED | FAILED | CANCELED | TIMED_OUT`
2. `RiskTier`: `L0_READONLY | L1_MUTATING | L2_DESTRUCTIVE | L3_PRIVILEGED`
3. `ApprovalDecision`: `APPROVE | DENY | EXPIRE`
4. `BypassMode`: `OFF | ON`
5. `PolicySource`: `ORG_DEFAULT | USER_OVERRIDE | SYSTEM_ENFORCED`

## Security Model
1. Deny-by-default action policy.
2. Risk-tiered approvals with clear rationale.
3. Input/prompt sanitization before planning/execution.
4. Immutable enforcement rules (agent cannot self-weaken policy).
5. Just-in-time scoped credentials for executors.
6. Full audit trail for plan/action/approval/mutation.
7. Per-user identity, quotas, and rate limits.
8. Org emergency controls:
- Disable bypass globally.
- Pause mutating actions.
- Cancel risky in-flight tasks.
- Rotate credentials.

## Delivery Tracks

### Track A: Foundation (Weeks 1-2)
1. Monorepo/service skeleton.
2. DB schema + migrations.
3. Redis queue/event wiring.
4. Telegram webhook + identity mapping.
5. Basic coordinator loop.

Exit criteria:
- Telegram message receives deterministic response.
- Task lifecycle is persisted.

### Track B: Secure Execution Core (Weeks 3-5)
1. Executor lifecycle orchestration.
2. Risk classifier + approval flow.
3. Terminal tool enforcement.
4. Audit event ingestion.

Exit criteria:
- Long-running tasks do not block chat.
- Risky actions pause for approval and resume correctly.

### Track C: Skills, Memory, Web/Browser (Weeks 6-8)
1. Skill manifest schema + validator.
2. Skill activation/version controls.
3. Context summarization + retrieval.
4. Web and browser adapters under unified policy.

Exit criteria:
- Skills obey declared permissions.
- Long chats remain effective without context bloat.

### Track D: App Lifecycle + Admin Controls (Weeks 9-11)
1. App/workload spin-up and teardown flows.
2. Admin policy controls (bypass, risk thresholds).
3. Audit exploration and incident workflows.
4. Org kill switches.

Exit criteria:
- Admin can enforce policy changes instantly.
- App operations are fully policy-gated and auditable.

### Track E: Hardening + Beta Readiness (Weeks 12-13)
1. Reliability tests (retry, timeout, idempotency).
2. Security tests (injection, bypass attempts, auth boundaries).
3. Load tests for concurrent users/tasks.
4. Runbooks and incident drills.

Exit criteria:
- Acceptance suite passes.
- No unresolved critical security findings.

## Test Scenarios

### Functional
1. Invite onboarding maps Telegram user to org correctly.
2. Coordinator remains responsive during long executor runs.
3. `/cancel` terminates task and releases resources.
4. Skills run only within declared permissions.
5. Browser and terminal actions return clear progress and results.

### Security
1. Prompt injection payload is blocked or escalated.
2. Mutating action requires approval when bypass is off.
3. Cross-user approval hijack is impossible.
4. Bypass users are still constrained by org policy.
5. Audit tampering attempts fail and are detectable.

### Reliability
1. Executor crash mid-task follows retry/recovery contract.
2. Duplicate webhook deliveries are idempotent.
3. Queue backlog drains under concurrency.
4. Service restart preserves task state integrity.

### Governance
1. Org admin disables bypass and active bypass users are revoked.
2. Policy updates affect new tasks immediately.
3. Mutating-action pause still allows read-only operations.

## Anti-Bloat Controls
1. Feature flags for all major capabilities.
2. No integration enters MVP without threat model, policy mapping, tests, and runbook.
3. Weekly scope review prioritizes customer-blocking work only.
4. Definition of done includes security and reliability acceptance, not just happy-path functionality.

## Assumptions
1. Implementation stack: Python + FastAPI services, Postgres, Redis, containerized executors.
2. One dedicated deployment per org.
3. Executor isolation is per task with per-user policy context.
4. Bypass default is `OFF`; org admin opt-in required for user bypass.
5. Internal CI/CD tooling is separate from user-facing product features.
