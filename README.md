# sql-performance-lab

A realistic PostgreSQL workload (a multi-tenant help desk: 5,000,000 tickets, 20,000,004 ticket events, 3.8 GB) with the slow queries such an app really has. Each one is diagnosed from its plan, fixed, and measured before and after; every rewrite is tested to return exactly the same rows; and the costs of the fixes are measured too.

PostgreSQL 16 (default settings) · Python 3.12 + psycopg 3 · DuckDB 2.0. Personal learning project, built pairing with Claude Code. All data is synthetic and generated deterministically. Numbers are from an Intel i5-10310U laptop: compare them with each other, not with other machines.

## Results

| Query | Before | After | What fixed it |
|---|---|---|---|
| q01 agent's queue, page 41 | 606 ms | **0.56 ms** | partial index in the page's order + keyset pagination |
| q02 tenant dashboard | 690 ms | 18 ms | composite index with `INCLUDE`: index-only scan |
| q03 "my tickets" | 668 ms | 1.6 ms | partial index on not-closed tickets |
| q04 VIP / region / tag filter | 1,158 ms | **0.98 ms** | query states the selective condition + partial covering index (GIN was ignored by the planner) |
| q06 newest tickets with latest event | 824 ms | 0.69 ms | `(ticket_id, created_at)` index: no sort, no spill |
| q07 SLA report | 890 ms | 72 ms | covering index on `created_at` |
| q08 monthly analytics (20M rows) | 8,535 ms | 3,444 ms / 446 ms / **1.9 ms** | expression statistics + partitions / DuckDB on Parquet / a daily rollup |
| q09 unassigned urgent | 605 ms | 2.1 ms | 0.1 MB partial index |
| q05, q10 | 0.7 ms, 3.2 ms | untouched | already fine: not everything needs an index |

What the fixes cost, and what else was measured:
- Inserting 20,000 tickets: 665 ms with 2 indexes → 2,771 ms with 8 → 2,317 ms after dropping two unused ones.
- Pagination: `OFFSET` takes 376 ms on the last page of a 55,042-ticket queue; keyset takes 0.44 ms on every page.
- N+1: 51 queries take 6.3 ms on localhost against 1.4 ms for one `JOIN`.
- Deleting a year of events: `DELETE` 62 s and 2.2 GB of WAL; dropping 13 partitions 5.1 s and 0.8 MB.
- Adding an index under write load: one insert blocked 10,969 ms (plain); 662 ms worst case (`CONCURRENTLY`).
- A held snapshot stopped `VACUUM` removing 500,000 dead rows until it ended.

## Try it

```bash
# PostgreSQL with pg_stat_statements loaded; DSN in LAB_DSN (default 127.0.0.1:5433/sql_lab)
psql -f sql/schema.sql
psql -v tenants=2000 -v users=100000 -v tickets=5000000 -v events_per_ticket=4 -f sql/generate.sql   # ~11 min
python -m lab.run baseline           # measure all ten queries
python -m lab.apply sql/indexes.sql  # then: python -m lab.run indexes
python -m lab.patterns               # pagination depth, N+1
python -m lab.analytics              # q08 four ways
python -m lab.partitions             # partitions and retention
python -m lab.ops                    # the two incidents
python -m pytest                     # correctness on a 20,000-ticket copy
```

## Build log

### Step 1: the schema, the data, the workload and the harness

- `sql/schema.sql`: tenants, users, tickets (with `text[]` tags and `jsonb` attributes), ticket events. Deliberately what many young apps have: primary keys, foreign keys, and two single-column indexes someone added once (`tickets(tenant_id)`, `ticket_events(ticket_id)`).
- `sql/generate.sql`: deterministic data with the skew that changes query plans: most tickets on a few big tenants (`power(random(), 3)`), 72% closed / 14% solved / 9% open / 5% pending, two years of history denser towards now.
- `queries/`: ten queries the app runs: an agent's queue page, a dashboard count, "my tickets", a VIP/region/tag filter, a ticket's history (the control: already fast), each ticket's latest event, an SLA report, monthly event analytics, unassigned urgent tickets, a subject search.
- `lab/run.py`: for each query, one warm-up and 7 timed runs on a warm cache, plus one `EXPLAIN (ANALYZE, BUFFERS)` saved as JSON. Parameters (the biggest tenant, a mid-size tenant, the busiest agent and ticket) are picked from the data itself.
- `tests/`: every query must return rows on a 20,000-ticket copy; every rewrite must return exactly the same rows as the original.

