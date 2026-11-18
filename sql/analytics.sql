-- Step 4: what made q08 (monthly event counts over 20M rows) faster.

-- 1. Tell the planner how many distinct months there are. Without this it
--    guessed "many groups", sorted 13M rows and spilled to disk; with it, it
--    hashes into 52 groups in 24 KB per worker. Expression statistics need an
--    IMMUTABLE expression: date_trunc on a timestamptz depends on the session
--    time zone, so the month is computed in UTC.
CREATE STATISTICS ticket_events_month
  ON (date_trunc('month', created_at AT TIME ZONE 'UTC')) FROM ticket_events;
ANALYZE ticket_events;

-- 2. A daily rollup: ~3,000 rows instead of 20M. Built in 9 s here; in an
--    app it would be kept current by a job or by aggregating new events
--    incrementally. (lab/analytics.py builds and queries it.)
-- CREATE TABLE event_daily AS
--   SELECT (created_at AT TIME ZONE 'UTC')::date AS day, kind, count(*) AS n
--   FROM ticket_events GROUP BY 1, 2;

-- 3. Monthly partitions (lab/partitions.py): queries can skip old months,
--    and old months are removed by dropping a partition instead of DELETE.
