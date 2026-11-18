"""What indexes cost: time to insert 20,000 tickets (then roll back), and the
total size of the table's indexes.

    python -m lab.writes before-indexes
    python -m lab.writes after-indexes

Every index on a table is updated on every insert, so more indexes means
slower writes. This puts a number on it.
"""

from __future__ import annotations

import json
import statistics
import sys
import time

import psycopg

from lab.run import DSN, ROOT

ROWS = 20_000

INSERT = """
INSERT INTO tickets (id, tenant_id, requester_id, assignee_id, status, priority, subject, tags, attrs,
                     created_at, updated_at, first_response_at, solved_at)
SELECT id + 100000000, tenant_id, requester_id, assignee_id, status, priority, subject, tags, attrs,
       created_at, updated_at, first_response_at, solved_at
FROM tickets WHERE id <= %s
"""


def main(label: str) -> None:
    times = []
    with psycopg.connect(DSN) as conn:
        for _ in range(5):
            start = time.perf_counter()
            conn.execute(INSERT, (ROWS,))
            times.append((time.perf_counter() - start) * 1000)
            conn.rollback()  # leave the data exactly as it was
        size = conn.execute("SELECT pg_indexes_size('tickets')").fetchone()[0]
        count = conn.execute("SELECT count(*) FROM pg_indexes WHERE tablename = 'tickets'").fetchone()[0]
    out = ROOT / "results" / "writes.json"
    data = json.loads(out.read_text()) if out.exists() else {}
    data[label] = {"insert_20k_median_ms": round(statistics.median(times), 1), "runs_ms": [round(t, 1) for t in times],
                   "ticket_indexes": count, "ticket_index_bytes": size}
    out.write_text(json.dumps(data, indent=1))
    print(label, data[label])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "before-indexes")
