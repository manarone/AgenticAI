---
name: status_summary
version: "1.0.0"
risk_tier: L1
permissions:
  files:
    read: ["/workspace/notes", "/workspace/tickets"]
    write: []
  network:
    allow_domains: []
  env:
    allow: []
requires_approval_actions: []
---
Read the latest notes and produce a concise daily status update with blockers and next actions.
