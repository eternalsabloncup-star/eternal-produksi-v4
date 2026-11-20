"""Health endpoint tests."""

from __future__ import annotations


def test_health_ok(app_client) -> None:
    response = app_client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mongodb"] is True
    assert "features" in body
    assert "loading_sj_task_enabled" in body["features"]
    assert body["features"]["loading_sj_task_enabled"] is False
