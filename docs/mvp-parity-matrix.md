# MVP Parity Matrix

| Requirement | Source Doc | Current Evidence | Gap | Blocking? | Target PR |
|---|---|---|---|---|---|
| Unified MVP baseline and scope boundaries | `README.md`, `docs/idea-to-first-customer.md`, `docs/high-level.md` | This matrix + `docs/mvp-contract-v1.md` | None after adoption | Yes | PR-01 |
| Invite onboarding via `/start <invite>` | `README.md`, Step 4 | `src/services/coordinator/main.py`, `src/libs/common/repositories.py`, `tests/integration/test_coordinator_flow.py` | Add tenant-specific invite targeting + strict admin behavior | Yes | PR-06 |
| Coordinator/executor core loop | Step 4, `README.md` | `src/services/coordinator/main.py`, `src/services/executor/main.py`, `src/libs/common/task_bus.py` | Harden idempotency and transition enforcement | Yes | PR-04 |
| Task lifecycle safety | Step 4 | `src/libs/common/state_machine.py`, `tests/unit/test_state_machine.py` | Enforce transitions in repository write path | Yes | PR-04 |
| `/status` and `/cancel` | Step 4, `docs/bootstrap.md` | `src/services/coordinator/main.py`, `tests/integration/test_coordinator_flow.py` | Add acceptance test coverage in one smoke file | Yes | PR-03 |
| Approval gates for mutating actions | `README.md`, Step 4 | `src/libs/common/shell_policy.py`, `tests/integration/test_coordinator_flow.py`, `tests/integration/test_executor_shell_policy.py` | Add private-beta guardrails and duplicate-safe notifications | Yes | PR-04 |
| Input sanitization | Step 4 | `src/libs/common/sanitizer.py`, `tests/unit/test_sanitizer.py` | None | Yes | Existing + PR-02 tagging |
| Bounded context window (recent full + older summarized) | Step 4, Phase 1 | Recent conversation currently in coordinator | Persisted summary compaction for older context | Yes | PR-05 |
| Memory integration (mem0/local) | Step 4, `README.md` | `src/libs/common/memory.py`, `tests/unit/test_memory_backend_config.py` | None for beta blocker baseline | Yes | Existing + PR-02 tagging |
| Deterministic time-sensitive web path with dated sources | `README.md`, `docs/bootstrap.md` | coordinator web helper + tests | Add acceptance test coverage | Yes | PR-03 |
| Tenant controls for small private beta (3-5 users) | `docs/idea-to-first-customer.md` (Step 6), `docs/high-level.md` (Phase 2) | Current admin API has default-tenant invite only | Add tenant APIs, limits, health views | Yes | PR-06 |
| Token budget alerts/soft guardrails | `docs/idea-to-first-customer.md` (Step 6), `docs/high-level.md` (Phase 2) | Token usage table + summary endpoints exist | Add configurable per-tenant limits and graceful refusal/audit | Yes | PR-06 |
| PR CI speed + risk coverage | Plan + CI guidance | `.github/workflows/ci.yml` currently full pytest | Tiered gate + separate full-suite workflow | Yes | PR-02 |
| One-command beta smoke verification | Plan | No dedicated smoke script | Add `scripts/beta_smoke.sh` + runbook go/no-go | Yes | PR-07 |
| Browser automation | Phase 3, Step-4 excluded | Browser code + tests exist | Keep non-blocking | No | N/A |
| Remote shell execution path | `README.md`, `docs/idea-to-first-customer.md` (Step 4 exclusion) | `src/services/executor/main.py`, `tests/integration/test_executor_shell_policy.py` | Keep non-blocking for private beta while preserving safety policy coverage | No | N/A |
| Voice pipeline | Phase 3, Step-4 excluded | Not implemented | Explicit post-beta | No | Post-beta |
| Event listeners | Phase 3, Step-4 excluded | Not implemented | Explicit post-beta | No | Post-beta |
| Heartbeat system | Phase 1 optional + open questions | Not implemented | Explicit post-beta | No | Post-beta |
| Basic RAG document upload/retrieval | Phase 1 optional + open questions | Not implemented | Explicit post-beta | No | Post-beta |
| Dashboard/billing/marketing | Later phases | Minimal admin only | Explicit post-beta | No | Post-beta |

## Section Map
- Beta-Blocking: all rows marked `Blocking? = Yes`
- Experimental/Non-Blocking: browser and optional advanced capabilities
- Post-Beta: heartbeat, RAG, event listeners, voice, billing, marketing, full namespace isolation

## Implementation Staging Note
- Rows with future `Target PR` values are planned deltas, not current-state guarantees.
- Specifically: pytest marker registration is delivered in PR-02; `scripts/beta_smoke.sh` is delivered in PR-07.
