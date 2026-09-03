"""Request-scoped session factory and lifecycle context managers."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from services.db.engine import get_engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
