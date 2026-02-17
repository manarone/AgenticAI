---
name: file_organizer
version: "1.0.0"
risk_tier: L2
permissions:
  files:
    read: ["/workspace/uploads"]
    write: ["/workspace/uploads"]
  network:
    allow_domains: []
  env:
    allow: []
requires_approval_actions: ["delete", "move"]
---
Organize files into folder buckets based on extension and naming conventions. Ask for approval before delete/move operations.
