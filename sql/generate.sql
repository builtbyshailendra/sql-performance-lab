-- Deterministic synthetic data with realistic skew. Sizes come from psql
-- variables so tests can use a small copy:
--   psql -v tenants=2000 -v users=100000 -v tickets=5000000 -v events_per_ticket=4 -f generate.sql
--
-- Skew that matters for query plans:
--   * tenants: power(random(), 3) puts most tickets on a few big tenants
--   * status: 72% closed, 14% solved, 9% open, 5% pending
--   * time: two years, denser towards now (the product grew)

SELECT setseed(0.42);

INSERT INTO tenants (id, name, plan, created_at)
SELECT g, 'tenant-' || g,
       (ARRAY['free','team','business','enterprise'])[1 + (g % 10 = 0)::int + (g % 50 = 0)::int + (g % 200 = 0)::int],
       now() - interval '900 days' + (g * interval '6 hours')
FROM generate_series(1, :tenants) g;

INSERT INTO users (id, tenant_id, email, name, created_at)
SELECT g,
       1 + floor(:tenants * power(random(), 3))::int,
       'user' || g || '@example.test',
       'User ' || g,
       now() - random() * interval '800 days'
FROM generate_series(1, :users) g;

-- Tickets. The random draws live in a subquery's select list: Postgres
-- evaluates volatile functions there once per row and won't flatten it.
-- (A LATERAL subquery that doesn't reference the outer row can be computed
-- once for all rows, which would give every ticket the same tenant.)
INSERT INTO tickets (id, tenant_id, requester_id, assignee_id, status, priority, subject, tags, attrs,
                     created_at, updated_at, first_response_at, solved_at)
SELECT g,
       tenant_id,
       1 + floor(random() * :users)::int,
       CASE WHEN random() < 0.08 THEN NULL ELSE 1 + floor(random() * :users)::int END,
       status,
       1 + floor(power(random(), 0.6) * 4)::smallint,
       'Ticket ' || g || ': ' || (ARRAY['login fails','invoice wrong','export slow','api 500','sso error','refund request'])[1 + floor(random() * 6)::int],
       CASE floor(random() * 6)::int
         WHEN 0 THEN ARRAY['billing'] WHEN 1 THEN ARRAY['auth','sso'] WHEN 2 THEN ARRAY['api']
         WHEN 3 THEN ARRAY['export','performance'] WHEN 4 THEN ARRAY['billing','refund'] ELSE ARRAY['mobile'] END,
       jsonb_build_object(
         'channel', (ARRAY['email','web','chat','phone'])[1 + floor(random() * 4)::int],
         'region',  (ARRAY['in','us','eu','apac'])[1 + floor(power(random(), 2) * 4)::int],
         'sla_tier', 1 + floor(random() * 3)::int,
         'vip', random() < 0.02),
       created_at,
       created_at + random() * interval '10 days',
       CASE WHEN random() < 0.93 THEN created_at + random() * interval '30 hours' END,
       CASE WHEN status IN ('solved', 'closed') THEN created_at + random() * interval '12 days' END
FROM (
  SELECT g,
         1 + floor(:tenants * power(random(), 3))::int AS tenant_id,
         now() - power(random(), 1.7) * interval '730 days' AS created_at,
         CASE WHEN r < 0.72 THEN 'closed' WHEN r < 0.86 THEN 'solved' WHEN r < 0.95 THEN 'open' ELSE 'pending' END AS status
  FROM (SELECT g, random() AS r FROM generate_series(1, :tickets) g) raw
) x;

-- Events: about events_per_ticket per ticket, after the ticket was created.
INSERT INTO ticket_events (id, ticket_id, tenant_id, kind, actor_id, created_at, body)
SELECT row_number() OVER (), t.id, t.tenant_id,
       (ARRAY['created','comment','comment','status','assign','comment'])[1 + floor(random() * 6)::int],
       1 + floor(random() * :users)::int,
       t.created_at + random() * interval '12 days',
       CASE WHEN random() < 0.5 THEN 'Looked into it; ' || md5(random()::text) END
FROM tickets t
-- The count depends on t.id, so it's computed per ticket (and is repeatable).
CROSS JOIN LATERAL generate_series(1, 1 + ((t.id * 2654435761) % (2 * :events_per_ticket - 1))::int) n;

ANALYZE;