What broke:
1. **Every ticket would have had the same tenant.** The first generator drew random values in `CROSS JOIN LATERAL (SELECT random() ...)` subqueries that don't reference the outer row, and PostgreSQL may compute those once for the whole query. Rewritten so the draws are in a subquery's select list, which PostgreSQL evaluates per row and won't flatten (volatile functions). The small test copy confirmed the skew: the biggest tenant holds 17% of tickets, the top ten 37%.
2. `ARRAY[ARRAY['billing'], ARRAY['auth','sso'], ...]` failed: PostgreSQL arrays of arrays must have equal lengths. A `CASE` picks the tag list instead.
3. The queue query's `OFFSET 2000` returned nothing on the small copy, so its test proved nothing. The offset is now a parameter: 2000 on the full data, a page that exists on the small copy.

### Step 2: baseline, then indexes

Baseline on the full data (5,000,000 tickets, 20,000,004 events; median of 7 warm runs on an i5-10310U laptop, PostgreSQL 16 with default settings: `shared_buffers` 128 MB, `work_mem` 4 MB):

| Query | Baseline | What the plan showed | Index added | After | |
|---|---|---|---|---|---|
| q01 queue page | 606 ms | bitmap scan of the big tenant's ~850k tickets, then a sort | partial `(tenant_id, priority, created_at, id) WHERE status IN ('open','pending')`, 27 MB | **1.6 ms** | 388× |
| q02 dashboard counts | 690 ms | same tenant scan | `(tenant_id, created_at) INCLUDE (status)`, 193 MB | **18 ms** | index-only scan |
| q03 my tickets | 668 ms | full table scan | partial `(assignee_id, updated_at DESC) WHERE status <> 'closed'`, 42 MB | **1.6 ms** | 417× |
| q04 VIP billing EU | 1,158 ms | full scan, JSON checked row by row | GIN on `attrs` (`jsonb_path_ops`, 22 MB) and on `tags` (9 MB) | 341 ms | the GIN indexes weren't used (below) |
| q05 ticket history (control) | 0.72 ms | already indexed | (served by the new events index) | 0.32 ms | the sort disappeared |
| q06 latest event per ticket | 824 ms | big scan, a sort spilled 2,756 pages to disk | `ticket_events (ticket_id, created_at)`, 601 MB | **0.69 ms** | 1,200× |
| q07 SLA report | 890 ms | full scan | `(created_at) INCLUDE (tenant_id, first_response_at)`, 193 MB | 72 ms | 12× |
| q08 monthly analytics | 8,535 ms | scans most of 20M rows; 84,489 pages spilled to disk | none: an index can't help a query that reads most of the table | 10,245 ms | step 4 |
| q09 unassigned urgent | 605 ms | full scan | partial `(created_at) WHERE assignee_id IS NULL AND priority = 1 AND status = 'open'`, 0.1 MB | **2.1 ms** | 288× |
| q10 subject search | 3.2 ms | fine already (mid-size tenant) | none | 1.2 ms | uses the new tenant index |

Two old indexes were dropped as redundant (`tickets(tenant_id)` is the prefix of the new tenant index; `ticket_events(ticket_id)` of the new events index). All eight builds took 77 s; `VACUUM ANALYZE` after them makes index-only scans possible.

