"""Correctness, on a small copy of the data (20,000 tickets).

A faster query that returns different rows is a bug, not an optimisation.
Every rewrite in queries_fixed/ must return exactly what the original in
queries/ returns, for the same parameters.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from lab.run import params

ROOT = Path(__file__).resolve().parents[1]
PG = os.environ.get("TEST_PG", "postgresql://postgres@127.0.0.1:5433")
PSQL = os.environ.get("PSQL", "D:/DB/bin/psql.exe")
DB = "sql_lab_test"


@pytest.fixture(scope="session")
def conn():
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {DB}")
    base = [PSQL, "-h", "127.0.0.1", "-p", "5433", "-U", "postgres", "-d", DB, "-q", "-v", "ON_ERROR_STOP=1"]
    subprocess.run(base + ["-f", str(ROOT / "sql" / "schema.sql")], check=True, capture_output=True)
    subprocess.run(base + ["-v", "tenants=50", "-v", "users=1000", "-v", "tickets=20000", "-v", "events_per_ticket=4",
                           "-f", str(ROOT / "sql" / "generate.sql")], check=True, capture_output=True)
    with psycopg.connect(f"{PG}/{DB}", autocommit=True) as c:
        yield c
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")


QUERIES = sorted((ROOT / "queries").glob("q*.sql"))


@pytest.mark.parametrize("path", QUERIES, ids=[p.stem for p in QUERIES])
def test_every_query_runs_and_returns_rows(conn, path):
    rows = conn.execute(path.read_text(encoding="utf-8"), params(conn)).fetchall()
    assert rows, f"{path.stem} returned nothing on the small data set; the test would prove nothing"


FIXED = sorted((ROOT / "queries_fixed").glob("q*.sql")) if (ROOT / "queries_fixed").exists() else []


@pytest.mark.parametrize("fixed", FIXED, ids=[p.stem for p in FIXED])
def test_rewrite_returns_the_same_rows(conn, fixed):
    original = ROOT / "queries" / fixed.name
    p = params(conn)
    before = conn.execute(original.read_text(encoding="utf-8"), p).fetchall()
    after = conn.execute(fixed.read_text(encoding="utf-8"), p).fetchall()
    assert after == before
