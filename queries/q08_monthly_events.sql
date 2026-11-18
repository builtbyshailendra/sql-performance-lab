-- Analytics: events per month and kind over the last year.
SELECT date_trunc('month', created_at) AS month, kind, count(*)
FROM ticket_events
WHERE created_at >= now() - interval '365 days'
GROUP BY 1, 2
ORDER BY 1, 2;
