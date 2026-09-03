"""Phase 9 operations utilities: database backup and restore scripts.

Both ``backup.py`` and ``restore.py`` are exposed as ``python -m`` entry
points so CI and operator runbooks can invoke them the same way::

    python -m infra.operations.backup --dest /var/backups/agentpay
    python -m infra.operations.restore --snapshot /var/backups/agentpay/<file>.sql.gz

The tests in ``tests/operations/`` exercise the script's argument
handling and redaction logic without touching a real database, so the
CI integration job can rely on the same code path the operator uses.
"""

from __future__ import annotations

from .backup import backup_database, prune_old_backups
from .restore import restore_snapshot

__all__ = [
    "backup_database",
    "prune_old_backups",
    "restore_snapshot",
]
