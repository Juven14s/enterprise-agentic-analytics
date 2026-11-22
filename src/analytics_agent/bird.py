import csv
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, Field, TypeAdapter

from analytics_agent.catalog import Catalog, Definition, Table, sql_identifier


class BirdCase(BaseModel):
    question_id: int
    db_id: str = Field(pattern=r"^[a-zA-Z0-9_]+$")
    question: str
    evidence: str | None = None
    sql: str = Field(alias="SQL")
    difficulty: str | None = None


def load_bird_cases(path: Path) -> list[BirdCase]:
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8-sig") as handle:
            return [BirdCase.model_validate_json(line) for line in handle if line.strip()]
    return TypeAdapter(list[BirdCase]).validate_json(path.read_bytes())


def _descriptions(folder: Path, table: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-zA-Z0-9_ .-]+", table):
        return {}
    path = folder / f"{table}.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        return {
            row["original_column_name"].strip().casefold(): " ".join(
                row.get(field, "").strip()
                for field in ("column_name", "column_description", "value_description")
                if row.get(field) and row[field].strip().casefold() != "not useful"
            )[:500]
            for row in reader
            if row.get("original_column_name")
        }


def _example_values(connection: sqlite3.Connection, table: str, columns: list[str]):
    candidates = [
        column
        for column in columns
        if re.search(
            r"language|region|district|gender|format|status|type|category|frequency|country",
            column,
            re.I,
        )
    ]
    result: dict[str, list[str]] = {}
    for column in candidates:
        name = sql_identifier(column)
        deadline = monotonic() + 0.18
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 500)
        try:
            values = connection.execute(
                f"SELECT DISTINCT {name} FROM (SELECT {name} FROM {sql_identifier(table)} "
                f"LIMIT 512) WHERE typeof({name}) = 'text' AND length({name}) BETWEEN 1 AND 30 "
                "LIMIT 8"
            )
            examples = [value for (value,) in values if value.isprintable()]
            if examples:
                result[column] = examples
        except sqlite3.OperationalError:
            continue  # Example values are optional when the small sampling budget expires.
        finally:
            connection.set_progress_handler(None, 0)
    return result


def catalog_from_sqlite(path: Path, *, value_samples: bool = False) -> Catalog:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    descriptions = path.parent / "database_description"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM pragma_table_list "
                "WHERE schema='main' AND type='table' AND name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ]
        tables = []
        relations = []
        for name in names:
            meanings = _descriptions(descriptions, name)
            info = connection.execute("SELECT name, type, pk FROM pragma_table_info(?)", (name,))
            columns = {
                column: " ".join(
                    filter(
                        None,
                        (
                            declared.upper(),
                            "PRIMARY KEY" if primary else "",
                            meanings.get(column.casefold(), ""),
                        ),
                    )
                )
                for column, declared, primary in info
            }
            examples = _example_values(connection, name, list(columns)) if value_samples else {}
            tables.append(
                Table(
                    name=name,
                    description=name.replace("_", " "),
                    columns=columns,
                    examples=examples,
                )
            )
            grouped: dict[tuple[int, str], list[str]] = {}
            keys = connection.execute(
                'SELECT id, "table", "from", "to" FROM pragma_foreign_key_list(?)', (name,)
            )
            for key, target, source, target_col in keys:
                actual = next(
                    (item for item in names if item.casefold() == target.casefold()), None
                )
                if actual and target_col:
                    grouped.setdefault((key, actual), []).append(
                        f"{name}.{source} = {actual}.{target_col}"
                    )
            for (key, actual), conditions in grouped.items():
                relations.append(
                    Definition(
                        id=f"fk:{name}:{key}",
                        tables=[name, actual],
                        definition=" AND ".join(conditions),
                    )
                )
    return Catalog(tables=tables, relations=relations)
