-- An agent's queue: a big tenant's open and pending tickets, most urgent
-- first, page 41 at 50 per page on the full data (OFFSET pagination, as most apps start).
SELECT id, subject, priority, status, created_at
FROM tickets
WHERE tenant_id = %(big_tenant)s AND status IN ('open', 'pending')
ORDER BY priority, created_at
LIMIT 50 OFFSET %(page_offset)s;
