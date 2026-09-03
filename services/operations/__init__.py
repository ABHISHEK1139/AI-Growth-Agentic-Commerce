"""Phase 9 operations module: alerting, dead-letter replay, and SLOs.

This module is the in-process alerting layer. It does not call PagerDuty or
Slack — the gateway is the wrong place to depend on a third-party SaaS for a
core reliability signal. Instead, it exposes a structured ``AlertManager`` and
a small set of severity-classified alert types that the worker and the API
publish into. A separate ``alert-exporter`` process (or a sidecar) is
expected to forward ``AlertManager.drain()`` to whatever channel the
operator has configured.

The contract is the structured payload. Every alert has:

* a ``kind`` — the symbolic name of the alert, e.g. ``webhook_dead_letter``
* a ``severity`` — one of ``info``, ``warning``, ``critical``
* a ``message`` — a human-readable one-liner, safe to log
* a ``context`` — structured fields, no credentials
* a ``created_at`` — UTC ISO-8601

Alerts never carry credentials. The ``AlertManager`` is paranoid about
what it accepts in ``context``: any value that looks like a Razorpay key
prefix, a Razorpay signature, or a long base64 token is dropped with a
warning log. A misconfigured caller cannot accidentally leak a webhook
secret into the alerting channel.
"""

from __future__ import annotations

from .alerts import Alert, AlertKind, AlertManager, AlertSeverity

__all__ = [
    "Alert",
    "AlertKind",
    "AlertManager",
    "AlertSeverity",
]
