import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

# Import the actual global FastAPI application context instance
from src.smartmatch.main import app
import src.smartmatch.main as main_module


@pytest.mark.asyncio
async def test_api_health_endpoint_success() -> None:
    """
    Validates /health yields 200 OK using a Supabase-compatible mock.
    """
    mock_db = AsyncMock()
    # Updated: health_check should now simulate a Supabase SDK/Redis ping
    mock_db.health_check = AsyncMock(return_value=True)

    with patch.object(main_module, 'db_manager', mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["database"] == "connected (Supabase SDK)"


@pytest.mark.asyncio
async def test_api_health_endpoint_failure_uninstantiated() -> None:
    """
    Validates /health returns 503 when the database manager is uninstantiated.
    """
    with patch.object(main_module, 'db_manager', None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

    assert response.status_code == 503
    data = response.json() if response.content else {}
    error_text = data.get("detail") or data.get("message") or data.get("error") or response.text
    assert "not initialized" in error_text


@pytest.mark.asyncio
async def test_api_health_endpoint_failure_unreachable() -> None:
    """
    Validates /health returns 503 and unhealthy status when the database health check fails.
    """
    mock_db = AsyncMock()
    # Simulating a failed Supabase/Redis connection attempt
    mock_db.health_check = AsyncMock(return_value=False)

    with patch.object(main_module, 'db_manager', mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

    assert response.status_code == 503
    data = response.json() if response.content else {}

    # Assert against the actual keys returned by your health endpoint
    assert data.get("status") == "unhealthy"
    assert data.get("database") == "disconnected"


@pytest.mark.asyncio
async def test_chapa_webhook_callback_processing_success() -> None:
    """
    Verifies webhook processing. Note: Ensure webhook_handler
    uses the same DatabaseManager instance for consistency.
    """
    mock_handler = AsyncMock()
    mock_handler.handle_payment_event = AsyncMock(
        return_value={"status": "success", "message": "Ledger updated"}
    )

    webhook_payload = {
        "event": "charge.success",
        "data": {"id": 98765, "tx_ref": "TX-SMARTMATCH-99120", "status": "success"}
    }

    with patch.object(main_module, 'webhook_handler', mock_handler):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/webhooks/chapa",
                json=webhook_payload,
                headers={"X-Chapa-Signature": "valid_signature"}
            )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    mock_handler.handle_payment_event.assert_called_once()


@pytest.mark.asyncio
async def test_chapa_webhook_handler_offline_error() -> None:
    with patch.object(main_module, 'webhook_handler', None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/webhooks/chapa", json={}, headers={"X-Chapa-Signature": "test"})

    assert response.status_code == 503
    assert "Payment processing sub-engine offline" in response.json()["detail"]
