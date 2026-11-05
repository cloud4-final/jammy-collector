import os
import sys
from pathlib import Path

import psycopg


def get_sql_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parent.parent / "sql",
        Path.cwd() / "apps" / "collector" / "sql",
        Path.cwd() / "sql",
        Path("/app/sql"),
    ]
    for p in candidates:
        if p.is_dir():
            return p
    raise FileNotFoundError("Could not find sql/ directory for migrations")


def run_migrations(database_url: str | None = None) -> None:
    db_url = database_url or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL must be set to run migrations")

    sql_dir = get_sql_dir()
    sql_files = sorted(sql_dir.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"No .sql migration files found in {sql_dir}")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            for sql_file in sql_files:
                sql_content = sql_file.read_text(encoding="utf-8")
                cur.execute(sql_content)
        conn.commit()


if __name__ == "__main__":
    try:
        run_migrations()
        print("Migrations completed successfully.")
    except (psycopg.Error, OSError, ValueError) as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        sys.exit(1)
