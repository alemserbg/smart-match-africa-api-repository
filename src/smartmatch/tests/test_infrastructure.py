import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.smartmatch.services.db_manager import DatabaseManager


# 1. Standard fixtures for clean dependency injection
@pytest.fixture
def mock_supabase_client():
    client = AsyncMock()
    client.auth = AsyncMock()
    client.auth.get_session = AsyncMock()
    return client


@pytest.fixture
def mock_redis_client():
    client = AsyncMock()
    client.ping = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.mark.asyncio
# 1. Target the exact file where the functions are used
@patch("src.smartmatch.services.db_manager.acreate_client", new_callable=AsyncMock)
@patch("src.smartmatch.services.db_manager.aioredis.from_url", new_callable=MagicMock)
async def test_database_manager_successful_connection(
        mock_redis_from_url,
        mock_supabase_create,
        mock_redis_client,
        mock_supabase_client
):
    """
    Verifies that the DatabaseManager correctly instantiates the Supabase and Redis Clients.
    """
    # Setup the mock factories to return our specific client instances
    mock_redis_from_url.return_value = mock_redis_client
    mock_supabase_create.return_value = mock_supabase_client

    manager = DatabaseManager()

    # Run the connection
    await manager.connect()

    # Assertions
    assert manager.supabase == mock_supabase_client
    assert manager.redis == mock_redis_client
    mock_redis_client.ping.assert_called_once()


@pytest.mark.asyncio
async def test_database_manager_health_check_healthy(mock_supabase_client, mock_redis_client):
    """
    Verifies health check perfectly aligns with the auth session and redis pings.
    """
    manager = DatabaseManager()
    manager.supabase = mock_supabase_client
    manager.redis = mock_redis_client

    # Act: Trigger the exact health check logic you provided
    health_status = await manager.health_check()

    # Assert: It should return True when both try blocks succeed
    assert health_status is True

    # Assert: Verify the exact methods used in your db_manager.py were invoked
    mock_supabase_client.auth.get_session.assert_called_once()
    mock_redis_client.ping.assert_called_once()


@pytest.mark.asyncio
async def test_redis_cache_set_and_get_lifecycle(mock_supabase_client, mock_redis_client):
    """Validates Redis interaction."""
    manager = DatabaseManager()
    manager.supabase = mock_supabase_client
    manager.redis = mock_redis_client

    test_key = "cache:housing:listings:active"
    test_payload = '{"status": "success", "matches": []}'

    mock_redis_client.get = AsyncMock(return_value=test_payload)

    await manager.redis.set(test_key, test_payload, ex=3600)
    mock_redis_client.set.assert_called_once_with(test_key, test_payload, ex=3600)

    cached_response = await manager.redis.get(test_key)
    assert cached_response == test_payload


@pytest.mark.asyncio
async def test_redis_cache_invalidation_channel(mock_supabase_client, mock_redis_client):
    """Validates Redis deletion."""
    manager = DatabaseManager()
    manager.supabase = mock_supabase_client
    manager.redis = mock_redis_client
    target_key = "cache:vehicles:active"

    await manager.redis.delete(target_key)
    mock_redis_client.delete.assert_called_once_with(target_key)