**What the indexes cost.** Inserting 20,000 tickets (then rolling back), median of 5 runs: **665 ms before, 2,771 ms after**, 4.2× slower, with the ticket indexes growing from 145 MB to 602 MB. The first run after the builds took 29 s (index pages not yet in memory). Every index is updated on every insert; this is the price of the read wins, and the reason to drop redundant ones.

**Why q04 ignored its GIN indexes.** The planner's estimate was good (6,281 matches predicted, 5,243 real). It chose to walk the newest-first `created_at` index and stop after 50 matches, expecting about 40,000 rows; it read 45,974, each a random row fetch: 341 ms. Forcing the GIN path (a `MATERIALIZED` CTE) gave 181 ms with identical rows, but the real cost is the same in both: thousands of matching rows scattered across the table. Step 3 fixes it with a partial covering index on the selective condition (VIP is 2% of tickets).

The `shared read` counts in the plans are pages not in PostgreSQL's 128 MB buffer pool; most came from the operating system's file cache, which is why 138,000 "reads" took 600 ms rather than minutes.

### Step 3: query rewrites and two application patterns

Every rewrite in `queries_fixed/` is tested to return exactly the original's rows (`tests/test_queries.py`, on the small copy), and the pattern experiments check the same on the full data.

| Query | Before (baseline) | With step 2 index only | Rewrite | After |
|---|---|---|---|---|
| q01 queue page | 606 ms | 1.6 ms | **keyset pagination** with `id` as a final tie-breaker | **0.56 ms** |
| q04 VIP billing EU | 1,158 ms | 341 ms | the VIP condition stated on its own, plus a partial covering index `(created_at DESC) INCLUDE (id, subject, attrs, tags) WHERE attrs @> '{"vip": true}'` (19 MB) | **0.98 ms** (index-only scan) |

Why q04 needed the rewrite: PostgreSQL can't prove that `attrs @> '{"vip": true, "region": "eu"}'` implies `attrs @> '{"vip": true}'`, so a partial index on the second is only used when the query says exactly that. With the index present, the original wording still took 342 ms.

