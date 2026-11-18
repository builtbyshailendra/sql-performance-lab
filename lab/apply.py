"""Apply a SQL file statement by statement, timing each one.

    python -m lab.apply sql/indexes.sql

Writes results/apply_<file>.json: each statement's time and, for CREATE
INDEX, the index's size. Then runs VACUUM ANALYZE, so the planner has fresh
statistics and index-only scans can use the visibility map.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import psycopg

from lab.run import DSN, ROOT


def statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def main(path: str) -> None:
    f = ROOT / path
    log = []
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET maintenance_work_mem = '512MB'")
        for stmt in statements(f.read_text(encoding="utf-8")):
            start = time.perf_counter()
            conn.execute(stmt)
            took = round(time.perf_counter() - start, 2)
            entry = {"sql": " ".join(stmt.split())[:120], "seconds": took}
            m = re.match(r"CREATE INDEX (\w+)", stmt)
            if m:
                entry["index"] = m.group(1)
                entry["bytes"] = conn.execute("SELECT pg_relation_size(%s::regclass)", (m.group(1),)).fetchone()[0]
            log.append(entry)
            size = f"  {entry['bytes'] / 2**20:7.1f} MB" if "bytes" in entry else ""
            print(f"{took:7.2f} s{size}  {entry['sql'][:80]}", flush=True)
        start = time.perf_counter()
        conn.execute("VACUUM ANALYZE")
        log.append({"sql": "VACUUM ANALYZE", "seconds": round(time.perf_counter() - start, 2)})
        print(f"{log[-1]['seconds']:7.2f} s  VACUUM ANALYZE")
    (ROOT / "results" / f"apply_{Path(path).stem}.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
