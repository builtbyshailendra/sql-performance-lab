-- The VIP condition stated on its own. PostgreSQL can't work out that
-- attrs @> '{"vip": true, "region": "eu"}' implies attrs @> '{"vip": true}',
-- so a partial index WHERE attrs @> '{"vip": true}' is only usable when the
-- query says exactly that.
SELECT id, subject, created_at
FROM tickets
WHERE attrs @> '{"vip": true}' AND attrs @> '{"region": "eu"}' AND tags @> ARRAY['billing']
ORDER BY created_at DESC
LIMIT 50;
