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
    assert (
        "http_requests_total" in response.text
        or "mobility_events_collected_total" in response.text
    )


def test_event_collection_success(client):
    payload = {
        "event_id": "evt-001",
        "region": "site-a",
        "vehicle_id": "v-1004",
        "speed_kmh": 85.5,
        "payload": {"battery_pct": 92},
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


def test_health_endpoints_not_traced():
    from fastapi.testclient import TestClient
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from apps.collector.src.db import get_repository
    from apps.collector.src.main import app
    from apps.collector.src.telemetry import setup_tracing
    from apps.collector.tests.conftest import FakeEventRepository

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    setup_tracing(app, tracer_provider=provider)

    repo = FakeEventRepository()
    app.dependency_overrides[get_repository] = lambda: repo

    try:
        with TestClient(app) as test_client:
            r_healthz = test_client.get("/healthz")
            assert r_healthz.status_code == 200
            r_ready = test_client.get("/ready")
            assert r_ready.status_code == 200
            r_metrics = test_client.get("/metrics")
            assert r_metrics.status_code == 200

            spans_after_health = exporter.get_finished_spans()
            assert len(spans_after_health) == 0, (
                f"Expected 0 spans for health endpoints, got: {[s.name for s in spans_after_health]}"
            )

            r_event = test_client.get("/api/v1/events/x")
            assert r_event.status_code in (200, 404)
            server_spans = [
                s
                for s in exporter.get_finished_spans()
                if s.kind == trace.SpanKind.SERVER
            ]
            assert len(server_spans) >= 1, (
                f"Expected >= 1 server span for /api/v1/events/x, got: {exporter.get_finished_spans()}"
            )
    finally:
        app.dependency_overrides.clear()
