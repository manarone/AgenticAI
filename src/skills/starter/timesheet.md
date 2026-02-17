---
name: timesheet
version: "1.0.0"
risk_tier: L1
permissions:
  files:
    read: ["/workspace/timesheets", "/workspace/calendar"]
    write: []
  network:
    allow_domains: []
  env:
    allow: []
requires_approval_actions: []
---
Summarize available work logs and return a timesheet draft grouped by date and project.
