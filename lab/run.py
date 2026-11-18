"""Measure the workload.

    python -m lab.run baseline            # every query in queries/
    python -m lab.run indexes q01 q03     # just some, labelled with a stage name

For each query: one warm-up run, then RUNS timed runs (wall clock around
execute + fetch, on a warm cache), and one EXPLAIN (ANALYZE, BUFFERS) whose
plan is saved. Results go to results/<stage>.json; plans to
results/plans/<stage>/<query>.json. Numbers are from this laptop, so compare
stages with each other, not with anyone else's machine.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LAB_DSN", "postgresql://postgres@127.0.0.1:5433/sql_lab")
RUNS = int(os.environ.get("LAB_RUNS", "7"))


def params(conn: psycopg.Connection) -> dict[str, int]:
    """Pick realistic parameters from the data itself, the same way every time."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    p = {
        "big_tenant": one("SELECT tenant_id FROM tickets GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"),
        "mid_tenant": one("SELECT tenant_id FROM (SELECT tenant_id, count(*) c FROM tickets GROUP BY 1) x "
                          "ORDER BY abs(c - 2000), tenant_id LIMIT 1"),
        "agent": one("SELECT assignee_id FROM tickets WHERE assignee_id IS NOT NULL "
                     "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"),
        "ticket": one("SELECT ticket_id FROM ticket_events GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"),
    }
    # Page 41 (offset 2000) on the full data; on a small test copy, a page that
    # still exists, so the query returns rows there too.
    open_count = conn.execute("SELECT count(*) FROM tickets WHERE tenant_id = %s AND status IN ('open', 'pending')",
                              (p["big_tenant"],)).fetchone()[0]
    p["page_offset"] = min(2000, max(0, open_count - 100))
    # The keyset version of q01 starts after the last row of the previous page.
    p.update(keyset_cursor(conn, p["big_tenant"], p["page_offset"]))
    return p


def keyset_cursor(conn: psycopg.Connection, tenant: int, offset: int) -> dict:
    """The (priority, created_at, id) of the row just before `offset`."""
    if offset == 0:
        return {"after_priority": 0, "after_created_at": "-infinity", "after_id": 0}
    pr, ca, i = conn.execute(
        "SELECT priority, created_at, id FROM tickets WHERE tenant_id = %s AND status IN ('open', 'pending') "
        "ORDER BY priority, created_at, id OFFSET %s LIMIT 1", (tenant, offset - 1)).fetchone()
    return {"after_priority": pr, "after_created_at": ca, "after_id": i}


def plan_summary(plan: dict) -> dict:
    """The parts of a plan worth reading: node types and relations, buffers."""
    nodes: list[str] = []

    def walk(n: dict) -> None:
        label = n["Node Type"]
        if "Relation Name" in n:
            label += f" on {n['Relation Name']}"
        if "Index Name" in n:
            label += f" using {n['Index Name']}"
        nodes.append(label)
        for child in n.get("Plans", []):
            walk(child)

    top = plan["Plan"]
    walk(top)
    return {
        "execution_ms": round(plan["Execution Time"], 2),
        "planning_ms": round(plan["Planning Time"], 2),
        "nodes": nodes,
        "shared_hit": top.get("Shared Hit Blocks", 0),
        "shared_read": top.get("Shared Read Blocks", 0),
        "temp_written": top.get("Temp Written Blocks", 0),
        "rows": top.get("Actual Rows"),
    }


def measure(conn: psycopg.Connection, sql: str, p: dict) -> dict:
    conn.execute(sql, p).fetchall()  # warm-up
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        rows = conn.execute(sql, p).fetchall()
        times.append((time.perf_counter() - start) * 1000)
    plan = conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, p).fetchone()[0][0]
    return {"median_ms": round(statistics.median(times), 2), "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2), "runs": RUNS, "rows": len(rows), "plan": plan_summary(plan),
            "_full_plan": plan}


def main(argv: list[str]) -> None:
    stage = argv[0] if argv else "baseline"
    only = set(argv[1:])
    folder = ROOT / os.environ.get("LAB_QUERIES", "queries")
    files = sorted(f for f in folder.glob("q*.sql") if not only or f.stem.split("_")[0] in only)
    out_file = ROOT / "results" / f"{stage}.json"
    results = json.loads(out_file.read_text()) if out_file.exists() else {}
    plan_dir = ROOT / "results" / "plans" / stage
    plan_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET statement_timeout = '120s'")
        p = params(conn)
        results["_params"] = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in p.items()}
        for f in files:
            sql = f.read_text(encoding="utf-8")
            r = measure(conn, sql, p)
            (plan_dir / f"{f.stem}.json").write_text(json.dumps(r.pop("_full_plan"), indent=1))
            results[f.stem] = r
            print(f"{f.stem:24s} median {r['median_ms']:>10.2f} ms   {', '.join(r['plan']['nodes'][:4])}", flush=True)
    out_file.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
