-- Search box: a tenant's tickets whose subject mentions "invoice".
SELECT id, subject, created_at
FROM tickets
WHERE tenant_id = %(mid_tenant)s AND subject ILIKE '%%invoice%%'
ORDER BY created_at DESC
LIMIT 50;
