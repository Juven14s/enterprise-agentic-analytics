import csv
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from analytics_agent.catalog import Catalog, Definition, Table


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
                for field in ("column_name", "column_description")
                if row.get(field)
            )[:500]
            for row in reader
            if row.get("original_column_name")
        }


def catalog_from_sqlite(path: Path) -> Catalog:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    descriptions = path.parent / "database_description"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA trusted_schema=OFF")
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
            tables.append(Table(name=name, description=name.replace("_", " "), columns=columns))
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
