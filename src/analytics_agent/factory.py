from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from analytics_agent.agent import AnalyticsAgent
from analytics_agent.catalog import Catalog, SchemaRetriever
from analytics_agent.database import PostgresDatabase, SQLiteDatabase, seed_sqlite
from analytics_agent.planners import DemoPlanner, LLMPlanner
from analytics_agent.policy import SQLPolicy
from analytics_agent.settings import Settings


def make_agent(catalog: Catalog, database, settings: Settings) -> AnalyticsAgent:
    planner = LLMPlanner(settings) if settings.mode == "llm" else DemoPlanner()
    return AnalyticsAgent(SchemaRetriever(catalog), planner, database, settings)


def sqlite_db(path: Path, catalog: Catalog, settings: Settings) -> SQLiteDatabase:
    return SQLiteDatabase(
        path,
        SQLPolicy("sqlite", "main", frozenset(table.name for table in catalog.tables)),
        settings.max_rows,
        settings.timeout_ms,
    )


@contextmanager
def retail_agent(settings: Settings):
    catalog = Catalog.retail()
    if settings.backend == "postgres":
        database = PostgresDatabase(
            SQLPolicy("postgres", "analytics", frozenset(table.name for table in catalog.tables)),
            settings,
        )
        database.execute("SELECT COUNT(*) FROM orders")
        yield make_agent(catalog, database, settings)
    else:
        with TemporaryDirectory(prefix="agentic-retail-") as directory:
            path = Path(directory) / "retail.sqlite"
            seed_sqlite(path)
            yield make_agent(catalog, sqlite_db(path, catalog, settings), settings)
