-- The "fires" view: urgent open tickets nobody has picked up.
SELECT id, tenant_id, subject, created_at
FROM tickets
WHERE assignee_id IS NULL AND priority = 1 AND status = 'open'
ORDER BY created_at
LIMIT 100;
