"""API keys are stored hashed and compared in constant time.

The three properties asserted here are the ones that matter if the database is ever
read by someone who should not have it: the plaintext is not in the store, the
comparison does not leak a prefix through timing, and a revoked key stops working.

`inspect.getsource` is used to assert the comparison primitive. A timing benchmark
would be the direct test and would also be the flakiest test in the suite on a
shared CI runner; asserting that `hmac.compare_digest` is the operation, plus the
behavioural tests around it, is the honest version of the same claim.

agentpay:allow-credential-shapes - generates real-shaped API keys at runtime.
"""

from __future__ import annotations

import hashlib
import inspect

import pytest

from packages.security import apikeys
from packages.security.apikeys import (
    API_KEY_PREFIX,
    MAX_API_KEY_LENGTH,
    ApiClient,
    ApiClientRegistry,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from packages.security.principals import Role, Scope

MERCHANT = "merchant_demo"


class TestGeneration:
    def test_a_key_is_prefixed_and_long(self) -> None:
        key = generate_api_key()

        assert key.startswith(API_KEY_PREFIX)
        # 32 bytes base64url-encoded, so comfortably past anything guessable.
        assert len(key) >= 40

    def test_keys_do_not_repeat(self) -> None:
        assert len({generate_api_key() for _ in range(200)}) == 200


class TestHashing:
    def test_the_digest_is_not_the_key(self) -> None:
        key = generate_api_key()
        digest = hash_api_key(key)

        assert key not in digest
        assert digest != key
        assert len(digest) == 64

    def test_hashing_is_deterministic(self) -> None:
        key = generate_api_key()

        assert hash_api_key(key) == hash_api_key(key)

    def test_a_one_character_difference_changes_the_digest(self) -> None:
        assert hash_api_key("ak_aaaa") != hash_api_key("ak_aaab")

    @pytest.mark.parametrize("bad", ["", "x" * (MAX_API_KEY_LENGTH + 1)])
    def test_an_implausible_key_is_refused(self, bad: str) -> None:
        with pytest.raises(ValueError):
            hash_api_key(bad)


class TestConstantTimeComparison:
    def test_the_comparison_uses_a_constant_time_primitive(self) -> None:
        """A plain `==` on a hex digest returns early on the first differing byte,
        which leaks the prefix and makes brute force cheap."""
        source = inspect.getsource(apikeys.verify_api_key)

        assert "compare_digest" in source
        assert "hmac" in inspect.getsource(apikeys)

    def test_a_matching_key_verifies(self) -> None:
        key = generate_api_key()

        assert verify_api_key(key, hash_api_key(key)) is True

    def test_a_near_miss_does_not_verify(self) -> None:
        key = generate_api_key()

        assert (
            verify_api_key(key[:-1] + ("a" if key[-1] != "a" else "b"), hash_api_key(key)) is False
        )

    @pytest.mark.parametrize("presented", ["", "x" * (MAX_API_KEY_LENGTH + 1)])
    def test_a_malformed_presentation_is_false_not_an_exception(self, presented: str) -> None:
        """The caller is asking "does this match", and a malformed credential simply
        does not. Raising here would turn a denied request into a 500."""
        assert verify_api_key(presented, hash_api_key(generate_api_key())) is False

    def test_an_empty_stored_hash_never_matches(self) -> None:
        assert verify_api_key(generate_api_key(), "") is False


class TestApiClient:
    def test_a_plaintext_key_cannot_be_stored_as_the_hash(self) -> None:
        """The one mistake that would defeat the whole module."""
        with pytest.raises(ValueError, match="plaintext"):
            ApiClient(
                client_id="apc_1",
                key_hash=generate_api_key().ljust(64, "0")[:64],
                merchant_id=MERCHANT,
                role=Role.BUYER,
                buyer_id="buyer_ada",
            )

    def test_a_non_digest_hash_is_refused(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ApiClient(
                client_id="apc_1",
                key_hash="short",
                merchant_id=MERCHANT,
                role=Role.BUYER,
                buyer_id="buyer_ada",
            )

    def test_scopes_beyond_the_role_are_refused(self) -> None:
        with pytest.raises(ValueError, match="may not be granted"):
            ApiClient(
                client_id="apc_1",
                key_hash=hashlib.sha256(b"x").hexdigest(),
                merchant_id=MERCHANT,
                role=Role.MERCHANT_OPERATOR,
                scopes=frozenset({Scope.PAYMENT_WRITE}),
            )


class TestRegistry:
    def test_issue_returns_the_plaintext_once_and_stores_only_a_digest(self) -> None:
        registry = ApiClientRegistry()

        api_key, client = registry.issue(
            merchant_id=MERCHANT, role=Role.BUYER, buyer_id="buyer_ada", label="demo agent"
        )

        assert client.key_hash == hash_api_key(api_key)
        # Nothing anywhere in the registry holds the plaintext.
        assert api_key not in repr(registry.clients())
        assert api_key not in repr(client)

    def test_the_stored_client_carries_no_attribute_equal_to_the_key(self) -> None:
        registry = ApiClientRegistry()
        api_key, client = registry.issue(merchant_id=MERCHANT, role=Role.BUYER, buyer_id="b1")

        for field in ApiClient.__dataclass_fields__:
            assert getattr(client, field) != api_key

    def test_a_registered_key_resolves_to_its_client(self) -> None:
        registry = ApiClientRegistry()
        api_key, client = registry.issue(
            merchant_id=MERCHANT,
            role=Role.BUYER,
            buyer_id="buyer_ada",
            scopes={Scope.CATALOG_READ},
        )

        resolved = registry.resolve(api_key)

        assert resolved is not None
        assert resolved.client_id == client.client_id
        assert resolved.scopes == {Scope.CATALOG_READ}

    def test_an_unknown_key_resolves_to_nothing(self) -> None:
        registry = ApiClientRegistry()
        registry.issue(merchant_id=MERCHANT, role=Role.BUYER, buyer_id="buyer_ada")

        assert registry.resolve(generate_api_key()) is None

    @pytest.mark.parametrize("presented", ["", "x" * (MAX_API_KEY_LENGTH + 1)])
    def test_a_malformed_key_resolves_to_nothing(self, presented: str) -> None:
        assert ApiClientRegistry().resolve(presented) is None

    def test_a_revoked_client_stops_resolving(self) -> None:
        """Revocation is checked here rather than left to each caller to remember."""
        registry = ApiClientRegistry()
        api_key, client = registry.issue(merchant_id=MERCHANT, role=Role.BUYER, buyer_id="b1")

        assert registry.revoke(client.client_id) is True
        assert registry.resolve(api_key) is None
        assert registry.revoke("apc_does_not_exist") is False

    def test_two_clients_do_not_collide(self) -> None:
        registry = ApiClientRegistry()
        first_key, first = registry.issue(merchant_id=MERCHANT, role=Role.BUYER, buyer_id="b1")
        second_key, second = registry.issue(
            merchant_id="merchant_other", role=Role.BUYER, buyer_id="b2"
        )

        assert len(registry) == 2
        assert registry.resolve(first_key) == first
        assert registry.resolve(second_key) == second

    def test_a_scope_request_is_capped_by_the_role_at_registration(self) -> None:
        from packages.errors.exceptions import ForbiddenError

        registry = ApiClientRegistry()

        with pytest.raises(ForbiddenError):
            registry.issue(
                merchant_id=MERCHANT,
                role=Role.MERCHANT_OPERATOR,
                scopes={Scope.CATALOG_READ, Scope.PAYMENT_WRITE},
            )

    def test_registering_the_same_digest_twice_is_refused(self) -> None:
        registry = ApiClientRegistry()
        _, client = registry.issue(merchant_id=MERCHANT, role=Role.BUYER, buyer_id="b1")

        with pytest.raises(ValueError, match="already registered"):
            registry.add(client)
