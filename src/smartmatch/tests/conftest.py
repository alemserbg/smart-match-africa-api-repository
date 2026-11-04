import os
import sys
import pytest
import asyncio
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

# Force test environment variables before importing application components
os.environ["DATABASE_URL"] = "postgresql://test_user:test_pass@localhost:5432/test_db"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["PORT"] = "8080"
os.environ["DEBUG_MODE"] = "true"
os.environ["CHAPA_WEBHOOK_SECRET"] = "test_secret_hash_12345"

# Ensure the project root is on the path for clean resolution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))


# ==========================================
# 1. ASYNC EVENT LOOP LIFECYCLE CONFIG
# ==========================================
@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """
    Creates an isolated, session-scoped cryptographic event loop instance
    to drive multi-threaded asynchronous sub-routine tests.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ==========================================
# 2. INFRASTRUCTURE MOCK FIXTURES
# ==========================================
@pytest.fixture(scope="function")
def mock_pg_pool() -> MagicMock:
    """
    Simulates an active asyncpg database connection pool.
    Intercepts physical socket allocations with non-blocking mock responses.
    """
    pool = MagicMock()

    # Configure the connection mock capabilities
    connection_mock = AsyncMock()
    connection_mock.execute = AsyncMock(return_value="SELECT 1")
    connection_mock.fetch = AsyncMock(return_value=[])
    connection_mock.fetchrow = AsyncMock(return_value=None)
    # Added fetchval to support the updated health_check logic
    connection_mock.fetchval = AsyncMock(return_value=1)

    # Correctly configure asynchronous context manager behaviors (async with pool.acquire())
    acquire_cm_mock = MagicMock()
    acquire_cm_mock.__aenter__ = AsyncMock(return_value=connection_mock)
    acquire_cm_mock.__aexit__ = AsyncMock(return_value=None)

    pool.acquire.return_value = acquire_cm_mock
    pool.close = AsyncMock()

    return pool


@pytest.fixture(scope="function")
def mock_redis_client() -> AsyncMock:
    """
    Mocks an aioredis client pool to eliminate real-time external caching dependencies.
    """
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


# ==========================================
# 3. EXTERNAL SITES & SERVICES MOCKS
# ==========================================
@pytest.fixture(scope="function", autouse=True)
def mock_external_dependencies():
    """
    Enforces clean isolation boundaries by proactively mocking third-party
    network systems across all operational test units automatically.
    """
    # Target the fully qualified paths for the Smart Match Africa namespace
    with patch("src.smartmatch.services.ai_engine.SmartMatchAIEngine", autospec=True) as mock_ai, \
            patch("src.smartmatch.utils.image_service.ImageService", autospec=True) as mock_s3, \
            patch("telegram.ext.ApplicationBuilder", autospec=True) as mock_tg:
        # Set up a stable mock application object for python-telegram-bot
        mock_app = MagicMock()
        mock_app.initialize = AsyncMock()
        mock_app.start = AsyncMock()
        mock_app.stop = AsyncMock()
        mock_app.shutdown = AsyncMock()
        mock_app.updater = MagicMock()
        mock_app.updater.start_polling = AsyncMock()
        mock_app.updater.stop = AsyncMock()

        mock_tg.return_value.token.return_value.build.return_value = mock_app

        yield {
            "ai_engine": mock_ai,
            "storage_service": mock_s3,
            "telegram_app": mock_app
        }
