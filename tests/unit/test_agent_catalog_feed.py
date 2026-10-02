"""The agent-readable catalog feed.

An endpoint an external agent depends on is a contract, so this pins the parts a
consumer would otherwise only discover in production: that the feed is a
`DataFeed` at the top level rather than nested in this API's envelope, that the
price and ceiling are integers in minor units, and that the ceiling the feed
advertises is the one checkout actually enforces. An agent told it may spend up to
X and then refused at checkout has been told a lie.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


@pytest.fixture
def client(settings):  # noqa: ANN201 - inferred from the application factory
    return TestClient(create_app(settings))


def test_feed_is_a_top_level_jsonld_datafeed(client: TestClient) -> None:
    response = client.get("/api/v1/agent/catalog")

    assert response.status_code == 200
    body = response.json()
    # Top level, not under `data`: a JSON-LD consumer parsing this as a DataFeed
    # should not have to know this API's envelope to find the feed.
    assert body["@context"] == "https://schema.org/"
    assert body["@type"] == "DataFeed"
    assert body["count"] == len(body["items"])


def test_offer_prices_are_integer_minor_units(client: TestClient) -> None:
    body = client.get("/api/v1/agent/catalog").json()

    for item in body["items"]:
        price = item["offers"]["price_minor"]
        # Money is never a float anywhere in this system, and an agent that reads
        # 379900.0 back has been handed a value that may already have lost a paise.
        assert isinstance(price, int), f"{item['product_id']} price is {type(price).__name__}"
        assert price >= 0


def test_advertised_ceiling_is_the_one_checkout_enforces(client: TestClient, settings) -> None:  # noqa: ANN001
    body = client.get("/api/v1/agent/catalog").json()
    ceiling = body["merchant"]["policy_ceiling_minor"]

    assert ceiling == settings.max_transaction_amount_minor

    for item in body["items"]:
        price = item["offers"]["price_minor"]
        allowed = item["agentic_contract"]["autonomous_checkout_allowed"]
        assert allowed == (price <= ceiling), f"{item['product_id']} disagrees with the ceiling"


def test_availability_matches_the_reported_stock(client: TestClient) -> None:
    body = client.get("/api/v1/agent/catalog").json()

    for item in body["items"]:
        offer = item["offers"]
        expected = "InStock" if offer["stock"] > 0 else "OutOfStock"
        assert (
            offer["availability"] == expected
        ), f"{item['product_id']} stock/availability disagree"
