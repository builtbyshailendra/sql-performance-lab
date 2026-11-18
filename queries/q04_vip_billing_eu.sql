-- Support lead's filter: VIP billing tickets from the EU, newest first.
SELECT id, subject, created_at
FROM tickets
WHERE attrs @> '{"vip": true, "region": "eu"}' AND tags @> ARRAY['billing']
ORDER BY created_at DESC
LIMIT 50;
