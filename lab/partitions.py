"""Monthly partitions for ticket_events, measured two ways.

    python -m lab.partitions

1. The analytics query (q08, UTC months) on the partitioned copy, where
   PostgreSQL can skip whole months that are outside the date range.
2. Retention: removing everything older than a year, as a DELETE on the
   plain table vs detaching and dropping old partitions. Both run inside a
   transaction that is rolled back, so the data stays for the next run; the
   work (and its write-ahead log) happens before the rollback either way.

The partitioned copy has no secondary indexes: neither test needs them.
"""

from __future__ import annotations

import json
import statistics
import time

import psycopg

from lab.analytics import PG_Q
from lab.run import DSN, ROOT

# Pruning needs a condition on the partition key itself. This is q08 with
# the cutoff applied to created_at directly (same rows as PG_Q).
PART_Q = """
SELECT date_trunc('month', created_at AT TIME ZONE 'UTC') AS month, kind, count(*) AS n
FROM {table}
WHERE created_at >= %(cutoff_tz)s
GROUP BY 1, 2 ORDER BY 1, 2
"""


def median_ms(conn: psycopg.Connection, sql: str, p: dict, runs: int = 5) -> tuple[float, list]:
    rows = conn.execute(sql, p).fetchall()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        conn.execute(sql, p).fetchall()
        times.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(times), 1), rows


def wal_bytes(conn: psycopg.Connection, before: str) -> int:
    return int(conn.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), %s)", (before,)).fetchone()[0])


def main() -> None:
    out: dict = {}
    with psycopg.connect(DSN, autocommit=True) as conn:
        lo, hi = conn.execute("SELECT date_trunc('month', min(created_at)), date_trunc('month', max(created_at)) "
                              "FROM ticket_events").fetchone()
        conn.execute("DROP TABLE IF EXISTS ticket_events_p")
        conn.execute("CREATE TABLE ticket_events_p (LIKE ticket_events) PARTITION BY RANGE (created_at)")
        months = conn.execute("SELECT g FROM generate_series(%s::timestamptz, %s::timestamptz, interval '1 month') g",
                              (lo, hi)).fetchall()
        for (m,) in months:
            name = f"ticket_events_p_{m:%Y_%m}"
            conn.execute(f"CREATE TABLE {name} PARTITION OF ticket_events_p "
                         f"FOR VALUES FROM ('{m.isoformat()}') TO ('{m.isoformat()}'::timestamptz + interval '1 month')")
        out["partitions"] = len(months)
        start = time.perf_counter()
        conn.execute("INSERT INTO ticket_events_p SELECT * FROM ticket_events")
        out["copy_s"] = round(time.perf_counter() - start, 1)
        conn.execute("ANALYZE ticket_events_p")
        print(f"{len(months)} monthly partitions, copied in {out['copy_s']} s", flush=True)

        cutoff = conn.execute("SELECT date_trunc('day', now() AT TIME ZONE 'UTC') - interval '365 days'").fetchone()[0]
        p = {"cutoff": cutoff, "cutoff_tz": conn.execute("SELECT %s::timestamp AT TIME ZONE 'UTC'", (cutoff,)).fetchone()[0]}
        plain_ms, plain_rows = median_ms(conn, PG_Q, p)
        part_ms, part_rows = median_ms(conn, PART_Q.format(table="ticket_events_p"), p)
        plan = conn.execute("EXPLAIN (FORMAT JSON) " + PART_Q.format(table="ticket_events_p"), p).fetchone()[0][0]
        scanned = json.dumps(plan).count('"Relation Name": "ticket_events_p_')
        out.update(plain_q08_ms=plain_ms, partitioned_q08_ms=part_ms, partitions_scanned=scanned,
                   same_rows=plain_rows == part_rows)
        print(f"q08: plain {plain_ms} ms, partitioned {part_ms} ms, scanning {scanned} of {len(months)} partitions, "
              f"same rows: {plain_rows == part_rows}", flush=True)

        # Retention: drop everything older than a year.
        old_months = [m for (m,) in months if m < p["cutoff_tz"].replace(day=1)]
        boundary = old_months[-1] if old_months else lo

    with psycopg.connect(DSN) as conn:  # transactions, rolled back
        conn.execute("SET maintenance_work_mem = '512MB'")
        lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
        start = time.perf_counter()
        deleted = conn.execute("DELETE FROM ticket_events WHERE created_at < %s::timestamptz + interval '1 month'",
                               (boundary,)).rowcount
        out["delete_s"] = round(time.perf_counter() - start, 1)
        out["delete_rows"] = deleted
        out["delete_wal_mb"] = round(wal_bytes(conn, lsn) / 2**20, 1)
        conn.rollback()
        print(f"DELETE of {deleted:,} rows older than a year: {out['delete_s']} s, {out['delete_wal_mb']} MB of WAL",
              flush=True)

        lsn = conn.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
        start = time.perf_counter()
        for m in old_months:
            name = f"ticket_events_p_{m:%Y_%m}"
            conn.execute(f"ALTER TABLE ticket_events_p DETACH PARTITION {name}")
            conn.execute(f"DROP TABLE {name}")
        out["drop_partitions_ms"] = round((time.perf_counter() - start) * 1000, 1)
        out["dropped_partitions"] = len(old_months)
        out["drop_wal_kb"] = round(wal_bytes(conn, lsn) / 1024, 1)
        conn.rollback()
        print(f"DETACH + DROP of {len(old_months)} partitions: {out['drop_partitions_ms']} ms, "
              f"{out['drop_wal_kb']} KB of WAL", flush=True)

    (ROOT / "results" / "partitions.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
