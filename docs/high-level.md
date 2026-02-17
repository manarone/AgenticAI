# AgentAI — High-Level Architecture & Feature Overview

> Related: [[Agent AI - OpenClaw-like System that is more secure]] | [[AgentAI Canvas.canvas]]

---

## Core Concept

A secure, self-hosted AI agent platform — similar to OpenClaw in capability but with per-user isolation, proper container security, and a coordinator/executor architecture that keeps the agent responsive at all times. Hosted SaaS model running on Proxmox + K3s.

---

## Architecture

### Per-User Isolation

Every paying user gets a fully isolated stack within their own K3s namespace. No shared compute, no shared data, no cross-user access. Each namespace contains:

- **Coordinator Pod (the "Brain")** — Lightweight. Handles conversation, planning, task delegation. Connects to the user's Telegram bot. Stays free to chat even while work is happening. Tracks task state for all running execution agents via a task table in Postgres (task ID, status, assigned agent, result).

- **Executor Pod(s) (the "Hands")** — Heavier, parallelizable. The coordinator delegates work here — code execution, terminal commands, file operations, API calls, app deployment. Multiple executors can run concurrently (up to plan limit). Ephemeral — spin up per task, destroy after. Wider network access than the coordinator since they're short-lived and sandboxed.

- **Browser Agent Pod** — Specialized executor for web automation. Runs Playwright or similar in its own container. Can fill forms, extract data, interact with websites, navigate flows. Same approval gates as other executors — destructive actions (submitting forms, making purchases) require user confirmation. Isolated from other executors.

- **Application Pod(s) (the "Output")** — User-facing apps built and deployed by the executor. Each app is its own pod. Only thing accessible via the Tailnet. Auto-expire after inactivity.

- **Postgres** — Per-user instance. Conversation history, task state, audit logs, config versioning.

- **Redis** — Per-user instance. Message queuing between coordinator and executors, caching.

- **mem0** — Per-user instance backed by that user's Postgres. Handles semantic, periodic, and ephemeral memory layers. Keeps context useful without overblowing the context window.

- **MinIO Bucket** — One MinIO cluster shared at the infrastructure level, but each user gets an isolated bucket with ACLs. Stores skills, user files, app artifacts, exports.

### Networking

- **Tailscale** for all networking. Zero-trust, encrypted WireGuard tunnels.
- Tailscale ACL tags to separate: admin access, coordinator-to-executor comms, app access, user access.
- Application pods exposed via Tailnet only — no public internet unless explicitly configured via Tailscale Funnel.
- **mTLS** between pods within a user's namespace for inter-service authentication.
- K3s NetworkPolicies enforce that namespaces cannot communicate with each other.

### Infrastructure

- **Host**: Single dedicated server — 256GB RAM, 40 cores, 2TB storage.
- **Virtualization**: Proxmox.
- **Orchestration**: K3s (lightweight Kubernetes).
- **Backups**: Automated to external S3 bucket. Proxmox-level snapshots + Postgres dumps + MinIO bucket replication.

---

## Agent Model — Coordinator/Executor Pattern

The coordinator does NOT do work itself. It plans, delegates, and communicates.

**Flow:**
1. User sends message via Telegram.
2. Coordinator receives it, decides what needs to happen.
3. If it's a simple question or conversation — coordinator responds directly.
4. If it requires work — coordinator spins up an execution agent, delegates the task, and remains free.
5. Multiple execution agents can run in parallel (up to plan limit).
6. Execution agents report results back to coordinator.
7. Coordinator synthesizes results and responds to the user.

