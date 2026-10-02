"""Teach SQLite the PostgreSQL column types the models use.

The models are written against PostgreSQL, but most of the suite runs on SQLite
so it needs no server. ``JSONB`` and ``ARRAY`` have no SQLite equivalent and
raise ``CompileError`` at DDL time, which would make every test that builds a
schema fail before it reached the behaviour it was written to check.

This was previously copy-pasted into five test modules. A duplicate registration
is harmless but a *missing* one is not: a new test that creates tables gets a
confusing ``CompileError`` instead of a working database, and the natural first
reaction is to assume the code under test is broken. One definition, imported
wherever a schema is built.

Registering the same type twice for the same dialect is a no-op rather than an
error, so importing this module is always safe.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202, ARG001
    return "TEXT"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202, ARG001
    return "TEXT"
