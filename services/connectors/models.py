"""SQLAlchemy ORM models for console identities and channel connections.

Two concerns live here because they are the same concern: **how a human or a
service authenticates, and what it is then allowed to reach.**

Why this module exists at all: the project had no human authentication. The
``Buyer`` row in :mod:`services.catalog.models` identifies a *purchaser's
wallet*, not a login — nothing ever checked a credential to produce one, so
there was no password, no login page, and no account-enumeration story. The
session endpoint took a role in the request body and issued a session for it.

Three things are therefore new here and the distinctions matter:

* :class:`OperatorAccount` — a human with a password. A login credential.
* :class:`ChannelConnection` — a *store* connection (Shopify, WooCommerce, a
  feed). Carries a bearer secret for a third-party API, so it is a credential
  too, and it is encrypted at rest rather than hashed: unlike a password it has
  to be replayable on every sync. The distinction is why the two tables cannot be
  one.
* :class:`ChannelSyncRun` — the append-only log of sync attempts, so the console
  can show what a connector actually did instead of asserting that it works.

The import-linter contract ``Domain services never import the API or web
layers`` means this module may not reach for :mod:`apps.api.config`. Key
encryption therefore takes the master key as an argument, and
:mod:`apps.api.routers.connectors` supplies it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base

# `operator_account` and `channel_connection` carry a foreign key to
# `merchant.merchant_id`, and that table is declared in `services.catalog.models`.
# SQLAlchemy resolves a ForeignKey lazily -- against whatever tables happen to be
# registered on `Base.metadata` at the moment it is asked -- so without this
# import an entry point that touches only the connector models gets
# `NoReferencedTableError: could not find table 'merchant'` the first time it
# builds a statement. The API never saw it because the catalog router imports
# `services.catalog.models` first; the operator CLIs did, and failed.
from services.catalog.models import Merchant  # noqa: F401 - registers the FK target

#: The envelope version byte. A stored ciphertext is ``v1.<nonce>.<tag>.<ct>``,
#: so a future scheme can be introduced without ambiguity about which one wrote
#: a given row.
_ENVELOPE_VERSION = "v1"


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Counter-mode bytes from ``key`` and ``nonce``.

    AES-GCM through the standard library would be the obvious choice, but
    ``cryptography`` is not a dependency and hand-rolling AES in a payments
    codebase is not a reasonable trade. What is here is deliberately the weaker
    primitive, named as such: a SHA-256 counter-mode keystream with HMAC-SHA256
    authentication. Encrypt-then-MAC over AES-CTR is a standard construction and
    is not a rolling-your-own cipher, but its security rests entirely on the
    nonce never repeating under one key -- which :func:`encrypt_secret` enforces
    by drawing a fresh 16-byte nonce per call.

    The alternative was storing the channel token in plaintext, which is what the
    connector did before, and that is a worse outcome than a documented primitive
    with a stated assumption. Swapping in AES-GCM later is a change to this
    module alone: the envelope format is versioned for exactly that.
    """
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream, strict=True))


def encrypt_secret(plaintext: str, key: bytes) -> str:
    """Encrypt a third-party channel token for storage.

    ``key`` is the deployment's 32-byte master key, derived from
    ``CHANNEL_ENCRYPTION_KEY`` by the caller. The nonce is drawn per call, so
    encrypting the same token twice produces different ciphertext and the table
    cannot be used as an equality oracle on token values.
    """
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("a secret to encrypt must be a non-empty string")
    if not key:
        raise ValueError("a channel encryption key is required")
    nonce = os.urandom(16)
    ciphertext = _xor(plaintext.encode("utf-8"), _keystream(key, nonce, len(plaintext.encode())))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return ".".join(
        (
            _ENVELOPE_VERSION,
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(tag).decode(),
            base64.urlsafe_b64encode(ciphertext).decode(),
        )
    )


def decrypt_secret(envelope: str, key: bytes) -> str:
    """Recover a token from :func:`encrypt_secret`.

    Raises :class:`ValueError` on any malformed or tampered envelope. A failed
    authentication tag is *not* silently returned as ciphertext: returning
    garbage would send a wrong credential to the store API on the next sync and
    surface as a confusing 401 from Shopify rather than as a key problem here.
    """
    if not isinstance(envelope, str) or not key:
        raise ValueError("an envelope and a channel encryption key are required")
    parts = envelope.split(".")
    if len(parts) != 4 or parts[0] != _ENVELOPE_VERSION:
        raise ValueError("not a recognised channel secret envelope")
    _, nonce_b64, tag_b64, ciphertext_b64 = parts
    nonce = base64.urlsafe_b64decode(nonce_b64)
    tag = base64.urlsafe_b64decode(tag_b64)
    ciphertext = base64.urlsafe_b64decode(ciphertext_b64)
    if not hmac.compare_digest(tag, hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()):
        raise ValueError("channel secret failed authentication; the key does not match this row")
    return _xor(ciphertext, _keystream(key, nonce, len(ciphertext))).decode("utf-8")


