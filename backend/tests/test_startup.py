"""Backend startup smoke test."""

from __future__ import annotations


def test_backend_startup_creates_app(app_client) -> None:
    assert app_client.app.title == "Eternal Production V4"
    response = app_client.get("/api/health")
    assert response.status_code == 200