**Pagination depth** (`lab/patterns.py`; the big tenant's queue is 55,042 tickets; both versions use the same index; rows identical at every depth):

| Page (50 per page) | `OFFSET` | keyset |
|---|---|---|
| 1 | 0.44 ms | 0.46 ms |
| 41 | 1.75 ms | 0.55 ms |
| 401 | 144 ms | 0.52 ms |
| 1,100 (last) | **376 ms** | **0.44 ms** |

`OFFSET` reads and throws away every row before the page, so it gets slower the deeper you go; keyset seeks straight to "after the last row you saw". The tie-breaker matters for correctness too: with `ORDER BY priority, created_at` alone, two tickets with the same values can land on either side of a page boundary, so `OFFSET` paging can skip or repeat them. The cost: no "jump to page 700", only next/previous.

**N+1**: 50 tickets then one query per ticket for the requester's name (51 queries) took **6.3 ms** on localhost; one `JOIN` took **1.4 ms**. Over a network each extra round trip adds its latency, so with 1 ms between app and database the 51-query version would be roughly 57 ms.

**Unused indexes.** `pg_stat_user_indexes` showed the two GIN indexes from step 2 were scanned only by my own experiments, never by the workload, so they were dropped (`sql/rewrites.sql`). Inserting 20,000 tickets: 2,771 ms with them, 2,317 ms without (median of 5; the runs ranged 1,642–2,787 ms, so treat it as "somewhat better"). The same view shows a cost no query plan does: 5.3 million `tenants_pkey` and 10 million `users_pkey` lookups, the foreign-key checks made while the data was inserted.

### Step 4: the analytics query indexes can't fix

q08 counts 20 million events by month and kind for the last year. It reads most of the table, so no index helps. Four approaches, all returning the identical 52 rows (same UTC cutoff, UTC months; `lab/analytics.py`, `lab/partitions.py`):

| Approach | q08 time | Notes |
|---|---|---|
| As configured (`work_mem` 4 MB) | 7,819 ms | 2 parallel workers each sort ~4.5M rows, spilling 84,580 pages to disk |
| `work_mem` raised to 256 MB | **12,885 ms (slower)** | the planner dropped parallelism: one process sorted 13.4M rows and *still* spilled (337 MB > 256 MB) |
| **Expression statistics** on the month | **4,365 ms** | the real problem was sorting at all: the planner couldn't estimate distinct months, so it assumed many groups. Told there are ~25, it hashes into 52 groups using 24 KB per worker, no spill |
| + monthly partitions | 3,444 ms | 13 of 25 partitions scanned (pruning). Without statistics on the partitioned table it was 6,381 ms: same lesson again |
| Daily rollup table | **1.9 ms** | 2,972 rows instead of 20M; built in 9 s; must be kept current, which is the real cost |
| DuckDB over Parquet | 446 ms | a column store reads only the two columns needed; the export took a 659 MB CSV to a 158 MB Parquet file |

Two details worth explaining in an interview: more memory made it slower (the planner's cost model changed the plan shape, not just the sort), and statistics can only be defined on an *immutable* expression, so the month had to be computed in UTC, which is also what a multi-region report should do.

**Retention: where partitioning really pays.** Removing everything older than a year (6,826,094 events), each inside a transaction that was rolled back:

| | Time | Write-ahead log |
|---|---|---|
| `DELETE ... WHERE created_at < ...` on the plain table | 62.0 s | 2,211 MB |
| `DETACH PARTITION` + `DROP TABLE` for 13 old months | 5.1 s | 0.8 MB |

The `DELETE` also leaves 6.8 million dead rows for `VACUUM` to clean up, and replicas must replay all 2.2 GB of log. Dropping a partition just removes files. (`DETACH ... CONCURRENTLY` avoids blocking readers while it happens.)

### Step 5: two production incidents, reproduced

`lab/ops.py` (test rows and tables are removed afterwards).

**Adding an index to a busy table.** A writer inserts one ticket every 50 ms while an index on `(updated_at, priority)` is built on the 5-million-row table:

| | Plain `CREATE INDEX` | `CREATE INDEX CONCURRENTLY` |
|---|---|---|
| Longest any insert waited | **10,969 ms**: blocked for the whole build | 662 ms |
| Inserts completed | 21 in ~13 s | 76 |
| Build time | 11.1 s | 8.7 s |

The plain build takes a lock that blocks writes until it's done: on a real table that's an outage for every write path. `CONCURRENTLY` builds in phases without that lock (the 662 ms was its waits at phase boundaries). It's normally *slower* than a plain build; it was faster here only because the first build had just pulled the table into memory. It also can't run inside a transaction, and a failed one leaves an invalid index to drop.

**A long-running transaction stops cleanup.** 500,000 rows, one connection holding a `REPEATABLE READ` snapshot open while another updates every row (measured with `pgstattuple`):

| | Table size | Dead row versions |
|---|---|---|
| loaded | 17.3 MB | 0 |
| every row updated, old snapshot still open | 34.6 MB | 500,000 |
| `VACUUM` while the snapshot is still open | 34.6 MB | **500,000: nothing removed** |
| `VACUUM` after that transaction ended | 34.6 MB (17.3 MB now free) | 0 |
| every row updated again, then `VACUUM` | **34.6 MB: the freed space was reused** | 0 |

PostgreSQL's MVCC keeps old row versions while any transaction might still need them, so one forgotten open transaction (an idle session in a pool, a stuck job) lets every busy table grow. `VACUUM` makes the space reusable but doesn't shrink the file (that needs `VACUUM FULL` or `pg_repack`, which rewrite the table). What to watch: `pg_stat_activity` for old `xact_start`, and `idle_in_transaction_session_timeout` to end them.

### Step 6: docs

This README's summary, `LEARN.md` (how to read a plan, which index for which query, production pitfalls, questions with this project's numbers), and an interview brief. Where it stands: 6 local commits, 12 correctness tests passing, every number above saved in `results/`. Not published.
