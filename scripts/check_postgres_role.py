"""Verify the current PostgreSQL demo login's effective permissions."""

from analytics_agent.database import PostgresDatabase
from analytics_agent.policy import SQLPolicy
from analytics_agent.settings import Settings


def main() -> None:
    database = PostgresDatabase(
        SQLPolicy("postgres", "analytics", frozenset({"orders", "customers"})),
        Settings(),
    )
    with database.connect() as connection:
        privileges = connection.execute("""
            SELECT
                current_setting('transaction_read_only') = 'on',
                has_table_privilege(current_user, 'analytics.orders', 'SELECT'),
                has_table_privilege(current_user, 'analytics.customers', 'SELECT'),
                has_table_privilege(current_user, 'analytics.orders',
                    'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES'),
                has_table_privilege(current_user, 'analytics.customers',
                    'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES'),
                has_database_privilege(current_user, current_database(), 'CREATE, TEMP'),
                has_schema_privilege(current_user, 'analytics', 'CREATE'),
                has_schema_privilege(current_user, 'public', 'CREATE'),
                rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls,
                EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member = pg_roles.oid)
            FROM pg_catalog.pg_roles WHERE rolname = current_user
        """).fetchone()
    assert privileges == (True, True, True, False, False, False, False, False, False, False), (
        privileges
    )
    print("The PostgreSQL reader has SELECT and no write, create, or inherited privileges.")


if __name__ == "__main__":
    main()
