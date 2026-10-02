"""Reset a console operator's password, with the current one in hand.

    python -m apps.worker.reset_operator_password --email admin@example.com

Separate from ``seed_operator`` on purpose. A seed script that silently resets
credentials is a privilege-escalation primitive the moment anyone can run it: the
same command that provisions the first account would also take over an existing
one, and the audit trail would show the same thing for both. Naming the intent
separately means the command that can overwrite a working credential is never
the command an operator runs to stand the system up.

This still requires the account to exist and does not create one. It does not
send a reset email, because the deployment has no mail transport and adding one
for a rarely-used recovery path would be a larger change than the problem.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from apps.api.config import get_settings
from apps.api.db import get_session_factory
from packages.observability.logging import configure_logging, get_logger
from packages.security.passwords import password_problem
from services.connectors.operator_auth import find_by_email, set_password

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.worker.reset_operator_password",
        description="Set a new password for an existing console operator.",
    )
    parser.add_argument("--email", required=True, help="Login email of the account to reset.")
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "New password. Prefer SEED_ADMIN_PASSWORD, or omit both to be prompted. "
            "Passing it as an argument puts it in the shell history."
        ),
    )
    return parser


def _resolve_password(provided: str | None) -> str:
    candidate = provided or os.environ.get("SEED_ADMIN_PASSWORD")
    if candidate:
        problem = password_problem(candidate)
        if problem is not None:
            raise SystemExit(f"refusing to reset: {problem}")
        return candidate

    if not sys.stdin.isatty():
        raise SystemExit(
            "refusing to reset: no password supplied. Set SEED_ADMIN_PASSWORD, pass "
            "--password, or run this from a terminal so it can prompt."
        )
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Confirm: ")
    if first != second:
        raise SystemExit("refusing to reset: passwords did not match")
    problem = password_problem(first)
    if problem is not None:
        raise SystemExit(f"refusing to reset: {problem}")
    return first


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(level=get_settings().log_level, service="agentpay-reset-operator")

    password = _resolve_password(args.password)
    factory = get_session_factory()
    with factory() as session:
        account = find_by_email(session, args.email)
        if account is None:
            # Named explicitly, because this is an operator console command and a
            # typo'd address is far more likely here than an attack: the caller
            # is already authenticated to the machine. The anti-enumeration rule
            # in `authenticate` does not apply to an operator with a database
            # session in hand.
            logger.error(
                "no operator account for that address",
                extra={"event": "OPERATOR_PASSWORD_RESET_MISS", "email": args.email},
            )
            return 1
        set_password(session, account, password)
        session.commit()
        operator_id = account.operator_id

    logger.info(
        "operator password reset",
        extra={
            "event": "OPERATOR_PASSWORD_RESET",
            "operator_id": operator_id,
            "email": args.email,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
