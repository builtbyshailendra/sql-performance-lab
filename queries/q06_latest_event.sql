-- The list view: a tenant's 50 newest tickets with each one's latest event.
SELECT t.id, t.subject, e.kind, e.created_at AS last_event_at
FROM tickets t
JOIN LATERAL (
  SELECT kind, created_at FROM ticket_events e
  WHERE e.ticket_id = t.id ORDER BY created_at DESC LIMIT 1
) e ON true
WHERE t.tenant_id = %(big_tenant)s
ORDER BY t.created_at DESC
LIMIT 50;
