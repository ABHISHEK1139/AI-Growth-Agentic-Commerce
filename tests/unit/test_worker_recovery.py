"""Unit tests for background worker recovery jobs (Task 23, Requirement 18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from apps.worker.main import ScheduledJob, _sweep_expired_checkouts
from services.checkout.models import Checkout


def test_scheduled_job_due_and_run():
    handler = MagicMock()
    job = ScheduledJob(name="test_job", interval_seconds=10, handler=handler)

    now = 100.0
    assert job.due(now) is True

    job.run(now)
    assert handler.called
    assert job._last_run == now
    assert job.due(now + 5) is False
    assert job.due(now + 10) is True


def test_sweep_expired_checkouts_releases_inventory():
    now = datetime.now(UTC)
    expired_chk = Checkout(
        checkout_id="chk_exp_1",
        merchant_id="mrc_1",
        buyer_id="buy_1",
        offer_id="off_1",
        offer_version=1,
        status="created",
        subtotal_minor=500000,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        total_minor=500000,
        currency="INR",
        price_hash="hash_1",
        price_snapshot={},
        expires_at=now - timedelta(minutes=5),
        created_at=now - timedelta(minutes=20),
    )

    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.all.return_value = [expired_chk]
    mock_session.query.return_value.filter.return_value.with_for_update.return_value.all.return_value = [
        expired_chk
    ]
    mock_factory = MagicMock(
        return_value=MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock())
    )

    with (
        patch("apps.api.db.get_session_factory", return_value=mock_factory),
        patch("services.inventory.service.InventoryService.release_stock") as mock_release,
    ):
        _sweep_expired_checkouts()
        assert expired_chk.status == "expired"
        assert mock_release.called
