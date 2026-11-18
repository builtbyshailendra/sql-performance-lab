"""Two production incidents, reproduced and measured.

    python -m lab.ops

1. Adding an index to a busy table. A writer inserts one ticket every 50 ms
   while an index is built, first with plain CREATE INDEX (which blocks
   writes until it finishes), then with CREATE INDEX CONCURRENTLY (which
   doesn't). Measured: the longest an insert had to wait.
2. A long-running transaction stops VACUUM. One connection holds an old
   snapshot open while another updates 500,000 rows; VACUUM can't remove
   the old row versions until the snapshot is gone. Measured with pgstattuple.

Results go to results/ops.json. The test rows and table are removed after.
"""

from __future__ import annotations

import json
import threading
import time

import psycopg

from lab.run import DSN, ROOT

INSERT = """
INSERT INTO tickets (id, tenant_id, requester_id, status, priority, subject, tags, attrs, created_at, updated_at)
VALUES (%s, 1, 1, 'open', 3, 'ops test', ARRAY['test'], '{}', now(), now())
"""


def writer(stop: threading.Event, waits: list[float], first_id: int) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        i = first_id
        while not stop.is_set():
            start = time.perf_counter()
            conn.execute(INSERT, (i,))
            waits.append((time.perf_counter() - start) * 1000)
            i += 1
            time.sleep(0.05)


def build_under_load(ddl: str, first_id: int) -> dict:
    waits: list[float] = []
    stop = threading.Event()
    t = threading.Thread(target=writer, args=(stop, waits, first_id))
    t.start()
    time.sleep(1.0)  # steady state first
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET maintenance_work_mem = '512MB'")
        start = time.perf_counter()
        conn.execute(ddl)
        build_s = round(time.perf_counter() - start, 1)
        conn.execute("DROP INDEX IF EXISTS ops_test_idx")
    time.sleep(1.0)
    stop.set()
    t.join()
    return {"build_s": build_s, "inserts": len(waits), "max_insert_wait_ms": round(max(waits), 1),
            "median_insert_ms": round(sorted(waits)[len(waits) // 2], 2)}


def dead_tuples(conn: psycopg.Connection) -> dict:
    r = conn.execute("SELECT table_len, tuple_count, dead_tuple_count, free_space FROM pgstattuple('bloat_demo')").fetchone()
    return {"table_mb": round(r[0] / 2**20, 1), "live": r[1], "dead": r[2], "free_mb": round(r[3] / 2**20, 1)}


def main() -> None:
    out: dict = {}
    print("plain CREATE INDEX under a write load...", flush=True)
    out["create_index"] = build_under_load("CREATE INDEX ops_test_idx ON tickets (updated_at, priority)", 300_000_000)
    print(out["create_index"], flush=True)
    print("CREATE INDEX CONCURRENTLY under the same load...", flush=True)
    out["create_index_concurrently"] = build_under_load(
        "CREATE INDEX CONCURRENTLY ops_test_idx ON tickets (updated_at, priority)", 310_000_000)
    print(out["create_index_concurrently"], flush=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM tickets WHERE id >= 300000000")

    with psycopg.connect(DSN, autocommit=True) as b:
        b.execute("CREATE EXTENSION IF NOT EXISTS pgstattuple")
        b.execute("DROP TABLE IF EXISTS bloat_demo")
        b.execute("CREATE TABLE bloat_demo (id int PRIMARY KEY, val int) WITH (autovacuum_enabled = off)")
        b.execute("INSERT INTO bloat_demo SELECT g, 0 FROM generate_series(1, 500000) g")
        b.execute("VACUUM bloat_demo")
        steps = {"loaded": dead_tuples(b)}

        with psycopg.connect(DSN) as a:  # the long-running transaction
            a.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            a.execute("SELECT count(*) FROM bloat_demo").fetchone()  # takes its snapshot now
            b.execute("UPDATE bloat_demo SET val = val + 1")
            steps["updated_snapshot_held"] = dead_tuples(b)
            b.execute("VACUUM bloat_demo")
            steps["vacuumed_snapshot_held"] = dead_tuples(b)
            a.execute("COMMIT")
        b.execute("VACUUM bloat_demo")
        steps["vacuumed_after_commit"] = dead_tuples(b)
        b.execute("UPDATE bloat_demo SET val = val + 1")
        b.execute("VACUUM bloat_demo")
        steps["second_update_reuses_space"] = dead_tuples(b)
        b.execute("DROP TABLE bloat_demo")
        out["long_transaction_vs_vacuum"] = steps
        for k, v in steps.items():
            print(f"{k:28s} {v}", flush=True)
    (ROOT / "results" / "ops.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
