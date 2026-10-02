"""A store that cannot be reached must not read as a store with no products.

The live Shopify and WooCommerce fetchers used to catch every failure and return
an empty list. The caller could not tell that apart from a genuinely empty
catalog, so a dead network, a revoked token, and an empty store were all
reported to the merchant as a successful sync of zero products -- which the sync
log then wrote over the connection's real product count.

These tests pin the distinction. They never touch the network: `httpx` is
replaced, so the assertions are about the error the connector raises, not about
Shopify being reachable from the test machine.
"""

from __future__ import annotations

import httpx
import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.connectors.ecommerce_platform import ShopifyWooConnector

TOKEN = "shpat_test_token_value"
DOMAIN = "mystore.myshopify.com"


def _connector(flavor: str = "shopify") -> ShopifyWooConnector:
    return ShopifyWooConnector(
        merchant_id="merchant_demo",
        platform_flavor=flavor,
        store_domain=DOMAIN,
        access_token=TOKEN,
    )


@pytest.fixture
def fake_get(monkeypatch: pytest.MonkeyPatch):
    """Replace the store call. Returns a setter for the response to return."""

    def install(*, status: int = 200, payload: object = None, raises: Exception | None = None):
        def handler(url: str, **kwargs: object) -> httpx.Response:
            if raises is not None:
                raise raises
            return httpx.Response(
                status_code=status, json=payload if payload is not None else {"products": []}
            )

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def get(self, url: str, **kwargs: object) -> httpx.Response:
                return handler(url, **kwargs)

        monkeypatch.setattr(httpx, "Client", _Client)

    return install


# --- Shopify --------------------------------------------------------------


def test_a_network_failure_raises_rather_than_returning_nothing(
    fake_get,
) -> None:
    fake_get(raises=httpx.ConnectError("name resolution failed"))

    with pytest.raises(DomainError) as excinfo:
        _connector()._fetch_live_shopify(limit=10)  # noqa: SLF001 - the unit under test

    assert excinfo.value.code is ErrorCode.CONNECTOR_UNREACHABLE
    assert DOMAIN in excinfo.value.message


def test_a_revoked_token_names_the_problem(fake_get) -> None:
    """The one message the merchant can act on.

    "The store could not be reached" and "your token is wrong" lead to opposite
    actions, and collapsing them into one failure is why the sync log has to
    carry the store's own message.
    """
    fake_get(status=401, payload={"errors": "Invalid API key or access token"})

    with pytest.raises(DomainError) as excinfo:
        _connector()._fetch_live_shopify(limit=10)  # noqa: SLF001

    assert excinfo.value.code is ErrorCode.CONNECTOR_UNAUTHORIZED
    assert "revoked" in excinfo.value.message.lower()


def test_rate_limiting_is_distinguished(fake_get) -> None:
    fake_get(status=429)

    with pytest.raises(DomainError) as excinfo:
        _connector()._fetch_live_shopify(limit=10)  # noqa: SLF001

    assert excinfo.value.code is ErrorCode.CONNECTOR_RATE_LIMITED
    assert excinfo.value.spec.is_retryable is True


def test_a_server_error_is_distinguished(fake_get) -> None:
    fake_get(status=503)

    with pytest.raises(DomainError) as excinfo:
        _connector()._fetch_live_shopify(limit=10)  # noqa: SLF001

    assert excinfo.value.code is ErrorCode.CONNECTOR_ERROR


def test_a_genuinely_empty_store_returns_empty(fake_get) -> None:
    """The distinction the whole change exists for: empty is not unreachable."""
    fake_get(status=200, payload={"products": []})

    assert _connector()._fetch_live_shopify(limit=10) == []  # noqa: SLF001


def test_products_are_returned_on_success(fake_get) -> None:
    fake_get(
        status=200,
        payload={
            "products": [
                {
                    "id": 1,
                    "title": "Cotton Shirt",
                    "vendor": "Acme",
                    "product_type": "Shirts",
                    "variants": [
                        {"id": 11, "price": "1499.00", "inventory_quantity": 7},
                    ],
                }
            ]
        },
    )

    raw = _connector()._fetch_live_shopify(limit=10)  # noqa: SLF001

    assert len(raw) == 1
    product, offers = _connector().parse_shopify_product(raw[0])
    assert product.title == "Cotton Shirt"
    assert offers[0].unit_price_minor == 149900
    assert offers[0].available_stock == 7


def test_a_non_shopify_domain_is_not_fetched_live(fake_get) -> None:
    """No network call at all for a domain that is not a Shopify store.

    Returns empty rather than raising: the connector is a dual Shopify/WooCommerce
    adapter, so a non-matching domain means "not my platform", which is a
    different condition from "my platform failed".
    """
    connector = ShopifyWooConnector(
        merchant_id="merchant_demo",
        platform_flavor="shopify",
        store_domain="example.com",
        access_token=TOKEN,
    )

    assert connector._fetch_live_shopify(limit=10) == []  # noqa: SLF001


def test_no_token_means_no_live_fetch(fake_get) -> None:
    connector = ShopifyWooConnector(
        merchant_id="merchant_demo",
        platform_flavor="shopify",
        store_domain=DOMAIN,
        access_token=None,
    )

    assert connector._fetch_live_shopify(limit=10) == []  # noqa: SLF001


# --- WooCommerce ----------------------------------------------------------


def test_woocommerce_network_failure_raises(fake_get) -> None:
    fake_get(raises=httpx.ConnectTimeout("timed out"))

    with pytest.raises(DomainError) as excinfo:
        _connector("woocommerce")._fetch_live_woocommerce(limit=10)  # noqa: SLF001

    assert excinfo.value.code is ErrorCode.CONNECTOR_UNREACHABLE


def test_woocommerce_bad_credentials_raise(fake_get) -> None:
    fake_get(status=403)

    with pytest.raises(DomainError) as excinfo:
        _connector("woocommerce")._fetch_live_woocommerce(limit=10)  # noqa: SLF001

    assert excinfo.value.code is ErrorCode.CONNECTOR_UNAUTHORIZED


def test_woocommerce_empty_store_returns_empty(fake_get) -> None:
    fake_get(status=200, payload=[])

    assert _connector("woocommerce")._fetch_live_woocommerce(limit=10) == []  # noqa: SLF001


def test_the_error_code_reaches_the_registry(fake_get) -> None:
    """Every raised code must be one a client can be told about.

    A `DomainError` with an unregistered code would serialise as a generic
    internal error, losing the distinction this whole module draws.
    """
    from packages.errors.registry import ERROR_REGISTRY

    fake_get(status=401)

    with pytest.raises(DomainError) as excinfo:
        _connector()._fetch_live_shopify(limit=10)  # noqa: SLF001

    assert excinfo.value.code in ERROR_REGISTRY
    assert excinfo.value.spec.http_status == 401
