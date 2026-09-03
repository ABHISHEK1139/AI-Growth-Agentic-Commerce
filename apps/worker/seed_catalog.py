"""Import catalog artifacts into PostgreSQL and publish them.

Before this existed there was no wiring at all between JSONL catalog artifacts and
the database. ``CatalogService.import_catalog_artifacts`` could read them and
nothing called it outside the test suite, so the searchable catalog in a running
deployment was whatever had been inserted by hand — in practice, nothing. That is
what pushed the natural-language surface onto a hardcoded product list.

This is the missing operator step, and it is deliberately generic about its input::

    # the committed demo seed (default)
    python -m apps.worker.seed_catalog

    # the output of `python -m pipeline.build_catalog all`
    python -m apps.worker.seed_catalog --products data/out/catalog/products.jsonl \
                                      --offers   data/out/catalog/offers.jsonl

Lives under ``apps/`` rather than ``pipeline/`` because the import contract keeps
the pipeline standalone: it must not import domain services. A CLI that writes to
the database is not part of the pipeline.

Import is idempotent on the source checksum, so re-running against unchanged files
returns the existing version instead of duplicating it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

from apps.api.config import get_settings
from apps.api.db import get_session_factory
from packages.observability.logging import configure_logging, get_logger
from services.catalog.models import Merchant
from services.catalog.service import CatalogService
from services.offers.seed import SEED_OFFERS_PATH, SEED_PRODUCTS_PATH

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.worker.seed_catalog",
        description="Import catalog artifacts into PostgreSQL and publish the version.",
    )
    parser.add_argument(
        "--products",
        type=Path,
        default=SEED_PRODUCTS_PATH,
        help="products.jsonl to import (default: the committed demo seed)",
    )
    parser.add_argument(
        "--offers",
        type=Path,
        default=SEED_OFFERS_PATH,
        help="offers.jsonl to import (default: the committed demo seed)",
    )
    parser.add_argument(
        "--merchant-id",
        default=None,
        help="merchant to import for (default: the configured default merchant)",
    )
    parser.add_argument(
        "--source-name",
        default="seed_catalog",
        help="recorded on the import run for provenance",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="leave the imported version in draft instead of publishing it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, service=f"{settings.app_name}-seed")

    merchant_id: str = args.merchant_id or settings.default_merchant_id
    products_path: Path = args.products
    offers_path: Path = args.offers

    if not products_path.exists():
        logger.error(
            "products artifact not found",
            extra={"event": "SEED_ARTIFACT_MISSING", "path": str(products_path)},
        )
        return 2

    factory = get_session_factory()
    with factory() as session:
        # The catalog tables carry a foreign key to `merchant`, so a first import
        # into an empty database needs the tenant row to exist. Created rather than
        # required because refusing here would mean the seed step could never be
        # the first thing an operator runs.
        existing = session.execute(
            select(Merchant).where(Merchant.merchant_id == merchant_id)
        ).scalar_one_or_none()
        if existing is None:
            session.add(Merchant(merchant_id=merchant_id, name=merchant_id, status="active"))
            session.flush()

        version = CatalogService().import_catalog_artifacts(
            session,
            merchant_id=merchant_id,
            products_path=products_path,
            offers_path=offers_path,
            source_name=args.source_name,
        )

        published = False
        if not args.no_publish:
            published = CatalogService().publish_catalog(
                session,
                merchant_id=merchant_id,
                catalog_version_id=version.catalog_version_id,
            )

        session.commit()

    logger.info(
        "catalog seeded",
        extra={
            "event": "CATALOG_SEEDED",
            "merchant_id": merchant_id,
            "catalog_version_id": version.catalog_version_id,
            "product_count": version.product_count,
            "valid_count": version.valid_count,
            "needs_review_count": version.needs_review_count,
            "published": published,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
