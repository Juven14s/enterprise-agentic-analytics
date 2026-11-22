import pytest
from fastapi.testclient import TestClient

from analytics_agent.api import create_app
from analytics_agent.catalog import Catalog, SchemaRetriever, Table
from analytics_agent.evaluation import evaluate_demo
from analytics_agent.factory import retail_agent
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
    assert [step.name for step in result.trace] == ["retrieve", "plan", "validate"]
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
            "retrieve",
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
