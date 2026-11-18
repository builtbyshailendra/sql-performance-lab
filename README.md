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
