-- Local-only runtime principals. Alembic validates these roles but never owns them.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clipah_api') THEN
        CREATE ROLE clipah_api
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clipah_worker') THEN
        CREATE ROLE clipah_worker
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clipah_api_runtime') THEN
        CREATE ROLE clipah_api_runtime
            LOGIN PASSWORD 'clipah_api_runtime_local'
            NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clipah_worker_runtime') THEN
        CREATE ROLE clipah_worker_runtime
            LOGIN PASSWORD 'clipah_worker_runtime_local'
            NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

ALTER ROLE clipah_api
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE clipah_worker
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE clipah_api_runtime
    LOGIN PASSWORD 'clipah_api_runtime_local'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE clipah_worker_runtime
    LOGIN PASSWORD 'clipah_worker_runtime_local'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Converge memberships as well as attributes. This removes stale direct and
-- transitive SET ROLE paths before granting each process login its sole group.
DO $$
DECLARE
    runtime_role text;
    parent_role text;
BEGIN
    FOREACH runtime_role IN ARRAY ARRAY[
        'clipah_api',
        'clipah_worker',
        'clipah_api_runtime',
        'clipah_worker_runtime',
        'clipah_runtime'
    ]
    LOOP
        FOR parent_role IN
            SELECT parent.rolname
            FROM pg_auth_members AS membership
            JOIN pg_roles AS member ON member.oid = membership.member
            JOIN pg_roles AS parent ON parent.oid = membership.roleid
            WHERE member.rolname = runtime_role
        LOOP
            EXECUTE 'REVOKE ' || quote_ident(parent_role)
                || ' FROM ' || quote_ident(runtime_role);
        END LOOP;
    END LOOP;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clipah_runtime') THEN
        ALTER ROLE clipah_runtime
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

GRANT clipah_api TO clipah_api_runtime;
GRANT clipah_worker TO clipah_worker_runtime;
