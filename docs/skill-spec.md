# Skill Spec (MVP)

Skills are Markdown files with YAML frontmatter.

## Required Frontmatter
- `name` (string)
- `version` (string)
- `risk_tier` (`L1`/`L2`/`L3`)
- `permissions.files.read[]`
- `permissions.files.write[]`
- `permissions.network.allow_domains[]`
- `permissions.env.allow[]`
- `requires_approval_actions[]`

## Example
```md
---
name: timesheet
version: "1.0.0"
risk_tier: L1
permissions:
  files:
    read: ["/workspace/timesheets"]
    write: []
  network:
    allow_domains: []
  env:
    allow: []
requires_approval_actions: []
---
Summarize timesheet records.
```

## Execution Rules
- Manifest validates before execution.
- Destructive actions listed in `requires_approval_actions[]` always require explicit approval.
- `risk_tier` should reflect blast radius: `L1` read-only/low impact, `L2` bounded writes, `L3` destructive or irreversible.
- Permissions are enforced before any file/network operation.
- Skills should be idempotent where possible.
