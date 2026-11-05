from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Protocol

from prometheus_client import Histogram
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from apps.collector.src.main import MobilityEvent

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
)


class DatabaseUnavailable(Exception):
    """Raised when the database is unreachable or query fails."""


class EventRepository(Protocol):
    def insert(self, event: MobilityEvent, site: str) -> bool: ...

    def get(self, event_id: str) -> dict | None: ...

    def list(
        self,
        region: str | None = None,
        vehicle_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]: ...

    def ping(self) -> None: ...


class PostgresEventRepository:
    def __init__(
        self,
        write_pool: ConnectionPool | None,
        read_pool: ConnectionPool | None,
    ) -> None:
        self.write_pool = write_pool
        self.read_pool = read_pool

    def insert(self, event: Any, site: str) -> bool:
        if self.write_pool is None:
            raise DatabaseUnavailable("Database write pool is not configured")

        query = """
            INSERT INTO events (event_id, region, vehicle_id, speed_kmh, payload, site)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
        """
        try:
            with (
                DB_QUERY_DURATION.labels(operation="insert").time(),
                self.write_pool.connection() as conn,
                conn.cursor() as cur,
            ):
                cur.execute(
                    query,
                    (
                        event.event_id,
                        event.region,
                        event.vehicle_id,
                        event.speed_kmh,
                        Jsonb(event.payload),
                        site,
                    ),
                )
                inserted = cur.rowcount == 1
                conn.commit()
            return inserted
        except Exception as exc:
            raise DatabaseUnavailable(f"Database error during insert: {exc}") from exc

    def get(self, event_id: str) -> dict | None:
        if self.read_pool is None:
            raise DatabaseUnavailable("Database read pool is not configured")

        query = """
            SELECT event_id, region, vehicle_id, speed_kmh, payload, site, received_at
            FROM events
            WHERE event_id = %s
        """
        try:
            with (
                DB_QUERY_DURATION.labels(operation="get").time(),
                self.read_pool.connection() as conn,
                conn.cursor() as cur,
            ):
                cur.execute(query, (event_id,))
                row = cur.fetchone()
            if not row:
                return None
            received_at = row[6]
            if hasattr(received_at, "isoformat"):
                received_at = received_at.isoformat()
            return {
                "event_id": row[0],
                "region": row[1],
                "vehicle_id": row[2],
                "speed_kmh": row[3],
                "payload": row[4],
                "site": row[5],
                "received_at": received_at,
            }
        except Exception as exc:
            raise DatabaseUnavailable(f"Database error during get: {exc}") from exc

    def list(
        self,
        region: str | None = None,
        vehicle_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        if self.read_pool is None:
            raise DatabaseUnavailable("Database read pool is not configured")

        clauses: list[str] = []
        params: list[Any] = []
        if region is not None:
            clauses.append("region = %s")
            params.append(region)
        if vehicle_id is not None:
            clauses.append("vehicle_id = %s")
            params.append(vehicle_id)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT event_id, region, vehicle_id, speed_kmh, payload, site, received_at
            FROM events
            {where_sql}
            ORDER BY received_at DESC, event_id DESC
            LIMIT %s
        """
        params.append(limit)

        try:
            with (
                DB_QUERY_DURATION.labels(operation="list").time(),
                self.read_pool.connection() as conn,
                conn.cursor() as cur,
            ):
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
            items: list[dict] = []
            for row in rows:
                received_at = row[6]
                if hasattr(received_at, "isoformat"):
                    received_at = received_at.isoformat()
                items.append(
                    {
                        "event_id": row[0],
                        "region": row[1],
                        "vehicle_id": row[2],
                        "speed_kmh": row[3],
                        "payload": row[4],
                        "site": row[5],
                        "received_at": received_at,
                    }
                )
            return items
        except Exception as exc:
            raise DatabaseUnavailable(f"Database error during list: {exc}") from exc

    def ping(self) -> None:
        if self.read_pool is None:
            raise DatabaseUnavailable("Database read pool is not configured")

        try:
            with (
                DB_QUERY_DURATION.labels(operation="ping").time(),
                self.read_pool.connection() as conn,
                conn.cursor() as cur,
            ):
                cur.execute("SELECT 1")
        except Exception as exc:
            raise DatabaseUnavailable(f"Database error during ping: {exc}") from exc


_write_pool: ConnectionPool | None = None
_read_pool: ConnectionPool | None = None


def init_db_pools() -> None:
    global _write_pool, _read_pool
    write_url = os.getenv("DATABASE_URL")
    if not write_url:
        return

    read_url = os.getenv("DATABASE_READ_URL", write_url)
    min_size = int(os.getenv("DB_POOL_MIN", "1"))
    max_size = int(os.getenv("DB_POOL_MAX", "10"))

    _write_pool = ConnectionPool(
        write_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        timeout=5.0,
    )
    _read_pool = ConnectionPool(
        read_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        timeout=5.0,
    )

    _write_pool.open(wait=False)
    _read_pool.open(wait=False)


def close_db_pools() -> None:
    global _write_pool, _read_pool
    if _write_pool is not None:
        _write_pool.close()
        _write_pool = None
    if _read_pool is not None:
        _read_pool.close()
        _read_pool = None


def get_repository() -> EventRepository:
    if _write_pool is None and os.getenv("DATABASE_URL"):
        init_db_pools()
    return PostgresEventRepository(write_pool=_write_pool, read_pool=_read_pool)
