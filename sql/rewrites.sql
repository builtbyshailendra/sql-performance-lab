-- Step 3: the index that goes with the q04 rewrite. VIP tickets (2%) newest
-- first, with every column the query reads INCLUDEd: the filter and the
-- result come from the index alone (an index-only scan).
CREATE INDEX tickets_vip_newest ON tickets (created_at DESC) INCLUDE (id, subject, attrs, tags)
  WHERE attrs @> '{"vip": true}';

-- The two GIN indexes from step 2 are now unused by the workload:
-- pg_stat_user_indexes showed their only scans came from the step 2
-- experiments. An unused index is pure write cost.
DROP INDEX tickets_attrs;
DROP INDEX tickets_tags;
