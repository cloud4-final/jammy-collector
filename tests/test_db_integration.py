import os

import pytest
from psycopg_pool import ConnectionPool

from apps.collector.src.db import DatabaseUnavailable, PostgresEventRepository
from apps.collector.src.main import MobilityEvent
from apps.collector.src.migrate import run_migrations

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="TEST_DATABASE_URL not set; skipping database integration tests",
)


@pytest.fixture(scope="module")
def setup_database():
    # Run migration twice to verify idempotency
    run_migrations(TEST_DB_URL)
    run_migrations(TEST_DB_URL)

    write_pool = ConnectionPool(TEST_DB_URL, min_size=1, max_size=5, open=True)
    read_pool = ConnectionPool(TEST_DB_URL, min_size=1, max_size=5, open=True)

    with write_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE events")
        conn.commit()

    yield write_pool, read_pool

    write_pool.close()
    read_pool.close()


def test_migrate_idempotent_and_repository(setup_database):
    write_pool, read_pool = setup_database
    repo = PostgresEventRepository(write_pool=write_pool, read_pool=read_pool)

    repo.ping()

    event = MobilityEvent(
        event_id="integ-evt-001",
        region="site-a",
        vehicle_id="v-integ-1",
        speed_kmh=88.8,
        payload={"battery": 99},
    )

    # First insert -> True
    inserted = repo.insert(event=event, site="jammy-a")
    assert inserted is True

    # Duplicate insert -> False
    duplicate = repo.insert(event=event, site="jammy-a")
    assert duplicate is False

    # Get event -> returns dict with all 7 fields
    record = repo.get("integ-evt-001")
    assert record is not None
    assert record["event_id"] == "integ-evt-001"
    assert record["region"] == "site-a"
    assert record["vehicle_id"] == "v-integ-1"
    assert record["speed_kmh"] == 88.8
    assert record["payload"] == {"battery": 99}
    assert record["site"] == "jammy-a"
    assert "received_at" in record

    # List events
    items = repo.list(region="site-a", vehicle_id="v-integ-1", limit=10)
    assert len(items) >= 1
    assert items[0]["event_id"] == "integ-evt-001"


def test_read_url_separation(setup_database):
    write_pool, _ = setup_database

    # Create a broken read pool to confirm read operations use read_pool
    # and write operations use write_pool
    broken_read_pool = ConnectionPool(
        "postgresql://postgres:postgres@127.0.0.1:54329/nonexistent",
        min_size=1,
        max_size=2,
        open=False,
        timeout=0.5,
    )
    broken_read_pool.open(wait=False)

    repo = PostgresEventRepository(write_pool=write_pool, read_pool=broken_read_pool)

    # Write should succeed using write_pool
    event = MobilityEvent(
        event_id="integ-evt-write-only",
        region="site-b",
        vehicle_id="v-write-1",
        speed_kmh=42.0,
        payload={},
    )
    inserted = repo.insert(event=event, site="jammy-b")
    assert inserted is True

    # Read should fail because read_pool is broken
    with pytest.raises(DatabaseUnavailable):
        repo.get("integ-evt-write-only")

    with pytest.raises(DatabaseUnavailable):
        repo.list(limit=10)

    with pytest.raises(DatabaseUnavailable):
        repo.ping()

    broken_read_pool.close()