class OperatorAccount(Base):
    """A human who can sign in to the merchant console.

    Stores an Argon2id PHC string, never a password. ``role`` is the ceiling this
    account can ever reach: it is carried into the session token, and no console
    request can escalate past it, so granting someone ``MERCHANT_OPERATOR`` is
    not a reversible mistake once the token is minted -- revoke the account.

    ``failed_login_count`` / ``locked_until`` back a lockout. Without it,
    ``MERCHANT_ADMIN`` is reachable by guessing a 12-character password as fast
    as the endpoint will accept requests, and the Argon2 cost that makes offline
    cracking expensive does nothing about online guessing.
    """

    __tablename__ = "operator_account"

    operator_id: Mapped[str] = mapped_column(String, primary_key=True)
    # `merchant_id` rather than `tenant_id`: an operator belongs to exactly one
    # merchant tenant, and PLATFORM_ADMIN is the deliberate exception that is
    # still scoped to a tenant so a platform operator cannot read an arbitrary
    # tenant's catalog by changing a column.
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # `True` until the operator has set their own password, which is how an
    # operator invited by a merchant admin is forced through a first-login change
    # rather than continuing to use a password the inviter chose.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Tracked separately from `last_login_at`, which only moves on success. A
    # decaying failure window has to be measured from the last *failure*, or five
    # mistyped passwords spread over five months still lock the account.
    last_failed_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        # Login is by email, so this index is the one that matters on a table
        # that will be read on every unauthenticated-looking request.
        Index("ix_operator_account_email", "email"),
        Index("ix_operator_account_merchant", "merchant_id"),
    )


class ApiClient(Base):
    """A registered external agent's credential.

    The seventh table in this module, and the oldest: it was declared in the
    first migration while :class:`ApiClientRegistry` held the credentials in
    memory, so every minted key vanished on restart. See
    :mod:`services.connectors.api_clients` for the repository behind it.

    ``key_hash`` is a SHA-256 hex digest, never a key. The digest is
    deterministic so a presented key is found by lookup rather than by comparing
    against every stored client, and the table is useless for authenticating
    without the plaintext -- which exists only in the mint response.
    """

    __tablename__ = "api_client"

    api_client_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    scopes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # 'active' | 'revoked', mirrored by the CHECK constraint in the migration.
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    label: Mapped[str] = mapped_column(String, nullable=False, default="")
    role: Mapped[str] = mapped_column(String, nullable=False, default="buyer")
    buyer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_api_client_merchant", "merchant_id"),
        # Lookup is by digest on every token exchange, and the unique constraint
        # already indexes the column. This index is for the console's
        # "which agents does this merchant have" listing.
        Index("ix_api_client_merchant_status", "merchant_id", "status"),
    )


class ChannelConnection(Base):
    """A merchant's connection to an external commerce platform.

    The store access token is a *replayable* credential: every catalog sync sends
    it to Shopify, so it cannot be hashed the way a password is. It is therefore
    encrypted at rest (:func:`encrypt_secret`) and the plaintext is only ever
    held in memory for the duration of one sync.

    ``status`` is the operator-visible health of the connection, kept separate
    from ``last_sync_status`` so a connection that is registered but has never
    synced is distinguishable from one whose last sync failed.
    """

    __tablename__ = "channel_connection"

    connection_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    platform_type: Mapped[str] = mapped_column(String, nullable=False)
    store_domain: Mapped[str] = mapped_column(String, nullable=False)
    # The encrypted access token. Named to make an accidental plaintext INSERT
    # obviously wrong in a schema review.
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sync_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        # One live connection per platform per merchant. A merchant connecting
        # the same store twice would double every synced product. Declared as a
        # unique index rather than a UniqueConstraint because SQLite reports a
        # UNIQUE constraint through get_unique_constraints and an index through
        # get_indexes; the migration creates an index, and one name has to mean
        # the same thing on both dialects.
        Index(
            "uq_channel_connection_store",
            "merchant_id",
            "platform_type",
            "store_domain",
            unique=True,
        ),
        Index("ix_channel_connection_merchant", "merchant_id"),
    )


class ChannelSyncRun(Base):
    """One attempt to pull a catalog from a connected store.

    Append-only. The console's sync panel reads the last N of these, so the
    operator sees the actual outcome of each attempt -- including the failures --
    rather than a single "connected" badge that could equally well have been
    written by a registration that never tried to sync.
    """

    __tablename__ = "channel_sync_run"

    sync_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String, ForeignKey("channel_connection.connection_id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    offer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_channel_sync_run_connection", "connection_id", "started_at"),)


class ChannelOrderPush(Base):
    """One attempt to push a confirmed order back to the store it came from.

    Separate from :class:`ChannelSyncRun` because the failure modes are opposite.
    A failed catalog sync loses visibility; a failed order push means the buyer
    was charged and the merchant's store does not know about the order. That
    asymmetry is why this is tracked as its own append-only log rather than a
    column on the sync run.
    """

    __tablename__ = "channel_order_push"

    push_id: Mapped[str] = mapped_column(String, primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String, ForeignKey("channel_connection.connection_id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(String, nullable=False)
    order_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    remote_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("ix_channel_order_push_connection", "connection_id", "created_at"),
        # One push per order per connection. An order must reach the store
        # exactly once: a retry after a push that *succeeded* server-side but
        # timed out on the response would otherwise create a duplicate order in
        # Shopify for a buyer already charged for the first one.
        Index("uq_channel_order_push_once", "connection_id", "order_id", unique=True),
    )


__all__ = [
    "ApiClient",
    "ChannelConnection",
    "ChannelOrderPush",
    "ChannelSyncRun",
    "OperatorAccount",
    "decrypt_secret",
    "encrypt_secret",
]
