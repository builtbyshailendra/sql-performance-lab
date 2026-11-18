# sql-performance-lab

A realistic PostgreSQL workload (a multi-tenant help desk: 5 million tickets, about 20 million ticket events) with the slow queries such an app really has, each one diagnosed from its plan and fixed with a measured before and after.

Status: built step by step, locally. Not published. All data is synthetic and generated deterministically (`setseed(0.42)`).

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
