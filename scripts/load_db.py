#!/usr/bin/env python3
"""Create and seed the two sample databases (ticketsdb for agent c, store for agent e).

    python scripts/load_db.py                    # postgres/postgres @ 127.0.0.1:5432
    python scripts/load_db.py --bootstrap-role   # Homebrew Postgres: first create the `postgres` role

Idempotent: a database that already has its tables is left alone. Uses psycopg2,
so no `psql` client is needed. Handles the `\\connect` lines in the .sql files.
"""

import argparse
from contextlib import closing
import getpass
import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
DATASETS = [
    ("ticketsdb", "tickets", ROOT / "c_mcp_bug_assistant/db/schema.sql", ROOT / "c_mcp_bug_assistant/db/seed.sql"),
    ("store", "delivery_methods", ROOT / "e_ecommerce_agent/db/schema.sql", ROOT / "e_ecommerce_agent/db/seed.sql"),
]
HOST = os.getenv("DB_HOST", "127.0.0.1")
PORT = int(os.getenv("DB_PORT", "5432"))
USER = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASS", "postgres")


def connect(dbname: str, user: str = USER, password: str | None = PASSWORD):
    # NB: never `with conn:` — in psycopg2 2.9 that opens a transaction even in autocommit,
    # and CREATE / DROP DATABASE refuse to run inside one.
    conn = psycopg2.connect(host=HOST, port=PORT, dbname=dbname, user=user, password=password, connect_timeout=5)
    conn.autocommit = True
    return conn


def run_sql_file(path: Path, default_db: str) -> None:
    """Execute a .sql file, switching databases at each `\\connect <db>` line."""
    db, chunk = default_db, []

    def flush():
        sql = "\n".join(chunk).strip()
        if sql:
            with closing(connect(db)) as conn, conn.cursor() as cur:
                cur.execute(sql)
        chunk.clear()

    for line in path.read_text().splitlines():
        if line.strip().startswith("\\connect"):
            flush()
            db = line.split()[1]
        else:
            chunk.append(line)
    flush()


def has_table(db: str, table: str) -> bool:
    try:
        with closing(connect(db)) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            return bool(cur.fetchone()[0])
    except psycopg2.OperationalError:
        return False  # database does not exist yet


def bootstrap_role() -> None:
    """Homebrew Postgres's superuser is your macOS user; create postgres/postgres for the configs."""
    me = getpass.getuser()
    with closing(connect("postgres", user=me, password=None)) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'postgres'")
        if cur.fetchone():
            cur.execute("ALTER ROLE postgres WITH LOGIN SUPERUSER PASSWORD 'postgres'")
        else:
            cur.execute("CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD 'postgres'")
    print(f"role 'postgres' ready (created via your user '{me}')")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-role", action="store_true")
    parser.add_argument("--check", action="store_true", help="only test the connection")
    args = parser.parse_args()
    try:
        if args.bootstrap_role:
            bootstrap_role()
        connect("postgres").close()
        if args.check:
            return 0
        for db, marker, schema, seed in DATASETS:
            if has_table(db, marker):
                print(f"{db}: already loaded")
                continue
            with closing(connect("postgres")) as conn, conn.cursor() as cur:  # half-loaded leftover → start clean
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
                if cur.fetchone():
                    cur.execute(f'DROP DATABASE "{db}"')
            run_sql_file(schema, "postgres")
            run_sql_file(seed, db)
            print(f"{db}: created and seeded")
    except psycopg2.Error as exc:
        print(f"database error: {str(exc).strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
