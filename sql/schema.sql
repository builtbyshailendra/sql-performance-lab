-- A multi-tenant help-desk app: tenants, their users, tickets, and an event
-- log per ticket. The starting point is deliberately what many young apps
-- have: primary keys, foreign keys, and a couple of single-column indexes
-- someone added once. The lab's job is to find and fix what's slow.

DROP TABLE IF EXISTS ticket_events, tickets, users, tenants CASCADE;

CREATE TABLE tenants (
  id         int PRIMARY KEY,
  name       text NOT NULL,
  plan       text NOT NULL,              -- free, team, business, enterprise
  created_at timestamptz NOT NULL
);

CREATE TABLE users (
  id         int PRIMARY KEY,
  tenant_id  int NOT NULL REFERENCES tenants (id),
  email      text NOT NULL,
  name       text NOT NULL,
  created_at timestamptz NOT NULL
);

CREATE TABLE tickets (
  id                bigint PRIMARY KEY,
  tenant_id         int NOT NULL REFERENCES tenants (id),
  requester_id      int NOT NULL REFERENCES users (id),
  assignee_id       int REFERENCES users (id),   -- NULL: nobody has it yet
  status            text NOT NULL,               -- open, pending, solved, closed
  priority          smallint NOT NULL,           -- 1 urgent .. 4 low
  subject           text NOT NULL,
  tags              text[] NOT NULL,
  attrs             jsonb NOT NULL,              -- channel, region, sla_tier, ...
  created_at        timestamptz NOT NULL,
  updated_at        timestamptz NOT NULL,
  first_response_at timestamptz,
  solved_at         timestamptz
);

CREATE TABLE ticket_events (
  id         bigint PRIMARY KEY,
  ticket_id  bigint NOT NULL REFERENCES tickets (id),
  tenant_id  int NOT NULL,
  kind       text NOT NULL,                      -- created, comment, status, assign, ...
  actor_id   int NOT NULL,
  created_at timestamptz NOT NULL,
  body       text
);

-- The "someone added these once" indexes.
CREATE INDEX tickets_tenant_id ON tickets (tenant_id);
CREATE INDEX ticket_events_ticket_id ON ticket_events (ticket_id);
