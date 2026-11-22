#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=reader_password="$ANALYTICS_READER_PASSWORD" <<'SQL'
REVOKE ALL ON DATABASE analytics FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA analytics;
CREATE ROLE analytics_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
  NOREPLICATION NOBYPASSRLS PASSWORD :'reader_password';
GRANT CONNECT ON DATABASE analytics TO analytics_reader;
GRANT USAGE ON SCHEMA analytics TO analytics_reader;
ALTER ROLE analytics_reader SET default_transaction_read_only = on;
ALTER ROLE analytics_reader SET search_path = pg_catalog;
\i /seed/retail.sql
GRANT SELECT ON analytics.customers, analytics.orders TO analytics_reader;
SQL
