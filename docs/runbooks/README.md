# Operations Runbooks

This directory contains the on-call runbooks for AgentPay. Each
runbook is written to be read at 3 AM, by an engineer who has never
seen the system before, with the alert text in front of them.

## Index

* [Backup and Restore](./backup-restore.md) — routine backup
  verification, full-database restore, and point-in-time analysis.
* [Database Failure](./database-failure.md) — connection-pool
  exhaustion, slow queries, full outage.
* [Payment Incident](./payment-incident.md) — gateway / provider /
  local-state disagreement, recovery path.
* [Webhook Replay](./webhook-replay.md) — manual replay of a
  dead-letter webhook entry.

## How to use these runbooks

1. Read the **When to use this runbook** section. If your alert does
   not match, the runbook is the wrong one.
2. The first step is always a **read-only** action (a SQL query, a
   curl, a log search). Do not modify state until you have
   triangulated.
3. The **Don't do this** section at the bottom of each runbook lists
   the reflexes that are tempting and wrong. Read it before doing
   anything irreversible.
4. Every runbook ends with a **Postmortem** step. The incident is
   not closed until the postmortem is filed.

## Severity definitions

| Severity | Definition | Response time |
| -------- | ---------- | ------------- |
| **P1**   | Customer-visible outage, money at risk, security incident. | 15 min |
| **P2**   | Degraded service, no customer impact yet, error rate above SLO. | 1 hour |
| **P3**   | Internal tooling, cosmetic, documentation drift. | 1 business day |
