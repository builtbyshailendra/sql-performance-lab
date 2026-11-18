-- Keyset pagination: "the 50 rows after the last one you saw", using the
-- index order directly. Cost doesn't grow with the page number, and id as a
-- final tie-breaker means two tickets with the same priority and time can
-- never be skipped or shown twice between pages (OFFSET with a non-unique
-- ORDER BY can do both).
SELECT id, subject, priority, status, created_at
FROM tickets
WHERE tenant_id = %(big_tenant)s AND status IN ('open', 'pending')
  AND (priority, created_at, id) > (%(after_priority)s, %(after_created_at)s, %(after_id)s)
ORDER BY priority, created_at, id
LIMIT 50;
