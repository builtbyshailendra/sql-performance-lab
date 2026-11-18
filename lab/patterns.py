"""Two application patterns, measured.

    python -m lab.patterns

1. Pagination depth: OFFSET vs keyset at page 1, 41, 401 and 2,001 of the
   big tenant's queue (both using the same index).
2. N+1: a list of 50 tickets, then one query per ticket for its requester's
   name (51 round trips), vs one query with a JOIN.

Results go to results/patterns.json.
"""

from __future__ import annotations

import json
import statistics
import time

import psycopg

from lab.run import DSN, ROOT, keyset_cursor, params

OFFSET_Q = (ROOT / "queries" / "q01_queue_page.sql").read_text(encoding="utf-8")
KEYSET_Q = (ROOT / "queries_fixed" / "q01_queue_page.sql").read_text(encoding="utf-8")


def timed(conn: psycopg.Connection, fn, runs: int = 7) -> float:
    fn(conn)  # warm-up
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        fn(conn)
        times.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(times), 2)


def main() -> None:
    out: dict = {"pagination": [], "n_plus_1": {}}
    with psycopg.connect(DSN, autocommit=True) as conn:
        p = params(conn)
        queue = conn.execute("SELECT count(*) FROM tickets WHERE tenant_id = %s AND status IN ('open', 'pending')",
                             (p["big_tenant"],)).fetchone()[0]
        out["queue_length"] = queue
        for offset in (0, 2_000, 20_000, (queue - 50) // 50 * 50):  # the last offset is the last full page
            q = {**p, "page_offset": offset, **keyset_cursor(conn, p["big_tenant"], offset)}
            off_ms = timed(conn, lambda c: c.execute(OFFSET_Q, q).fetchall())
            key_ms = timed(conn, lambda c: c.execute(KEYSET_Q, q).fetchall())
            same = conn.execute(OFFSET_Q, q).fetchall() == conn.execute(KEYSET_Q, q).fetchall()
            row = {"page": offset // 50 + 1, "offset": offset, "offset_ms": off_ms, "keyset_ms": key_ms, "same_rows": same}
            out["pagination"].append(row)
            print(row, flush=True)

        tenant = p["big_tenant"]
        list_q = "SELECT id, requester_id, subject FROM tickets WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 50"

        def n_plus_1(c: psycopg.Connection) -> list:
            rows = c.execute(list_q, (tenant,)).fetchall()
            return [(i, s, c.execute("SELECT name FROM users WHERE id = %s", (r,)).fetchone()[0]) for i, r, s in rows]

        def joined(c: psycopg.Connection) -> list:
            return c.execute("SELECT t.id, t.subject, u.name FROM tickets t JOIN users u ON u.id = t.requester_id "
                             "WHERE t.tenant_id = %s ORDER BY t.created_at DESC LIMIT 50", (tenant,)).fetchall()

        out["n_plus_1"] = {"queries": 51, "n_plus_1_ms": timed(conn, n_plus_1), "join_ms": timed(conn, joined),
                           "same_rows": n_plus_1(conn) == joined(conn),
                           "note": "localhost: each round trip is about 0.1 ms; across a network add the round-trip time x 51"}
        print(out["n_plus_1"])
    (ROOT / "results" / "patterns.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
