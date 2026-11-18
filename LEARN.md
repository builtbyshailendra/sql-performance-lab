# Learning notes: sql-performance-lab

## 1. How to read a query plan

`EXPLAIN (ANALYZE, BUFFERS) <query>` runs the query and shows the plan PostgreSQL chose, as a tree. Read it **inside out**: the most indented nodes run first and feed their parents.

For each node, look at four things:

| Look at | What it tells you | Red flag |
|---|---|---|
| Node type | how rows are fetched or combined | `Seq Scan` on a big table for a query returning a few rows |
| `rows=` estimated vs `actual rows=` | whether the planner understood the data | off by 10× or more: bad statistics or correlated columns |
| `actual time=` | where the time goes (the second number is the node's total, per loop) | one node taking almost all of it |
| `Buffers: shared hit / read` | pages from cache vs from disk (8 KB each) | large `read` counts; `temp written` (a sort or hash spilled to disk) |

Common nodes:
- **Seq Scan**: read the whole table. Fine for small tables or when most rows are needed.
- **Index Scan**: walk an index, then fetch each matching row from the table.
- **Index Only Scan**: the index has every column needed; no table visit (if the visibility map says pages are all-visible).
- **Bitmap Index Scan → Bitmap Heap Scan**: collect matching row locations first, then read table pages in order. Good for "medium" selectivity, and for combining indexes (`BitmapAnd`/`BitmapOr`).
- **Nested Loop**: for each outer row, look up inner rows. Great when the outer side is small and the inner side is indexed.
- **Hash Join**: build a hash table on one side, probe with the other. Good for big unsorted inputs.
- **Merge Join**: both sides sorted, walk them together.
- **Sort / Incremental Sort / Top-N heapsort**: ordering; `LIMIT` with an index in the right order avoids it entirely.
- **HashAggregate / GroupAggregate**: `GROUP BY`.

## 2. Which index for which query

| Query shape | Index | Why |
|---|---|---|
| `WHERE a = ? AND b = ? ORDER BY c` | `(a, b, c)` | equality columns first, then the sort column: rows come out already ordered |
| `WHERE a = ? AND b > ?` | `(a, b)` | equality before range; a range column stops the index being useful for later columns |
| `WHERE status = 'open'` on 9% of rows that's always queried | partial index `... WHERE status = 'open'` | smaller, cheaper to maintain, only covers what's asked |
| `SELECT x, y WHERE a = ?` | `(a) INCLUDE (x, y)` | covering: index-only scan, no table visits |
| `jsonb @> '{...}'`, `text[] @> ARRAY[...]` | GIN | indexes the keys/elements inside the value |
| `ILIKE '%word%'` | GIN with `pg_trgm` | trigram index: substring search can't use a B-tree |
| huge append-only table filtered by time | BRIN on the time column | tiny index storing min/max per block range; works because time correlates with physical order |

Every index has a price: disk space and slower writes (each insert updates every index). The lab measures both.

## 3. Things that bite in production

- **`OFFSET` pagination** reads and throws away every skipped row: page 400 is 400 pages of work. **Keyset pagination** (`WHERE (priority, created_at) > (?, ?) ... LIMIT 50`) stays fast at any depth.
- **N+1 queries**: one query for a list, then one per row. Each is fast; a hundred round trips aren't.
- **`OR` across different columns** often defeats indexes; `UNION ALL` of two indexed queries can win.
- **Functions on indexed columns** (`WHERE date(created_at) = ...`) hide the column from a plain index; rewrite as a range or index the expression.
- **Locks during migrations**: `CREATE INDEX` blocks writes for the whole build; `CREATE INDEX CONCURRENTLY` doesn't (but takes longer and can't run in a transaction). Adding a column with a volatile default rewrites the table.
- **MVCC and bloat**: an `UPDATE` writes a new row version; the old one stays until `VACUUM` removes it. Long-running transactions stop that cleanup.
- **Statistics**: the planner's estimates come from `ANALYZE`. Correlated columns (tenant and region, say) fool it; extended statistics (`CREATE STATISTICS`) can help.

## 4. Explain it without notes

(Filled in with this project's real numbers once the measurements are done.)

1. Read the baseline plan for the queue query and say why it's slow.
2. Why did the composite index's column order matter?
3. When does PostgreSQL choose a bitmap scan over an index scan?
4. Why is keyset pagination faster than `OFFSET`, and what does it cost the UI?
5. What does `Buffers: shared read=...` tell you that timing alone doesn't?
6. How would you add an index to a 5-million-row table in production without blocking writes?
7. What is MVCC, and why does a long-running transaction cause bloat?
8. When would you move a query to DuckDB (or another column store) instead of indexing it?
