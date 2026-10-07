#!/bin/sh
# 只初始化 SupportOps 的角色与数据库，业务表由 Alembic 管理。
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=app_password="$SUPPORTOPS_APP_PASSWORD" <<'SQL'
CREATE ROLE supportops_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
REVOKE ALL ON DATABASE supportops FROM PUBLIC;
GRANT CONNECT ON DATABASE supportops TO supportops_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO supportops_app;
CREATE DATABASE supportops_test OWNER supportops_admin;
REVOKE ALL ON DATABASE supportops_test FROM PUBLIC;
GRANT CONNECT ON DATABASE supportops_test TO supportops_app;
\connect supportops_test
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO supportops_app;
SQL
