-- "My tickets": everything assigned to one agent that isn't closed.
SELECT id, subject, status, updated_at
FROM tickets
WHERE assignee_id = %(agent)s AND status <> 'closed'
ORDER BY updated_at DESC
LIMIT 20;
