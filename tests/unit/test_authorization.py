"""Unit tests for authorization binding, approvals, and pre-payment gates (Task 17, Requirement 13, Property 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.authorization.models import Authorization
from services.authorization.service import AuthorizationService
from services.checkout.models import Checkout


def _sample_auth(
    auth_id: str = "ath_1",
    checkout_id: str = "chk_1",
    buyer_id: str = "buy_1",
    merchant_id: str = "merch_1",
    price_hash: str = "hash_valid",
    status: str = "approved",
    expires_in_minutes: int = 15,
) -> Authorization:
    now = datetime.now(UTC)
    return Authorization(
        authorization_id=auth_id,
        checkout_id=checkout_id,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        amount_ceiling_minor=5000000,
        currency="INR",
        price_hash=price_hash,
        policy_version="1.0",
        status=status,
        valid_until=now + timedelta(minutes=expires_in_minutes),
        created_at=now,
    )


# ---------------------------------------------------------------------------
# Property 5: Pre-payment gates and authorization binding
# ---------------------------------------------------------------------------


def test_property_5_revalidation_succeeds_for_matching_hash_and_checkout():
    auth = _sample_auth()
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = auth

    service = AuthorizationService()
    validated = service.revalidate_for_payment(
        session,
        authorization_id="ath_1",
        checkout_id="chk_1",
        current_price_hash="hash_valid",
    )
    assert validated.authorization_id == "ath_1"


def test_property_5_price_hash_mismatch_blocks_payment():
    """Requirement 13.5: Any mutation of price/terms blocks payment with PRICE_CHANGED."""
    auth = _sample_auth(price_hash="hash_old")
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = auth

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.revalidate_for_payment(
            session,
            authorization_id="ath_1",
            checkout_id="chk_1",
            current_price_hash="hash_new_modified",
        )
    assert exc_info.value.code == ErrorCode.PRICE_CHANGED


def test_authorization_cannot_pay_different_checkout():
    """Requirement 13.2: Authorization bound to checkout A cannot pay checkout B."""
    auth = _sample_auth(checkout_id="chk_A")
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = auth

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.revalidate_for_payment(
            session,
            authorization_id="ath_1",
            checkout_id="chk_B",
            current_price_hash="hash_valid",
        )
    assert exc_info.value.code == ErrorCode.AUTHORIZATION_CHECKOUT_MISMATCH


def test_expired_authorization_is_rejected():
    """Requirement 13.3: Expired authorization cannot be used for payment."""
    auth = _sample_auth(expires_in_minutes=-5)
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = auth

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.revalidate_for_payment(
            session,
            authorization_id="ath_1",
            checkout_id="chk_1",
            current_price_hash="hash_valid",
        )
    assert exc_info.value.code == ErrorCode.AUTHORIZATION_EXPIRED


def test_consumed_authorization_cannot_be_reused():
    """Requirement 13.4: An authorization already consumed cannot be used again."""
    auth = _sample_auth(status="consumed")
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = auth

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.revalidate_for_payment(
            session,
            authorization_id="ath_1",
            checkout_id="chk_1",
            current_price_hash="hash_valid",
        )
    assert exc_info.value.code == ErrorCode.AUTHORIZATION_ALREADY_CONSUMED


def test_revalidate_for_payment_enforces_tenant_scoping():
    """revalidate_for_payment delegates through AuthorizationRepository for tenant scoping (BUG-49)."""
    auth = _sample_auth()
    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = auth

    service = AuthorizationService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.authorization.service.AuthorizationRepository",
            lambda s, scope: mock_repo,
        )
        validated = service.revalidate_for_payment(
            session,
            authorization_id="ath_1",
            checkout_id="chk_1",
            current_price_hash="hash_valid",
            merchant_id="merch_1",
            buyer_id="buy_1",
        )
        assert validated.authorization_id == "ath_1"
        mock_repo.get_by_id.assert_called_once_with("ath_1")


def test_approve_authorization_updates_status_and_checkout():
    auth = _sample_auth(status="pending")
    mock_checkout = Checkout(
        checkout_id="chk_1",
        buyer_id="buy_1",
        merchant_id="merch_1",
        offer_id="off_1",
        offer_version=1,
        status="authorization_pending",
        subtotal_minor=5000000,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        total_minor=5000000,
        currency="INR",
        price_hash="hash_valid",
        price_snapshot={},
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        created_at=datetime.now(UTC),
    )

    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = auth
    session.query.return_value.filter.return_value.first.return_value = mock_checkout

    service = AuthorizationService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.authorization.service.AuthorizationRepository", lambda s, scope: mock_repo
        )
        mp.setattr(
            service,
            "get_authorization",
            lambda s, buyer_id, merchant_id, authorization_id: MagicMock(status="approved"),
        )
        res = service.approve_authorization(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            authorization_id="ath_1",
        )

    assert res.status == "approved"
    assert auth.status == "approved"
    assert mock_checkout.status == "authorized"


def test_request_authorization_not_found():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.request_authorization(
            session, buyer_id="buy_1", merchant_id="merch_1", checkout_id="chk_missing"
        )
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_request_authorization_expired():
    session = MagicMock()
    mock_checkout = MagicMock(spec=Checkout)
    mock_checkout.status = "expired"
    session.query.return_value.filter.return_value.first.return_value = mock_checkout

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.request_authorization(
            session, buyer_id="buy_1", merchant_id="merch_1", checkout_id="chk_1"
        )
    assert exc_info.value.code == ErrorCode.CHECKOUT_EXPIRED


def test_request_authorization_policy_blocked():
    session = MagicMock()
    mock_checkout = MagicMock(spec=Checkout)
    mock_checkout.status = "created"
    mock_checkout.total_minor = 10000000
    mock_checkout.currency = "INR"
    mock_checkout.price_hash = "hash_1"
    mock_checkout.offer_id = "off_1"
    session.query.return_value.filter.return_value.first.return_value = mock_checkout

    mock_policy = MagicMock()
    mock_policy.evaluate_checkout_policy.return_value = MagicMock(
        decision="BLOCK",
        reason_code=ErrorCode.AMOUNT_ABOVE_MAX_LIMIT.value,
        policy_version="1.0",
    )

    service = AuthorizationService(policy_service=mock_policy)
    with pytest.raises(DomainError) as exc_info:
        service.request_authorization(
            session, buyer_id="buy_1", merchant_id="merch_1", checkout_id="chk_1"
        )
    assert exc_info.value.code == ErrorCode.AMOUNT_ABOVE_MAX_LIMIT


def test_reject_authorization_success():
    auth = _sample_auth(status="pending")
    mock_checkout = MagicMock(spec=Checkout)
    mock_checkout.checkout_id = "chk_1"
    mock_checkout.status = "authorization_pending"

    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = auth
    session.query.return_value.filter.return_value.first.return_value = mock_checkout

    mock_inv = MagicMock()
    service = AuthorizationService(inventory_service=mock_inv)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.authorization.service.AuthorizationRepository", lambda s, scope: mock_repo
        )
        mp.setattr(
            service,
            "get_authorization",
            lambda s, buyer_id, merchant_id, authorization_id: MagicMock(status="rejected"),
        )
        res = service.reject_authorization(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            authorization_id="ath_1",
        )

    assert res.status == "rejected"
    assert auth.status == "rejected"
    assert mock_checkout.status == "cancelled"
    mock_inv.release_stock.assert_called_once_with(
        session, checkout_id="chk_1", merchant_id="merch_1"
    )


def test_reject_authorization_consumed_raises_error():
    """Attempting to reject an already consumed authorization raises AUTHORIZATION_ALREADY_CONSUMED (BUG-43)."""
    auth = Authorization(
        authorization_id="ath_consumed",
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_1",
        amount_ceiling_minor=5000,
        currency="INR",
        price_hash="hash",
        policy_version="1.0",
        status="consumed",
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = auth

    service = AuthorizationService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.authorization.service.AuthorizationRepository", lambda s, scope: mock_repo
        )
        with pytest.raises(DomainError) as exc_info:
            service.reject_authorization(
                session,
                buyer_id="buy_1",
                merchant_id="merch_1",
                authorization_id="ath_consumed",
            )
    assert exc_info.value.code == ErrorCode.AUTHORIZATION_ALREADY_CONSUMED


def test_reject_authorization_on_completed_checkout_raises_error():
    """Attempting to reject an authorization when checkout is already completed raises ALREADY_FINALIZED (BUG-43)."""
    auth = Authorization(
        authorization_id="ath_1",
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_completed",
        amount_ceiling_minor=5000,
        currency="INR",
        price_hash="hash",
        policy_version="1.0",
        status="approved",
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    mock_checkout = Checkout(
        checkout_id="chk_completed",
        merchant_id="merch_1",
        buyer_id="buy_1",
        offer_id="off_1",
        offer_version=1,
        status="completed",
        subtotal_minor=5000,
        total_minor=5000,
        currency="INR",
        price_hash="hash",
        price_snapshot={},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = auth
    session.query.return_value.filter.return_value.first.return_value = mock_checkout

    service = AuthorizationService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.authorization.service.AuthorizationRepository", lambda s, scope: mock_repo
        )
        with pytest.raises(DomainError) as exc_info:
            service.reject_authorization(
                session,
                buyer_id="buy_1",
                merchant_id="merch_1",
                authorization_id="ath_1",
            )
    assert exc_info.value.code == ErrorCode.ALREADY_FINALIZED


def test_get_authorization_not_found():
    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = None

    service = AuthorizationService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.authorization.service.AuthorizationRepository", lambda s, scope: mock_repo
        )
        with pytest.raises(DomainError) as exc_info:
            service.get_authorization(
                session,
                buyer_id="buy_1",
                merchant_id="merch_1",
                authorization_id="ath_missing",
            )

    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_request_authorization_returns_existing_when_called_twice():
    """request_authorization returns existing active authorization idempotently rather than inserting a duplicate (BUG-44)."""
    checkout = Checkout(
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        offer_id="off_1",
        offer_version=1,
        status="created",
        subtotal_minor=5000,
        total_minor=5000,
        currency="INR",
        price_hash="hash",
        price_snapshot={},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    existing_auth = Authorization(
        authorization_id="ath_existing",
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_1",
        amount_ceiling_minor=5000,
        currency="INR",
        price_hash="hash",
        policy_version="1.0",
        status="approved",
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )

    session = MagicMock()
    # First query returns checkout, second query returns existing_auth, third/fourth return offer/product/policy
    session.query.return_value.filter.return_value.first.side_effect = [
        checkout,
        existing_auth,
        None,
        None,
    ]
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    service = AuthorizationService()
    res = service.request_authorization(
        session,
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_1",
    )
    assert res.authorization_id == "ath_existing"
    assert res.status == "approved"
    # Ensure no new authorization was added
    session.add.assert_not_called()


def test_request_authorization_consumed_raises_error():
    """request_authorization on an already consumed authorization raises AUTHORIZATION_ALREADY_CONSUMED (BUG-44)."""
    checkout = Checkout(
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        offer_id="off_1",
        offer_version=1,
        status="created",
        subtotal_minor=5000,
        total_minor=5000,
        currency="INR",
        price_hash="hash",
        price_snapshot={},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    existing_auth = Authorization(
        authorization_id="ath_consumed",
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_1",
        amount_ceiling_minor=5000,
        currency="INR",
        price_hash="hash",
        policy_version="1.0",
        status="consumed",
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )

    session = MagicMock()
    session.query.return_value.filter.return_value.first.side_effect = [
        checkout,
        existing_auth,
    ]

    service = AuthorizationService()
    with pytest.raises(DomainError) as exc_info:
        service.request_authorization(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
        )
    assert exc_info.value.code == ErrorCode.AUTHORIZATION_ALREADY_CONSUMED
