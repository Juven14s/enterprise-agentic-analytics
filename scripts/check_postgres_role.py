"""Verify the configured PostgreSQL reader's effective permissions."""

from analytics_agent.database import PostgresDatabase
from analytics_agent.policy import SQLPolicy
from analytics_agent.settings import Settings


def main() -> None:
    settings = Settings()
    schema, tables = (
        ("online_retail", ("invoice_lines",))
        if settings.dataset == "online_retail"
        else ("analytics", ("orders", "customers"))
    )
    database = PostgresDatabase(
        SQLPolicy("postgres", schema, frozenset(tables)),
        settings,
    )
    with database.connect() as connection:
        privileges = connection.execute(
            """
            SELECT
                current_setting('transaction_read_only') = 'on',
                has_database_privilege(current_user, current_database(), 'CREATE, TEMP'),
                has_schema_privilege(current_user, %s, 'CREATE'),
                has_schema_privilege(current_user, 'public', 'CREATE'),
                rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls,
                EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members WHERE member = pg_roles.oid)
            FROM pg_catalog.pg_roles WHERE rolname = current_user
            """,
            (schema,),
        ).fetchone()
        assert privileges == (True, False, False, False, False, False), privileges
        for name in tables:
            table = f"{schema}.{name}"
            table_privileges = connection.execute(
                """
                SELECT has_table_privilege(current_user, %s, 'SELECT'),
                       has_table_privilege(current_user, %s,
                           'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES')
                """,
                (table, table),
            ).fetchone()
            assert table_privileges == (True, False), (table, table_privileges)
    print("The PostgreSQL reader has SELECT and no write, create, or inherited privileges.")


if __name__ == "__main__":
    main()
