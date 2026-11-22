from dataclasses import dataclass
from typing import Literal

from sqlglot import exp, parse
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import traverse_scope

Dialect = Literal["postgres", "sqlite"]

# The policy deliberately supports a subset of each dialect.
NODES = frozenset(
    """
    Select Union Except Intersect Subquery With CTE Table TableAlias Identifier Column Star Alias
    Literal Null Boolean Distinct From Join Where Group Having Order Ordered Limit Offset
    Paren Tuple Add Sub Mul Div IntDiv Mod Neg EQ NEQ GT GTE LT LTE Is And Or Not Between In
    Like ILike Escape Exists Case If Cast TryCast DataType DataTypeParam Interval Var DPipe
    Window WindowSpec Filter
""".split()
)

FUNCTIONS = frozenset(
    """
    ABS AVG CEIL CEILING COALESCE COUNT CONCAT DATE DATE_ADD DATE_DIFF DATE_PART DATE_TRUNC
    DATESTR_TO_DATE DATETIME DAY DENSE_RANK EXTRACT FIRST_VALUE FLOOR IF IFNULL JULIANDAY LAG
    INSTR LAST_VALUE LEAD LENGTH LEAST LOWER LTRIM MAX MIN MONTH NULLIF RANK REPLACE ROUND
    ROW_NUMBER RTRIM STR_TO_DATE STR_TO_TIME STRFTIME SUBSTR SUBSTRING SUM TIME_TO_STR
    TIMESTAMP_TRUNC STR_POSITION TOTAL TRIM TS_OR_DS_TO_TIMESTAMP UNIXEPOCH UPPER YEAR
""".split()
)

SQLITE_FUNCTIONS = frozenset(
    """
    abs avg ceil ceiling coalesce concat count date datetime dense_rank first_value floor
    ifnull instr julianday lag last_value lead length like lower ltrim max min nullif rank replace
    round row_number rtrim strftime substr substring sum total trim unixepoch upper
""".split()
)

TYPES = frozenset(
    """
    BIGINT BOOLEAN DATE DECIMAL DOUBLE FLOAT INT SMALLINT TEXT TIME TIMESTAMP TIMESTAMPTZ VARCHAR
""".split()
)


class PolicyError(ValueError):
    def __init__(self, code: str, *, repairable: bool = False):
        super().__init__(code)
        self.code = code
        self.repairable = repairable


@dataclass(frozen=True)
class SQLPolicy:
    dialect: Dialect
    schema: str
    tables: frozenset[str]

    def validate(self, sql: str) -> str:
        if not sql.strip() or len(sql) > 12_000:
            raise PolicyError("sql_size")
        try:
            statements = [item for item in parse(sql, read=self.dialect) if item is not None]
        except RecursionError as error:
            raise PolicyError("query_complexity") from error
        except SqlglotError as error:
            raise PolicyError("sql_syntax", repairable=True) from error
        if len(statements) != 1:
            raise PolicyError("single_statement_required")
        query = statements[0]
        if not isinstance(query, (exp.Select, exp.SetOperation)):
            raise PolicyError("read_query_required")

        for index, node in enumerate(query.walk()):
            if index >= 600:
                raise PolicyError("query_complexity")
            if type(node).__name__ in NODES:
                pass
            elif isinstance(node, exp.Func):
                name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                if name.upper() not in FUNCTIONS:
                    raise PolicyError("function_not_allowed")
            else:
                raise PolicyError("sql_feature_not_allowed")
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise PolicyError("recursive_query_not_allowed")
            if isinstance(node, exp.DataType) and node.this.name not in TYPES:
                raise PolicyError("type_not_allowed")
            if isinstance(node, exp.Join) and (
                node.args.get("method")
                or node.kind.upper() == "CROSS"
                or not (node.args.get("on") or node.args.get("using"))
            ):
                raise PolicyError("explicit_join_required")
            if isinstance(node, exp.TableAlias) and not isinstance(node.this, exp.Identifier):
                raise PolicyError("invalid_alias")

        # Normalize unquoted PostgreSQL names before differentiating CTEs from physical tables.
        if self.dialect == "postgres":
            for identifier in query.find_all(exp.Identifier):
                if not identifier.quoted:
                    identifier.set("this", identifier.name.lower())
        checked: set[int] = set()
        try:
            for scope in traverse_scope(query):
                for reference, source in scope.selected_sources.values():
                    if not isinstance(reference, exp.Table):
                        continue
                    checked.add(id(reference))
                    if not isinstance(source, exp.Table):
                        if reference.db or reference.catalog:
                            raise PolicyError("scope_not_allowed")
                        continue
                    if (
                        not isinstance(source.this, exp.Identifier)
                        or source.catalog
                        or source.db not in ("", self.schema)
                        or source.name not in self.tables
                    ):
                        raise PolicyError("table_not_allowed")
                    source.set("db", exp.to_identifier(self.schema, quoted=True))
            if any(id(table) not in checked for table in query.find_all(exp.Table)):
                raise PolicyError("scope_not_allowed")
            return query.sql(
                dialect=self.dialect, comments=False, unsupported_level=ErrorLevel.RAISE
            )
        except SqlglotError as error:
            raise PolicyError("sql_scope", repairable=True) from error
        except RecursionError as error:
            raise PolicyError("query_complexity") from error
