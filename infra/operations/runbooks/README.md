# Operations runbooks

The gateway's reliability story is only as good as the engineer's worst
day. These runbooks are written for the engineer who is reading them at
03:00 with a Slack ping in one window and a status page in another.

Each runbook is structured the same way:

1. **Symptoms** — the alert or user-visible problem that brought you here.
2. **Triage** — the first command to run, the log to read, the page to open.
3. **Mitigation** — the smallest action that takes the symptom away.
4. **Root cause** — the deeper investigation to do once the page is green.
5. **Postmortem checklist** — what to capture for the next retrospective.

| Runbook | Trigger |
| --- | --- |
| [`webhook-replay.md`](webhook-replay.md) | `WEBHOOK_RETRY_EXHAUSTED` alert; dead-letter queue growing |
| [`payment-incident.md`](payment-incident.md) | `PAYMENT_INCIDENT` or repeated `PROVIDER_ERROR` |
| [`reconciliation-mismatch.md`](reconciliation-mismatch.md) | `RECONCILIATION_MISMATCH` alert |
| [`database-failure.md`](database-failure.md) | API process restarting, jobs not draining |
| [`worker-stuck.md`](worker-stuck.md) | `WORKER_HEARTBEAT_LOST`; DLQ reconcile not running |

A runbook that doesn't get updated after an incident is a runbook
that's wrong. The postmortem checklist ends with "edit the runbook
with anything that surprised you".
