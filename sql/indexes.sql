-- Step 2: indexes, each for a query whose plan showed why it was slow.
-- Rule used for column order: equality columns first, then the range or sort
-- column, then anything that makes the scan unique or index-only.

-- q01 queue page. Plan: bitmap scan of tenant 1's ~850k tickets, then a
-- sort. A partial index holding only open/pending tickets, already in the
-- page's order (id breaks ties, so pages are stable), lets the query walk
-- the first 2,050 entries and stop.
CREATE INDEX tickets_queue ON tickets (tenant_id, priority, created_at, id)
  WHERE status IN ('open', 'pending');

-- q02 dashboard. Tenant + time range; status is INCLUDEd so the count can be
-- answered from the index alone (an index-only scan), never touching rows.
-- Also serves q06's outer "tenant's newest tickets" (scanned backwards) and
-- q10, so the old single-column tenant index can go.
CREATE INDEX tickets_tenant_created ON tickets (tenant_id, created_at) INCLUDE (status);
DROP INDEX tickets_tenant_id;

-- q03 my tickets. Only the 28% of tickets that aren't closed, per agent,
-- newest update first.
CREATE INDEX tickets_assignee_open ON tickets (assignee_id, updated_at DESC)
  WHERE status <> 'closed';

-- q04 VIP filter. Containment (@>) on jsonb and on an array needs GIN.
-- jsonb_path_ops is smaller and faster than the default, and only supports @>,
-- which is all this query uses.
CREATE INDEX tickets_attrs ON tickets USING gin (attrs jsonb_path_ops);
CREATE INDEX tickets_tags ON tickets USING gin (tags);

-- q06 latest event per ticket. With (ticket_id, created_at) the "latest one"
-- is the last index entry: no sort, no spill. It also serves q05 (one
-- ticket's history, now without a sort), so the old index is redundant.
CREATE INDEX ticket_events_ticket_created ON ticket_events (ticket_id, created_at);
DROP INDEX ticket_events_ticket_id;

-- q07 SLA report. Last 7 days across all tenants, with the two other columns
-- it reads INCLUDEd. BRIN would be tiny but useless here: tickets are stored
-- in id order and created_at is random relative to it.
CREATE INDEX tickets_created ON tickets (created_at) INCLUDE (tenant_id, first_response_at);

-- q09 "fires". About 0.5% of tickets match; a partial index holds only them.
CREATE INDEX tickets_fires ON tickets (created_at)
  WHERE assignee_id IS NULL AND priority = 1 AND status = 'open';
