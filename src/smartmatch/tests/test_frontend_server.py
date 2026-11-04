import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, mock_open

# Import the actual global FastAPI application instance
from src.smartmatch.main import app


@pytest.mark.asyncio
async def test_frontend_renders_successfully() -> None:
    """
    Verifies that the FastAPI frontend route successfully reads index.html
    and renders a 200 OK HTML response when requested by clients.
    """
    mock_html_content = "<html><body><h1>Smart Match Africa Landing Page</h1></body></html>"

    # Enforce safe localized filesystem intercept parameters
    with patch("builtins.open", mock_open(read_data=mock_html_content)), \
            patch("os.path.exists", return_value=True):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/")

    assert response.status_code == 200
    assert "Smart Match Africa Landing Page" in response.text


@pytest.mark.asyncio
async def test_frontend_handles_missing_file_gracefully() -> None:
    """
    Ensures that if index.html is missing during deployment,
    the FastAPI router falls back to a clean 404 error page instead of crashing.
    """
    # Force os.path.exists to return False for candidate paths
    with patch("os.path.exists", return_value=False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/")

    assert response.status_code == 404
    assert "Deploy Error 404" in response.text
