from pathlib import Path

import pytest

from analytics_agent.catalog import Catalog
from analytics_agent.database import ExecutionError, seed_sqlite
from analytics_agent.factory import sqlite_db
from analytics_agent.policy import PolicyError, SQLPolicy
from analytics_agent.settings import Settings


@pytest.fixture
def database(tmp_path: Path):
    path = tmp_path / "retail.sqlite"
    seed_sqlite(path)
    return sqlite_db(path, Catalog.retail(), Settings(max_rows=2))


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM orders", "read_query_required"),
        ("SELECT 1; SELECT 2", "single_statement_required"),
        ("SELECT order_id FROM private.orders", "table_not_allowed"),
        ("SELECT * FROM unrelated", "table_not_allowed"),
        ("WITH x AS (SELECT * FROM unrelated) SELECT * FROM x", "table_not_allowed"),
        ("SELECT RANDOM() FROM orders", "function_not_allowed"),
        ("SELECT * FROM orders CROSS JOIN customers", "explicit_join_required"),
        ("SELECT order_id FROM orders FOR UPDATE", "sql_feature_not_allowed"),
    ],
)
def test_denied_statements(sql, code):
    policy = SQLPolicy("postgres", "analytics", frozenset({"orders", "customers"}))
    with pytest.raises(PolicyError, match=code) as raised:
        policy.validate(sql)
    assert not raised.value.repairable


def test_cte_uses_qualified_physical_tables():
    policy = SQLPolicy("postgres", "analytics", frozenset({"orders"}))
    sql = policy.validate(
        "WITH Recent AS (SELECT Order_ID FROM Orders) SELECT order_id FROM RECENT"
    )
    assert 'FROM "analytics".orders' in sql
    assert "FROM recent" in sql
    assert policy.validate(sql) == sql


def test_cte_and_nested_subquery_execute(database):
    result = database.execute(
        "WITH v AS (SELECT order_id FROM orders WHERE order_id IN "
        "(SELECT order_id FROM orders WHERE order_id < 3)) SELECT * FROM v ORDER BY order_id"
    )
    assert result.rows == [[1], [2]]
    assert not result.truncated


def test_limit_reports_truncation_and_preserves_order(database):
    result = database.execute("SELECT order_id FROM orders ORDER BY order_id DESC")
    assert result.rows == [[10], [9]]
    assert result.truncated


def test_database_enforces_readonly_independently(database):
    with pytest.raises(ExecutionError, match="db_permission_denied"):
        database.read_reference("DELETE FROM orders")
    with pytest.raises(ExecutionError, match="db_permission_denied"):
        database.read_reference("SELECT name FROM sqlite_master")
    assert database.execute("SELECT COUNT(*) FROM orders").rows == [[10]]
