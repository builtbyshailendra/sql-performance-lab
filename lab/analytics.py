"""q08 (events per month and kind, last year, over 20M rows) four ways.

    python -m lab.analytics

1. PostgreSQL as configured (work_mem 4 MB: the aggregation spills to disk)
2. PostgreSQL with work_mem 256 MB for this query
3. A daily rollup table (built once; queries read ~1,500 rows instead of ~15M)
4. DuckDB over a Parquet copy of the two columns the query needs

All four use the same cutoff (midnight UTC, 365 days back) and UTC months,
and must return identical results. Timings: median of 5 after a warm-up.
The Parquet file lives on drive D, outside the repo.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

import duckdb
import psycopg

from lab.run import DSN, ROOT

WORK = Path(os.environ.get("LAB_WORK", "D:/cache/sql-lab-work"))

PG_Q = """
SELECT date_trunc('month', created_at AT TIME ZONE 'UTC') AS month, kind, count(*) AS n
FROM ticket_events
WHERE created_at AT TIME ZONE 'UTC' >= %(cutoff)s
GROUP BY 1, 2 ORDER BY 1, 2
"""
ROLLUP_BUILD = """
CREATE TABLE event_daily AS
SELECT (created_at AT TIME ZONE 'UTC')::date AS day, kind, count(*) AS n
FROM ticket_events GROUP BY 1, 2
"""
ROLLUP_Q = """
SELECT date_trunc('month', day)::timestamp AS month, kind, sum(n)::bigint AS n
FROM event_daily WHERE day >= %(cutoff)s::date
GROUP BY 1, 2 ORDER BY 1, 2
"""
DUCK_Q = """
SELECT date_trunc('month', created_at) AS month, kind, count(*) AS n
FROM read_parquet(?) WHERE created_at >= ?
GROUP BY 1, 2 ORDER BY 1, 2
"""


def timed(fn, runs: int = 5) -> tuple[float, list]:
    result = fn()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(times), 1), result


def norm(rows: list) -> list:
    return [(m.replace(tzinfo=None) if isinstance(m, datetime) else m, k, int(n)) for m, k, n in rows]


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    out: dict = {}
    with psycopg.connect(DSN, autocommit=True) as conn:
        cutoff = conn.execute("SELECT date_trunc('day', now() AT TIME ZONE 'UTC') - interval '365 days'").fetchone()[0]
        p = {"cutoff": cutoff}
        out["cutoff_utc"] = cutoff.isoformat()

        ms, base = timed(lambda: conn.execute(PG_Q, p).fetchall())
        out["postgres_work_mem_4MB_ms"] = ms
        print("postgres, work_mem 4MB  ", ms, "ms", flush=True)

        conn.execute("SET work_mem = '256MB'")
        ms, big = timed(lambda: conn.execute(PG_Q, p).fetchall())
        conn.execute("RESET work_mem")
        out["postgres_work_mem_256MB_ms"] = ms
        print("postgres, work_mem 256MB", ms, "ms", flush=True)

        conn.execute("DROP TABLE IF EXISTS event_daily")
        start = time.perf_counter()
        conn.execute(ROLLUP_BUILD)
        out["rollup_build_s"] = round(time.perf_counter() - start, 1)
        out["rollup_rows"] = conn.execute("SELECT count(*) FROM event_daily").fetchone()[0]
        ms, rolled = timed(lambda: conn.execute(ROLLUP_Q, p).fetchall())
        out["rollup_query_ms"] = ms
        print("rollup table            ", ms, "ms  (built in", out["rollup_build_s"], "s,", out["rollup_rows"], "rows)", flush=True)

        csv_path, parquet = WORK / "events.csv", WORK / "events.parquet"
        start = time.perf_counter()
        with open(csv_path, "wb") as f, conn.cursor().copy(
                "COPY (SELECT created_at AT TIME ZONE 'UTC' AS created_at, kind FROM ticket_events) TO STDOUT (FORMAT csv)") as cp:
            for chunk in cp:
                f.write(chunk)
        out["export_csv_s"] = round(time.perf_counter() - start, 1)

    duck = duckdb.connect()
    start = time.perf_counter()
    duck.execute(f"COPY (SELECT * FROM read_csv('{csv_path.as_posix()}', header=false, "
                 "columns={'created_at': 'TIMESTAMP', 'kind': 'VARCHAR'})) "
                 f"TO '{parquet.as_posix()}' (FORMAT parquet)")
    out["csv_to_parquet_s"] = round(time.perf_counter() - start, 1)
    out["csv_mb"] = round(csv_path.stat().st_size / 2**20, 1)
    out["parquet_mb"] = round(parquet.stat().st_size / 2**20, 1)
    csv_path.unlink()
    ms, ducked = timed(lambda: duck.execute(DUCK_Q, [parquet.as_posix(), cutoff]).fetchall())
    out["duckdb_ms"] = ms
    print("duckdb over parquet     ", ms, "ms  (parquet", out["parquet_mb"], "MB from a", out["csv_mb"], "MB csv)", flush=True)

    out["identical_results"] = norm(base) == norm(big) == norm(rolled) == norm(ducked)
    out["groups"] = len(base)
    print("identical results:", out["identical_results"], "groups:", out["groups"])
    (ROOT / "results" / "analytics.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
