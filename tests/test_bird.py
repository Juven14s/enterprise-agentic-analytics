import json
from argparse import Namespace

from analytics_agent.bird import catalog_from_sqlite
from analytics_agent.database import seed_sqlite
from analytics_agent.evaluation import compare_results, evaluate_bird
from analytics_agent.models import QueryResult
from analytics_agent.settings import Settings


def test_bird_references_and_jsonl(tmp_path):
    seed_sqlite(tmp_path / "retail.sqlite")
    dataset = tmp_path / "mini.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "question_id": 7,
                "db_id": "retail",
                "question": "How many orders?",
                "SQL": "SELECT COUNT(*) FROM orders",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = evaluate_bird(
        Namespace(
            dataset=dataset,
            db_root=tmp_path,
            db_id=None,
            case_id=[7],
            limit=1,
            reference_check=True,
            ordered=False,
        ),
        Settings(),
    )
    assert report["references_executed"] == report["references_matching_after_policy"] == 1


def test_evaluation_compares_duplicates_and_rejects_partial_results():
    first = QueryResult(columns=["value"], rows=[[1], [1], [2]], truncated=False)
    second = QueryResult(columns=["alias"], rows=[[2], [1], [1]], truncated=False)
    assert compare_results(first, second, ordered=False)
    assert not compare_results(first, second, ordered=True)
    assert not compare_results(first, second.model_copy(update={"rows": [[1], [2]]}), False)
    assert not compare_results(first, second.model_copy(update={"truncated": True}), False)


def test_public_category_examples_are_optional_and_come_from_the_database(tmp_path):
    path = tmp_path / "retail.sqlite"
    seed_sqlite(path)
    assert all(not table.examples for table in catalog_from_sqlite(path).tables)
    tables = {table.name: table for table in catalog_from_sqlite(path, value_samples=True).tables}
    assert set(tables["customers"].examples["region"]) == {"Nord", "Sud", "Ouest"}
    assert set(tables["orders"].examples["status"]) == {"completed", "cancelled", "pending"}
    assert "customer_id" not in tables["orders"].examples
