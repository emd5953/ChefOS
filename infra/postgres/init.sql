-- Runs once, as the postgres superuser, on first start of a fresh volume (and in CI via psql).
-- chefos_owner owns the schema and runs migrations.
-- chefos_app is what the API and workers connect as: DML only, no DDL, no BYPASSRLS.

CREATE ROLE chefos_owner LOGIN PASSWORD 'chefos_owner' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
CREATE ROLE chefos_app LOGIN PASSWORD 'chefos_app' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

CREATE DATABASE chefos OWNER chefos_owner;
CREATE DATABASE chefos_test OWNER chefos_owner;  -- tests run here so they never touch dev data

\connect chefos
REVOKE ALL ON DATABASE chefos FROM PUBLIC;
GRANT CONNECT ON DATABASE chefos TO chefos_app;
CREATE EXTENSION IF NOT EXISTS citext;
GRANT USAGE ON SCHEMA public TO chefos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE chefos_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO chefos_app;

\connect chefos_test
REVOKE ALL ON DATABASE chefos_test FROM PUBLIC;
GRANT CONNECT ON DATABASE chefos_test TO chefos_app;
CREATE EXTENSION IF NOT EXISTS citext;
GRANT USAGE ON SCHEMA public TO chefos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE chefos_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO chefos_app;
