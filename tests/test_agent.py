import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from analytics_agent.api import create_app
from analytics_agent.catalog import Catalog, SchemaRetriever, Table
from analytics_agent.database import seed_sqlite
from analytics_agent.evaluation import evaluate_demo
from analytics_agent.factory import make_agent, retail_agent, sqlite_db
from analytics_agent.planners import PlannerError, SQLPlan
from analytics_agent.settings import Settings


def test_catalog_retrieval_resolves_metric_dependency():
    retriever = SchemaRetriever(Catalog.retail())
    found = {doc.id for doc in retriever.retrieve("panier moyen", top_k=1)}
    assert found == {"metric:average_order_value", "table:orders"}


def test_catalog_preserves_sql_quoting_of_punctuation():
    retriever = SchemaRetriever(
        Catalog(
            tables=[
                Table(
                    name="rate history",
                    description="Taux",
                    columns={'Rate (%) "excl. tax"': "Taux net"},
                )
            ]
        )
    )
    [document] = retriever.retrieve("taux")
    assert 'Table "rate history"' in document.text
    assert '"Rate (%) ""excl. tax"""' in document.text


class SequencePlanner:
    def __init__(self, *statements):
        self.statements = iter(statements)
        self.feedback = []

    def plan(self, question, context, dialect, previous_sql, feedback, evidence):
        self.feedback.append(feedback)
        return SQLPlan(action="query", sql=next(self.statements)), (100, 20)


def test_agent_repairs_db_error_once():
    with retail_agent(Settings()) as agent:
        planner = SequencePlanner(
            "SELECT missing_column FROM orders", "SELECT COUNT(*) FROM orders"
        )
        agent.planner = planner
        result = agent.ask("Nombre total de commandes")
    assert planner.feedback == [None, "unknown_column"]
    assert result.result.rows == [[10]]
    assert result.corrections == 1
    assert result.usage.input_tokens == 200


def test_agent_stops_at_retry_limit():
    with retail_agent(Settings(max_corrections=1)) as agent:
        agent.planner = SequencePlanner(*["SELECT missing_column FROM orders"] * 2)
        result = agent.ask("Nombre total de commandes")
    assert result.status == "error"
    assert result.corrections == 1
    assert result.attempts == 2
    assert result.sql is None


def test_agent_does_not_repair_forbidden_plan():
    with retail_agent(Settings()) as agent:
        agent.planner = SequencePlanner("DELETE FROM orders")
        result = agent.ask("Supprime les commandes")
    assert result.status == "refused"
    assert result.attempts == 1
    assert [step.name for step in result.trace] == ["plan", "validate"]
    assert result.result is None


def test_model_error_is_sanitized():
    class Unavailable:
        def plan(self, *args):
            raise PlannerError("model_unavailable")

    with retail_agent(Settings()) as agent:
        agent.planner = Unavailable()
        result = agent.ask("Nombre total de commandes")
    assert result.status == "error"
    assert result.trace[-1].code == "model_unavailable"


@pytest.mark.parametrize(
    ("question", "chart"),
    [
        ("Quel est le chiffre d’affaires net par région ?", "bar"),
        ("Quel est le chiffre d’affaires net par mois ?", "line"),
    ],
)
def test_api_returns_verifiable_results(question, chart):
    with TestClient(create_app(Settings())) as client:
        response = client.post("/v1/analyze", json={"question": question})
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "answered"
        assert result["chart"]["kind"] == chart
        assert result["sql"].startswith("SELECT")
        assert "metric:net_revenue" in result["sources"]
        assert [item["name"] for item in result["trace"]] == [
            "plan",
            "validate",
            "execute",
            "summarize",
        ]
        assert client.post("/v1/analyze", json={"question": "     "}).status_code == 422


def test_demo_evaluation_expectations():
    report = evaluate_demo(Settings())
    assert report["metrics"]["execution_accuracy"] == 1
    assert report["metrics"]["unauthorized_refusal_rate"] == 1
    assert report["metrics"]["response_match_rate"] == 1


def test_same_question_reflects_new_orders_without_changing_the_planner(tmp_path):
    path = tmp_path / "sales.sqlite"
    seed_sqlite(path)
    catalog = Catalog.retail()
    settings = Settings()
    agent = make_agent(catalog, sqlite_db(path, catalog, settings), settings)
    question = "Donne-moi le CA du Nord en février 2025."
    assert agent.ask(question).result.rows == [[150]]
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
            (11, 1, "2025-02-28", "completed", 3750),
        )
    assert agent.ask(question).result.rows == [[187.5]]


def test_retail_text_and_guided_endpoints_compute_the_same_scoped_total():
    with TestClient(create_app(Settings())) as client:
        text = (
            client.post(
                "/v1/analyze",
                json={
                    "question": "Donne-moi le CA du Nord en février 2025.",
                },
            )
            .raise_for_status()
            .json()
        )
        guided = (
            client.post(
                "/v1/retail",
                json={
                    "metric": "revenue",
                    "region": "Nord",
                    "date_from": "2025-02-01",
                    "date_to": "2025-02-28",
                },
            )
            .raise_for_status()
            .json()
        )
    assert text["sql"] == guided["sql"]
    assert text["interpretation"] == guided["interpretation"]
    assert guided["interpretation"]["status"] == "completed"
    assert text["result"]["rows"] == guided["result"]["rows"] == [[150]]
    assert "relation:orders_customer" in guided["sources"]


@pytest.mark.parametrize(
    "body",
    [
        {"metric": "revenue", "status": "cancelled"},
        {"metric": "orders", "date_from": "2025-02-28", "date_to": "2025-02-01"},
        {"metric": "orders", "region": "Est"},
        {"metric": "orders", "sql": "SELECT 1"},
    ],
)
def test_guided_rejects_incompatible_filters(body):
    with TestClient(create_app(Settings())) as client:
        assert client.post("/v1/retail", json=body).status_code == 422


def test_ambiguous_request_explains_how_to_continue_without_running_sql():
    with TestClient(create_app(Settings())) as client:
        reply = client.post("/v1/analyze", json={"question": "Quel est le CA en février ?"})
    result = reply.raise_for_status().json()
    assert result["status"] == "clarify"
    assert "année" in result["answer"]
    assert result["result"] is result["sql"] is None
    assert all(stage["name"] != "execute" for stage in result["trace"])


def test_regional_absence_is_checked_across_all_customers_in_the_region():
    question = "Quelle région n’a aucune commande annulée ?"
    with TestClient(create_app(Settings())) as client:
        response = client.post("/v1/analyze", json={"question": question}).raise_for_status().json()
    assert response["status"] == "answered"
    assert response["interpretation"]["absence"] is True
    assert response["interpretation"]["status"] == "cancelled"
    assert "NOT EXISTS" in response["sql"]
    assert response["result"]["rows"] == [["Nord"]]
