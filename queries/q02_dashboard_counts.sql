-- The tenant dashboard: tickets created in the last 30 days, by status.
SELECT status, count(*)
FROM tickets
WHERE tenant_id = %(big_tenant)s AND created_at >= now() - interval '30 days'
GROUP BY status
ORDER BY status;
