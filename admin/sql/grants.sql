-- Grants of the operator app's database account kosis_admin (T3-57, kosis-infra/12 3.3).
-- Run as the database owner after the role exists, and again after a service migration adds a table the operator
-- app reads. Make the role first (the password comes from the Secret; never write it in a file):
--   CREATE ROLE kosis_admin LOGIN PASSWORD '…';
-- then: psql "$DATABASE_URL" -f admin/sql/grants.sql   (plain SQL; tests run this file as is)

-- its own schema (feedback reviews, audit, its alembic version table)
CREATE SCHEMA IF NOT EXISTS admin AUTHORIZATION kosis_admin;
ALTER SCHEMA admin OWNER TO kosis_admin;

-- nothing in public but what is listed below
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM kosis_admin;
GRANT USAGE ON SCHEMA public TO kosis_admin;

-- the user log: read only
-- ingest_rejects: log quality card (T3-68); rerun this file on the server after deploying it
GRANT SELECT ON request_logs, request_rewrites, feedback, ui_events, ingest_rejects, daily_usage TO kosis_admin;
-- sign-in check and operator names: these columns only (no Kakao number, no settings)
GRANT SELECT (token_hash, user_id, expires_at) ON login_sessions TO kosis_admin;
GRANT SELECT (id, display_name, nickname) ON users TO kosis_admin;
-- conversations, messages, chat_sessions, chat_requests, vectors, checkpoints: no grant
