from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.db import DatabaseUnavailable, EventRepository, get_repository
from src.main import MobilityEvent, app


class FakeEventRepository(EventRepository):
    def __init__(self) -> None:
        self.events: dict[str, dict] = {}
        self.is_available: bool = True

    def insert(self, event: MobilityEvent, site: str) -> bool:
        if not self.is_available:
            raise DatabaseUnavailable("database unavailable")
        if event.event_id in self.events:
            return False

        now_iso = datetime.now(UTC).isoformat()
        self.events[event.event_id] = {
            "event_id": event.event_id,
            "region": event.region,
            "vehicle_id": event.vehicle_id,
            "speed_kmh": event.speed_kmh,
            "payload": event.payload,
            "site": site,
            "received_at": now_iso,
        }
        return True

    def get(self, event_id: str) -> dict | None:
        if not self.is_available:
            raise DatabaseUnavailable("database unavailable")
        return self.events.get(event_id)

    def list(
        self,
        region: str | None = None,
        vehicle_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        if not self.is_available:
            raise DatabaseUnavailable("database unavailable")
        items = list(self.events.values())
        if region is not None:
            items = [item for item in items if item["region"] == region]
        if vehicle_id is not None:
            items = [item for item in items if item["vehicle_id"] == vehicle_id]

        items.sort(
            key=lambda e: (e["received_at"], e["event_id"]),
            reverse=True,
        )
        return items[:limit]

    def ping(self) -> None:
        if not self.is_available:
            raise DatabaseUnavailable("database unavailable")


@pytest.fixture
def fake_repo() -> FakeEventRepository:
    return FakeEventRepository()


@pytest.fixture
def client(fake_repo: FakeEventRepository):
    app.dependency_overrides[get_repository] = lambda: fake_repo
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def reset_telemetry():
    yield
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.uninstrument_app(app)
    app.middleware_stack = None
    trace._TRACER_PROVIDER = None
    if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        trace._TRACER_PROVIDER_SET_ONCE._done = False
