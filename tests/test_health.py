import pytest
from fastapi.testclient import TestClient
from apps.collector.src.main import app

@pytest.fixture
def client():
    return TestClient(app)

def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"

def test_metrics(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text or "mobility_events_collected_total" in response.text

def test_event_collection_success(client):
    payload = {
        "event_id": "evt-001",
        "region": "site-a",
        "vehicle_id": "v-1004",
        "speed_kmh": 85.5,
        "payload": {"battery_pct": 92}
    }
    response = client.post("/api/v1/events", json=payload)
    assert response.status_code == 201
    assert response.json()["status"] == "accepted"
    assert response.json()["event_id"] == "evt-001"

def test_event_collection_invalid_region(client):
    payload = {
        "event_id": "evt-002",
        "region": "unknown-zone",
        "vehicle_id": "v-1004",
    }
    response = client.post("/api/v1/events", json=payload)
    assert response.status_code == 400
