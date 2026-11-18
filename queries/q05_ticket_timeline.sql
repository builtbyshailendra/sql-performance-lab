-- One ticket's history. Already indexed: the control query that should stay fast.
SELECT kind, actor_id, created_at, body
FROM ticket_events
WHERE ticket_id = %(ticket)s
ORDER BY created_at;
