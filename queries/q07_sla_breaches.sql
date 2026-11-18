-- SLA report: last 7 days, tickets whose first response took over 24 h
-- (or hasn't happened), by plan.
SELECT te.plan, count(*)
FROM tickets t
JOIN tenants te ON te.id = t.tenant_id
WHERE t.created_at >= now() - interval '7 days'
  AND (t.first_response_at IS NULL OR t.first_response_at - t.created_at > interval '24 hours')
GROUP BY te.plan
ORDER BY te.plan;
