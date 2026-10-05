from fastapi.testclient import TestClient

from trialsentinel.api.main import create_app


def test_liveness_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "trialsentinel-api"
