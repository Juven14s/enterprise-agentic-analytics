import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from time import monotonic
from typing import Any

import psycopg

from analytics_agent.models import QueryResult
from analytics_agent.policy import SQLITE_FUNCTIONS, SQLPolicy
from analytics_agent.settings import Settings


class ExecutionError(RuntimeError):
    def __init__(self, code: str, *, repairable: bool = False):
        super().__init__(code)
        self.code = code
        self.repairable = repairable


def _result(columns: list[str], rows: list, cap: int) -> QueryResult:
    distinct: list[str] = []
    used: set[str] = set()
    for name in columns:
        label = name
        counter = 2
        while label in used:
            label = f"{name}_{counter}"
            counter += 1
        distinct.append(label)
        used.add(label)
    clean_rows: list[list[Any]] = []
    for row in rows[:cap]:
        clean_rows.append(
            [
                value.hex()
                if isinstance(value, bytes)
                else str(value)
                if isinstance(value, float) and not math.isfinite(value)
                else value
                for value in row
            ]
        )
    return QueryResult(columns=distinct, rows=clean_rows, truncated=len(rows) > cap)


def seed_sqlite(path: Path) -> None:
    seed = files("analytics_agent").joinpath("data/retail.sql").read_text(encoding="utf-8")
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(seed.replace("analytics.", ""))


@dataclass(frozen=True)
class SQLiteDatabase:
    path: Path
    policy: SQLPolicy
    cap: int
    timeout_ms: int

    def _authorize(self, action, name, column, database, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and name in self.policy.tables:
            # SQLite may omit the database name for COUNT(*) on a table.
            if database in ("main", None):
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and column and column.lower() in SQLITE_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def execute(self, sql: str) -> QueryResult:
        checked = self.policy.validate(sql)
        return self._read(checked)

    def read_reference(self, sql: str) -> QueryResult:
        """Run benchmark-provided reference SQL under SQLite's read-only authorizer."""
        return self._read(sql)

    def _read(self, sql: str) -> QueryResult:
        deadline = monotonic() + self.timeout_ms / 1000
        try:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=self.timeout_ms / 1000)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("PRAGMA trusted_schema=OFF")
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4_000_000)
                conn.set_progress_handler(lambda: int(monotonic() >= deadline), 1_000)
                conn.set_authorizer(self._authorize)
                cursor = conn.execute(sql)
                return _result(
                    [item[0] for item in cursor.description],
                    cursor.fetchmany(self.cap + 1),
                    self.cap,
                )
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", None)
            if code == sqlite3.SQLITE_INTERRUPT:
                raise ExecutionError("query_timeout") from error
            if code == sqlite3.SQLITE_AUTH:
                raise ExecutionError("db_permission_denied") from error
            if code == sqlite3.SQLITE_ERROR:
                message = str(error).lower()
                if "not authorized" in message:
                    raise ExecutionError("db_permission_denied") from error
                if "no such column" in message:
                    raise ExecutionError("unknown_column", repairable=True) from error
                if "ambiguous column" in message:
                    raise ExecutionError("ambiguous_column", repairable=True) from error
                raise ExecutionError("sql_execution", repairable=True) from error
            raise ExecutionError("database_unavailable") from error


@dataclass(frozen=True)
class PostgresDatabase:
    policy: SQLPolicy
    settings: Settings

    def connect(self):
        return psycopg.connect(
            host=self.settings.pg_host,
            port=self.settings.pg_port,
            dbname=self.settings.pg_database,
            user=self.settings.pg_user,
            password=self.settings.pg_password.get_secret_value(),
            connect_timeout=3,
            options="-c search_path=pg_catalog",
        )

    def execute(self, sql: str) -> QueryResult:
        checked = self.policy.validate(sql)
        try:
            with self.connect() as conn:
                conn.read_only = True
                timeout = str(self.settings.timeout_ms)
                conn.execute(
                    "SELECT pg_catalog.set_config('statement_timeout', %s, true)", (timeout,)
                )
                conn.execute("SELECT pg_catalog.set_config('lock_timeout', %s, true)", (timeout,))
                with conn.cursor(name="analytics_result") as cursor:
                    cursor.execute(checked)
                    rows = cursor.fetchmany(self.settings.max_rows + 1)
                    columns = [item.name for item in cursor.description]
                return _result(columns, rows, self.settings.max_rows)
        except psycopg.Error as error:
            if error.sqlstate in {"42501", "25006"}:
                raise ExecutionError("db_permission_denied") from error
            if error.sqlstate in {"57014", "55P03"}:
                raise ExecutionError("query_timeout") from error
            common = {
                "42703": "unknown_column",
                "42702": "ambiguous_column",
                "42601": "sql_syntax",
                "42803": "invalid_grouping",
                "42883": "unknown_function",
                "42804": "type_mismatch",
            }
            if error.sqlstate in common:
                raise ExecutionError(common[error.sqlstate], repairable=True) from error
            status = "sql_execution" if error.sqlstate else "database_unavailable"
            raise ExecutionError(status) from error
