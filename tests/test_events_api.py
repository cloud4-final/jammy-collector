from fastapi import FastAPI

from apps.collector.src.telemetry import setup_tracing


def test_post_new_event_201(client):
    payload = {
        "event_id": "evt-101",
        "region": "site-a",
        "vehicle_id": "v-101",
        "speed_kmh": 60.5,
        "payload": {"soc": 80},
    }
    response = client.post("/api/v1/events", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "accepted"
    assert data["event_id"] == "evt-101"
    assert data["region"] == "site-a"


def test_post_duplicate_200_duplicate(client):
    payload = {
        "event_id": "evt-dup-1",
        "region": "site-b",
        "vehicle_id": "v-202",
        "speed_kmh": 45.0,
        "payload": {"soc": 75},
    }
    # First submission -> 201 accepted
    res1 = client.post("/api/v1/events", json=payload)
    assert res1.status_code == 201
    assert res1.json()["status"] == "accepted"

    # Second submission with same event_id -> 200 duplicate
    res2 = client.post("/api/v1/events", json=payload)
    assert res2.status_code == 200
    assert res2.json()["status"] == "duplicate"
    assert res2.json()["event_id"] == "evt-dup-1"
    assert res2.json()["region"] == "site-b"


def test_get_event_200_fields(client):
    payload = {
        "event_id": "evt-field-test",
        "region": "site-c",
        "vehicle_id": "v-303",
        "speed_kmh": 110.0,
        "payload": {"temperature": 25.5},
    }
    post_res = client.post("/api/v1/events", json=payload)
    assert post_res.status_code == 201

    get_res = client.get("/api/v1/events/evt-field-test")
    assert get_res.status_code == 200
    data = get_res.json()
    expected_fields = {
        "event_id",
        "region",
        "vehicle_id",
        "speed_kmh",
        "payload",
        "site",
        "received_at",
    }
    assert expected_fields.issubset(set(data.keys()))
    assert data["event_id"] == "evt-field-test"
    assert data["region"] == "site-c"
    assert data["vehicle_id"] == "v-303"
    assert data["speed_kmh"] == 110.0
    assert data["payload"] == {"temperature": 25.5}
    assert isinstance(data["received_at"], str)


def test_get_missing_404(client):
    res = client.get("/api/v1/events/missing-id-999")
    assert res.status_code == 404


def test_list_filters_and_order(client):
    events = [
        {
            "event_id": f"evt-list-{i}",
            "region": "site-a" if i % 2 == 0 else "site-b",
            "vehicle_id": f"v-{i}",
            "speed_kmh": 50.0 + i,
        }
        for i in range(4)
    ]
    for ev in events:
        res = client.post("/api/v1/events", json=ev)
        assert res.status_code == 201

    # Filter by region
    res_region = client.get("/api/v1/events?region=site-a")
    assert res_region.status_code == 200
    data_region = res_region.json()
    assert all(item["region"] == "site-a" for item in data_region["items"])
    assert data_region["count"] == len(data_region["items"])

    # Filter by vehicle_id
    res_vehicle = client.get("/api/v1/events?vehicle_id=v-1")
    assert res_vehicle.status_code == 200
    data_vehicle = res_vehicle.json()
    assert len(data_vehicle["items"]) == 1
    assert data_vehicle["items"][0]["vehicle_id"] == "v-1"

    # Verify descending ordering: received_at DESC, event_id DESC
    res_all = client.get("/api/v1/events?limit=50")
    items = res_all.json()["items"]
    assert len(items) >= 4
    for i in range(len(items) - 1):
        prev = (items[i]["received_at"], items[i]["event_id"])
        nxt = (items[i + 1]["received_at"], items[i + 1]["event_id"])
        assert prev >= nxt


def test_list_limit_bounds_422(client):
    # limit < 1 -> 422
    res_zero = client.get("/api/v1/events?limit=0")
    assert res_zero.status_code == 422

    # limit > 500 -> 422
    res_high = client.get("/api/v1/events?limit=501")
    assert res_high.status_code == 422


def test_db_unavailable_returns_503(client, fake_repo):
    fake_repo.is_available = False

    # POST returns 503
    post_res = client.post(
        "/api/v1/events",
        json={"event_id": "evt-db-down", "region": "site-a", "vehicle_id": "v-1"},
    )
    assert post_res.status_code == 503
    assert post_res.json()["detail"] == "database unavailable"

    # GET by ID returns 503
    get_res = client.get("/api/v1/events/evt-db-down")
    assert get_res.status_code == 503
    assert get_res.json()["detail"] == "database unavailable"

    # GET list returns 503
    list_res = client.get("/api/v1/events")
    assert list_res.status_code == 503
    assert list_res.json()["detail"] == "database unavailable"


def test_ready_503_when_db_down(client, fake_repo):
    fake_repo.is_available = False
    res = client.get("/ready")
    assert res.status_code == 503
    assert res.json() == {"status": "not_ready", "reason": "database"}


def test_ready_200_when_db_ok(client, fake_repo):
    fake_repo.is_available = True
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"


def test_metrics_endpoint_label_uses_route_template(client):
    client.post(
        "/api/v1/events",
        json={"event_id": "evt-a", "region": "site-a", "vehicle_id": "v-a"},
    )
    client.post(
        "/api/v1/events",
        json={"event_id": "evt-b", "region": "site-b", "vehicle_id": "v-b"},
    )
    client.get("/api/v1/events/evt-a")
    client.get("/api/v1/events/evt-b")

    metrics_res = client.get("/metrics")
    assert metrics_res.status_code == 200
    text = metrics_res.text

    assert 'endpoint="/api/v1/events/{event_id}"' in text
    assert 'endpoint="/api/v1/events/evt-a"' not in text
    assert 'endpoint="/api/v1/events/evt-b"' not in text


def test_app_starts_without_otel_env(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    test_app = FastAPI()
    setup_tracing(test_app)