**Why this matters:**
- User can chat while work is in progress (solves OpenClaw's "agent is busy" problem).
- Coordinator can manage multiple parallel tasks.
- Different models can run at different levels — cheap/fast model for coordination, stronger model for complex execution.

### Self-Improving Agent

The executor can draft new skills autonomously — writing .md files based on patterns it observes or tasks the user frequently requests. New skills proposed by the agent require user approval before activation. Larger changes (skills that request elevated permissions, network access, or filesystem writes) require explicit review via Telegram confirmation. Minor self-improvements (optimizing an existing skill's wording, adding a shortcut) can auto-approve based on trust level.

### Heartbeat System

The agent proactively wakes on a configurable interval (e.g. every 5-15 minutes), reviews its current context — pending tasks, recent events, calendar, notifications — and decides whether to reach out to the user. Different from cron (user-scheduled) and event listeners (trigger-based). The heartbeat is the agent autonomously deciding something is worth mentioning.

Examples:
- "You have a meeting in 30 minutes — here's a prep summary based on the last thread with that person."
- "That deployment you kicked off finished successfully."
- "Your server metrics look off — CPU spiked on prod-2."
- Morning briefing: daily summary of calendar, unread emails, pending tasks.

Controlled via a `HEARTBEAT.md` config — what to check, how often, what's worth interrupting the user for. Uses the coordinator's cheap/fast model to keep costs low. Can be disabled or frequency-adjusted per user.

### Event-Driven Automation

Beyond scheduled cron jobs, the coordinator can subscribe to event sources and trigger tasks reactively:
- "When I get an email from X, summarize it and send me the summary."
- "When a GitHub PR is opened on my repo, review it."
- "When a file is added to my MinIO bucket, process it."

Event listeners run as lightweight sidecars in the user's namespace. When a condition is met, the listener triggers the coordinator, which delegates as usual. Builds on the same coordinator/executor pattern — just a different trigger type alongside cron and direct messages.

---

## Chat Interface — Telegram

- **One bot per user.** User creates their own bot via BotFather during onboarding and provides the token. Safer than shared bot, and users can customize their bot's name/avatar.
- Bot lives inside the user's K3s namespace.
- Commands: `/status` (what's running), `/cancel` (kill a task or all tasks), `/config` (view/change settings).
- Supports text, images, voice notes, and file uploads.

### Multi-Modal Pipeline

- **Voice in**: User sends voice note → Whisper (via OpenRouter) transcribes → text sent to coordinator.
- **Voice out**: Coordinator response → ElevenLabs TTS → voice note sent back via Telegram. User chooses their own ElevenLabs voice. Toggle-able per user (text-only or voice).
- **Images**: Sent to coordinator, processed by vision-capable model.
- **Files**: Downloaded to user's MinIO bucket, accessible to executor agents.

---

## LLM / Model Layer

- **OpenRouter as the primary router.** Provides access to Kimi, Claude, GPT, Gemini, DeepSeek, and others through a single API.
- **Model is configurable** — both by admin (default) and by user (override). Users can also point to any OpenAI-compatible endpoint.
- **Per-agent-level model selection**: Coordinator can use a cheap/fast model, executors can use a stronger model for complex tasks. Configurable per user.
- **Fallback chain**: If primary model is unreachable, automatically route to next provider on OpenRouter.
- **BYOK supported**: Users can bring their own API keys for any provider.

---

## Skills System

- Skills are `.md` files — human-readable, auditable, version-controlled.
- Stored in the user's MinIO bucket.
- Each skill has a **permission manifest** in YAML frontmatter (OpenClaw-style) — declares filesystem access (read/write paths), network access (allowed domains), env vars needed. Risk tiered L1 (read-only) through L3 (credentials/production).
- Transferable: users can export and import skills.
- Examples: Timesheet generation, expense reports, email drafts, heartbeat/status checks, meeting summaries.
- **No marketplace to start.** Community shares skills publicly (GitHub, forums), users verify and import manually. Avoids the ClawHub malware problem.
- Skill versioning via git history on the dashboard.
- Agent can propose new skills autonomously (see Self-Improving Agent above).

---

## Computer Use

### Terminal / Remote Server Access

The executor can operate on real infrastructure the user gives it access to — not just sandboxed code execution. Users add their servers to the same Tailnet, and the executor reaches them via Tailscale SSH.

**Capabilities:**
- SSH into user's servers to diagnose issues, check logs, run commands.
- Manage services (restart nginx, check Docker containers, tail logs).
- Deploy code, run scripts, manage infrastructure.
- File operations on remote systems.

**Security:**
- Access controlled entirely via Tailscale ACLs — the executor can only reach machines the user explicitly allows.
- Destructive actions (restarting services, modifying configs, deleting files) require user approval via Telegram.
- Read-only actions (checking logs, running diagnostics, listing processes) proceed automatically.
- All remote commands logged in the audit trail.
- Each session is ephemeral — executor doesn't retain SSH keys or session state after task completion.

### Browser Automation

Dedicated browser agent executor running Playwright (or Puppeteer) in an isolated container.

**Capabilities:**
- Fill out web forms, extract data from websites, navigate multi-step flows.
- Screenshot capture and visual verification.
- Login to services using stored credentials (from secrets management).

**Security:**
- Runs in its own container, isolated from other executors.
- Destructive actions (form submissions, purchases, account changes) require user approval via Telegram.
- Read-only actions (scraping, screenshots) proceed automatically.
- No access to other users' browser sessions.
- Credentials retrieved from secrets store per-request, not persisted.

---

## Productivity Integrations

Each integration is implemented as a skill + OAuth connection. Auth tokens managed via secrets management.

**Priority integrations:**
- Email — Gmail, Outlook (read, draft, send with approval, summarize)
- Calendar — Google Calendar, Outlook Calendar (read, create events with approval)
- Notes — Notion, Obsidian (read, create, organize)
- Tasks — Todoist, Things 3, Trello (read, create, update)
- Cloud Storage — Google Drive, Dropbox (read, upload with approval)
- Dev — GitHub (PRs, issues, repo management)

Each integration requires the user to complete an OAuth flow during setup. Tokens encrypted and stored in secrets management. The coordinator accesses these through executor agents — never directly.

---

## Bootstrap / Onboarding

`bootstrap.md` walks a new user through initial setup:

1. Create a Telegram bot via BotFather (step-by-step with screenshots).
2. Provide the bot token.
3. Configure LLM provider — OpenRouter default, or BYOK endpoint.
4. Set up the agent's identity — name, personality, communication style.
5. Define what the agent knows about the user — role, preferences, common tasks.
6. Enable initial skills.
7. Connect integrations (email, calendar, etc.) — OAuth flows.
8. Choose ElevenLabs voice (if voice mode enabled).
9. First guided interaction — agent demonstrates a skill and delivers something useful in under 2 minutes.

---

## Scheduled Tasks (Cron) & Event Listeners

**Cron:**
- Users can set recurring tasks: "Every Monday at 9am, generate my timesheet."
- Lightweight cron scheduler per user's namespace.
- Triggers the coordinator pod, which delegates as usual.
- Managed via Telegram commands or dashboard.

**Event Listeners:**
- Subscribe to event sources (email inbox, GitHub webhooks, file changes, etc.).
- Trigger coordinator when conditions are met.
- Configured via Telegram or skills with event triggers in the manifest.

---

## Management Dashboard (Admin)

**For the platform operator, not per-user.**

### Tech Stack
- **Frontend**: AI-generated (React or Svelte — built and iterated with AI assistance).
- **Container management**: Portainer (pre-built, works with K3s).
- **Observability**: Prometheus + Grafana.

### Core Features
- User management — add/remove users, view per-user status.
- Token usage monitoring — per user, per model, per agent level.
- Container/pod status — running, stopped, errored, uptime, resource usage.
- Skill management — view installed skills across users.
- Audit log viewer — both user actions and agent actions, per namespace.
- Config history — git-style diff view of all config changes per user.
- Cost tracking — API spend per user, total platform spend.
- Alerting — agent loops, pod crashes, token spikes, disk usage.

### Operator Observability
- Prometheus + Grafana stack on K3s.
- CPU/RAM per namespace, API call volumes, error rates, pod restarts.
- Proactive alerts before users notice issues.

---

## Security Model

### What This Fixes vs. OpenClaw
| Problem in OpenClaw | AgentAI Solution |
|---|---|
| Runs on host OS by default | Full container isolation per user |
| WebSocket accepts any origin | Tailscale-only access, no public endpoints |
| Agent can self-modify config | Config changes versioned, approval-gated |
| Plaintext credential storage | Secrets management (encrypted, never in agent memory) |
| ClawHub had 12% malware rate | No marketplace — community-shared, user-verified skills |
| Prompt injection via chat/email | Input sanitization before LLM, approval gates for destructive actions |
| Single-threaded, agent gets stuck | Coordinator/executor split, parallel execution |

### Security Layers
- **Per-user namespace isolation** — K3s namespaces + NetworkPolicies. Users cannot reach each other.
- **mTLS** between pods within a namespace.
- **Input sanitization** — Every inbound message filtered for injection before reaching the LLM.
- **Approval gates** — Destructive actions (delete, send, spend) require user confirmation via Telegram. Non-destructive actions (search, read, create) proceed automatically. Middle-ground actions (install, config change) approved once then auto-approved (iOS-style permissions).
- **Secrets management** — OAuth tokens, API keys stored encrypted. Executor requests a token for a specific service, uses it, token not persisted beyond the request.
- **Immutable audit log** — Every agent action logged with full context (what it saw, decided, did). Per-user, stored in their Postgres.
- **Config versioning** — All changes tracked with diffs. Rollback available via dashboard.
- **Executor isolation** — Ephemeral containers with resource limits (CPU, memory, disk). No persistent state.

---

## Business Model

### Hosted SaaS
- Platform runs on owned infrastructure (Proxmox + K3s on dedicated server).
- No free tier. 14-day trial.

### Pricing
| Tier | Price | Includes |
|---|---|---|
| Pro | $50/month | Single user, multi-agent (up to 5), up to 5 apps, 5 concurrent executor agents. API usage not included. |
| Team | $100/month | Multi-user, multi-agent (up to 5), up to 5 apps, 5 concurrent executor agents. API usage not included. |

- API credits: Users purchase via OpenRouter or BYOK.
- Per-user spending limits and usage alerts on the dashboard.
- Metering: Track tokens per user, per model, per agent level.

### Billing
- **Square** (existing account) for subscription management.

---

## Website

### Purpose
Marketing site + signup/purchase flow. The front door to the product.

### Pages
- **Landing page** — Hero with value prop ("OpenClaw-level AI agent, actually secure"), feature overview, architecture diagram (simplified), security comparison vs. OpenClaw.
- **Pricing** — Two tiers, clear comparison table, 14-day trial CTA.
- **How it works** — Step-by-step: sign up → onboard → talk to your agent. Screenshots/demo video of Telegram interaction.
- **Security** — Dedicated page. Per-user isolation, container architecture, encryption, audit logging. This is the differentiator — sell it hard.
- **Docs / Getting Started** — Bootstrap guide, skill documentation, integration setup guides, FAQ.
- **Blog** — SEO play. Security comparisons, tutorials, updates.
- **Account / Dashboard link** — Login redirects to management dashboard (Tailscale-authenticated).

### Tech
- Static site (Next.js, Astro, or Hugo) — AI-generated and iterated.
- Hosted on the same server or a cheap VPS / Cloudflare Pages (this one DOES need to be public-facing).
- Square checkout integration for subscription purchase.
- Signup flow provisions the user's K3s namespace and kicks off bootstrap onboarding.

### Domain & Brand
- [ ] Choose product name and domain.
- [ ] Logo, basic brand identity.

---

## Data & Storage

| Data Type | Storage | Notes |
|---|---|---|
| Conversations | Postgres (per-user) | Summarization strategy to keep context window manageable |
| Memory (semantic, periodic, ephemeral) | mem0 → Postgres (per-user) | Per-user mem0 instance |
| Skills & config | MinIO (per-user bucket) | Git-versioned via dashboard |
| User files & uploads | MinIO (per-user bucket) | Files sent via Telegram land here |
| App artifacts & images | MinIO (per-user bucket) | Built by executor, deployed as pods |
| Knowledge base (RAG) | pgvector in Postgres (per-user) | Chunked + embedded uploaded documents |
| Audit logs | Postgres (per-user) | Append-only |
| Backups | External S3 bucket | Automated via Proxmox + scheduled dumps |

### Knowledge Base / RAG

Users can upload documents (PDFs, notes, handbooks, reference material) that the agent can reference when answering questions or performing tasks. Separate from conversational memory (mem0) — this is structured knowledge.

**Pipeline:**
- User uploads document via Telegram or MinIO.
- Document chunked and embedded (pgvector in the user's Postgres).
- When the coordinator processes a message, it queries the knowledge base for relevant chunks and includes them in context.
- Supports: PDFs, markdown, text files, HTML. Extensible to other formats.

**Use cases:**
- "Reference my company handbook when drafting emails."
- "Here are my Obsidian notes — use them for context."
- "I uploaded the API docs for X — help me integrate with it."

### Context Window Management
- Recent messages: full text.
- Older messages: summarized.
- Long-term recall: mem0 semantic search.
- Knowledge base: pgvector retrieval for uploaded documents.
- Coordinator dynamically decides how much context to include per request.

---

## Data Portability

**Export:** Skills (.md files) + bootstrap config (bot personality, name, preferences).
**Import:** Same — users can bring skills and config from another instance or community.

Conversation history and memory are platform-contextual and not part of the export scope.

---

## Liability

All liability for agent actions sits with the user. Covered in Terms of Service. Approval gates for destructive actions reinforce this — the user explicitly confirms before anything irreversible happens.

Legal docs needed: Terms of Service, Privacy Policy.

---

## Development Roadmap — Idea to Production

### Phase 0: Foundation (Weeks 1-3)
**Goal:** Infrastructure running, nothing smart yet.
- [ ] Proxmox setup on the dedicated server.
- [ ] K3s cluster deployed on Proxmox.
- [ ] Tailscale installed and ACLs configured.
- [ ] MinIO deployed as a shared service.
- [ ] Base container images built (coordinator, executor).
- [ ] Namespace provisioning script — "create a full user stack with one command."
- [ ] Basic Postgres and Redis deployed per-namespace.

### Phase 1: Single-User Agent MVP (Weeks 4-8)
**Goal:** One user can talk to an agent via Telegram that actually does things.
- [ ] Telegram bot integration — receive messages, send responses.
- [ ] Coordinator logic — LLM call via OpenRouter, basic planning.
- [ ] Executor — receives tasks from coordinator, runs them (terminal commands, file ops).
- [ ] Task state tracking in Postgres.
- [ ] `/status` and `/cancel` commands.
- [ ] 3-5 starter skills (.md format with YAML permission manifests).
- [ ] mem0 integration for conversation memory.
- [ ] Basic context window management (recent full, older summarized).
- [ ] Input sanitization layer.
- [ ] Heartbeat system — `HEARTBEAT.md` config, coordinator checks on interval.
- [ ] Basic RAG — upload a document, agent can reference it.
- **Milestone: You can chat with your agent and it does useful work.**

### Phase 2: Multi-User & Security (Weeks 9-13)
**Goal:** Multiple users, each fully isolated.
- [ ] Per-user namespace provisioning automated.
- [ ] mTLS between pods in each namespace.
- [ ] K3s NetworkPolicies enforced.
- [ ] Secrets management — encrypted credential store.
- [ ] Approval gates for destructive actions via Telegram.
- [ ] Audit logging — every agent action recorded.
- [ ] Per-user spending limits and metering.
- [ ] Parallel executor support (up to 5 concurrent).
- **Milestone: 5 beta users running simultaneously, isolated from each other.**

### Phase 3: Computer Use, Voice & Integrations (Weeks 14-18)
**Goal:** The agent becomes genuinely useful day-to-day.
- [ ] Browser agent pod (Playwright) — web automation with approval gates.
- [ ] Terminal/remote server access — Tailscale SSH, ACL-controlled, approval gates for destructive ops.
- [ ] Voice pipeline — Whisper in, ElevenLabs out, user-selectable voices.
- [ ] OAuth integration framework — reusable flow for connecting services.
- [ ] First integrations: Gmail, Google Calendar, GitHub.
- [ ] Event listener framework — trigger tasks on email arrival, webhook, etc.
- [ ] Self-improving agent — executor can propose new skills with user approval.
- **Milestone: Agent can manage email, calendar, browse the web, and learn new skills.**

### Phase 4: Dashboard & Observability (Weeks 19-22)
**Goal:** You can manage the platform without SSHing into the server.
- [ ] Admin dashboard frontend (AI-generated React/Svelte).
- [ ] Portainer for container management.
- [ ] Prometheus + Grafana for observability.
- [ ] User management, token tracking, audit log viewer.
- [ ] Config history with git-style diffs.
- [ ] Alerting (agent loops, pod crashes, cost spikes).
- **Milestone: Full operational visibility without touching a terminal.**

### Phase 5: Website & Billing (Weeks 23-26)
**Goal:** People can find you, sign up, and pay.
- [ ] Marketing website (static, AI-generated).
- [ ] Square subscription integration — $50 and $100 tiers.
- [ ] Signup → namespace provisioning → bootstrap onboarding flow (automated).
- [ ] 14-day trial implementation.
- [ ] Metering dashboard for API usage.
- [ ] Terms of Service and Privacy Policy.
- **Milestone: End-to-end flow — stranger finds website, signs up, pays, onboards, uses agent.**

### Phase 6: Beta Launch (Weeks 27-30)
**Goal:** Real users, real feedback, real bugs.
- [ ] 10-20 paying beta users.
- [ ] Gather feedback on UX, reliability, missing skills.
- [ ] Bug fixes and stability hardening.
- [ ] Additional integrations based on user demand (Notion, Outlook, Todoist, etc.).
- [ ] Skill community kickoff — share starter skills publicly.
- **Milestone: Stable enough that users rely on it daily.**

### Phase 7: Public Launch
**Goal:** Open the doors.
- [ ] Public marketing push.
- [ ] Documentation site complete.
- [ ] Blog content for SEO.
- [ ] Cron and event listener polished.
- [ ] Onboarding flow refined based on beta feedback.
- [ ] Scaling plan for beyond 50-100 users (second server, multi-node K3s).

---

## Open Questions

- [ ] ElevenLabs voice pricing — user-selectable voices, monitor per-user TTS cost.
- [ ] Square Subscriptions API — confirm metering/add-on support.
- [ ] Prompt injection resistance testing across OpenRouter models.
- [ ] First 5 skills to ship with.
- [ ] Legal review — ToS and Privacy Policy.
- [ ] Product name and domain.
