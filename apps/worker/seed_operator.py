"""Create the first console administrator. Refuses to invent a password.

    # interactive: prompts for the password twice
    python -m apps.worker.seed_operator --email admin@example.com

    # unattended, for a container entrypoint or a CI fixture
    SEED_ADMIN_PASSWORD=... python -m apps.worker.seed_operator --email ...

This exists because the project had no way to obtain a login at all. The session
endpoint took a role in the request body and issued a session for it, so the
console was reachable but not *secured* -- and once the credential-checking login
replaced it, a fresh database would have had no account to sign in with. Nothing
else in the tree creates one.

Two rules, both deliberate:

* **No default password.** An operator running this with no ``--password`` and no
  environment variable is prompted, rather than being given ``admin/admin``. A
  seeded default is the single most common way a real deployment ends up with a
  guessable ``MERCHANT_ADMIN``, and it survives every later hardening pass
  because nothing looks wrong.
* **Idempotent, and it says so.** Re-running against an account that exists
  prints that the account is present and changes nothing. It does *not* reset
  the password, because a seed script that silently resets credentials is a
  privilege-escalation primitive the moment anyone can run it.

If the account exists and you genuinely need to change its password, use
``python -m apps.worker.reset_operator_password --email ...``, which is a separate
command so the two intents never blur.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from sqlalchemy import select

from apps.api.config import get_settings
from apps.api.db import get_session_factory
from packages.observability.logging import configure_logging, get_logger
from packages.security.passwords import MIN_PASSWORD_LENGTH, password_problem
from packages.security.principals import Role
from services.catalog.models import Merchant
from services.connectors.operator_auth import create_operator, find_by_email

logger = get_logger(__name__)

#: The role ceiling a seeded account gets. ``merchant_admin`` covers the whole
#: console without being the platform role, so seeding a local account does not
#: mint a credential that outranks a real merchant admin in the same deployment.
DEFAULT_ROLE = Role.MERCHANT_ADMIN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.worker.seed_operator",
        description="Create the first console administrator for a merchant tenant.",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Login email. Must be unique across all operators.",
    )
    parser.add_argument(
        "--merchant-id",
        default=None,
        help="Merchant tenant to attach the account to. Defaults to DEFAULT_MERCHANT_ID.",
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Name shown in the console. Defaults to the local part of the email.",
    )
    parser.add_argument(
        "--role",
        default=DEFAULT_ROLE.value,
        choices=[role.value for role in Role if role is not Role.BUYER],
        help="Role ceiling for this account. A buyer account cannot use the console.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Password. Prefer the SEED_ADMIN_PASSWORD environment variable, or omit "
            "both to be prompted. Passing it as an argument puts it in the shell history."
        ),
    )
    parser.add_argument(
        "--merchant-name",
        default=None,
        help="Create the merchant row too, if it does not exist. Defaults to the tenant id.",
    )
    return parser


def _resolve_password(provided: str | None) -> str:
    """The password to use, from an argument, the environment, or a prompt.

    ``getpass`` is used rather than :func:`input` so the password is not echoed
    into the terminal scrollback or the shell transcript a CI system keeps.
    """
    candidate = provided or os.environ.get("SEED_ADMIN_PASSWORD")
    if candidate:
        problem = password_problem(candidate)
        if problem is not None:
            raise SystemExit(f"refusing to seed: {problem}")
        return candidate

    if not sys.stdin.isatty():
        raise SystemExit(
            "refusing to seed: no password supplied. Set SEED_ADMIN_PASSWORD, pass "
            "--password, or run this from a terminal so it can prompt."
        )
    first = getpass.getpass(f"Password for {MIN_PASSWORD_LENGTH}+ characters: ")
    second = getpass.getpass("Confirm: ")
    if first != second:
        raise SystemExit("refusing to seed: passwords did not match")
    problem = password_problem(first)
    if problem is not None:
        raise SystemExit(f"refusing to seed: {problem}")
    return first


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(level=get_settings().log_level, service="agentpay-seed-operator")

    password = _resolve_password(args.password)
    settings = get_settings()
    merchant_id = args.merchant_id or settings.default_merchant_id
    display_name = args.display_name or args.email.split("@")[0]
    role = Role(args.role)

    factory = get_session_factory()
    with factory() as session:
        # Checked before the merchant insert so a re-run against a populated
        # database reports "already exists" instead of reporting a duplicate
        # tenant, which would send the operator looking for the wrong problem.
        if find_by_email(session, args.email) is not None:
            logger.info(
                "operator account already exists; nothing changed",
                extra={"event": "OPERATOR_SEED_SKIPPED", "email": args.email},
            )
            return 0

        if (
            session.execute(
                select(Merchant).where(Merchant.merchant_id == merchant_id)
            ).scalar_one_or_none()
            is None
        ):
            # Created rather than required, for the same reason `seed_catalog`
            # creates its tenant: refusing here would make this script unable to
            # be the first thing an operator runs against a fresh database.
            session.add(
                Merchant(
                    merchant_id=merchant_id,
                    name=args.merchant_name or merchant_id,
                    status="active",
                )
            )
            session.flush()
            logger.info(
                "merchant tenant created",
                extra={"event": "MERCHANT_TENANT_CREATED", "merchant_id": merchant_id},
            )

        account = create_operator(
            session,
            merchant_id=merchant_id,
            email=args.email,
            display_name=display_name,
            password=password,
            role=role,
        )
        session.commit()

    logger.info(
        "operator account created; sign in at /login",
        extra={
            "event": "OPERATOR_SEEDED",
            "operator_id": account.operator_id,
            "email": account.email,
            "merchant_id": merchant_id,
            "role": role.value,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
