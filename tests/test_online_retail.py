from decimal import Decimal

from langchain_core.messages import AIMessage

from analytics_agent.agent import AnalyticsAgent
from analytics_agent.models import QueryResult
from analytics_agent.online_retail import EXAMPLES, OnlineRetailPlanner, RetailAnalyticsTool
from analytics_agent.policy import SQLPolicy
from analytics_agent.settings import Settings


class Database:
    policy = SQLPolicy("postgres", "online_retail", frozenset({"invoice_lines"}))

    def __init__(self):
        self.statements = []
        self.result = QueryResult(
            columns=["panier_moyen_gbp", "factures"], rows=[[Decimal("12.34"), 2]], truncated=False
        )

    def execute(self, sql):
        self.statements.append(self.policy.validate(sql))
        return self.result


class Model:
    def __init__(self, args):
        self.args = args
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(
            content="",
            tool_calls=[{"name": RetailAnalyticsTool.__name__, "args": self.args, "id": "call"}],
            usage_metadata={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        )


def make_agent(**overrides):
    settings = Settings(
        dataset="online_retail",
        mode="llm",
        backend="postgres",
        llm_base_url="http://127.0.0.1:8773/v1",
    )
    planner = OnlineRetailPlanner(settings)
    planner.model = model = Model(
        {
            "metric": "average_order_value",
            "from_month": "2011-11",
            "through_month": "2011-11",
            "view": "total",
        }
        | overrides
    )
    database = Database()
    return AnalyticsAgent(None, planner, database, settings), model, database


def test_tool_month_is_checked_and_translated_to_scoped_sql():
    agent, model, database = make_agent()
    analysis = agent.ask(EXAMPLES[0])
    assert analysis.status == "answered"
    assert analysis.answer == "Le panier moyen est de 12,34 £ sur 2 factures."
    assert analysis.online_retail_scope is not None
    assert analysis.online_retail_scope.month == "2011-11"
    assert [stage.name for stage in analysis.trace] == ["plan", "validate", "execute", "summarize"]
    assert analysis.usage.input_tokens == 20
    assert model.calls == 1
    assert database.statements == [analysis.sql]
    assert 'FROM "online_retail".invoice_lines' in analysis.sql
    assert "invoice_date < '2011-12-01'" in analysis.sql


def test_wrong_month_and_extra_tool_arguments_never_run_sql():
    agent, model, database = make_agent(from_month="2011-10")
    assert agent.ask(EXAMPLES[0]).status == "clarify"
    model.args["from_month"] = "2011-11"
    model.args["metric"] = "orders"
    assert agent.ask(EXAMPLES[0]).status == "clarify"
    model.args["metric"] = "average_order_value"
    model.args["view"] = "monthly"
    assert agent.ask(EXAMPLES[0]).status == "clarify"
    model.args["sql"] = "unexpected"
    malformed = agent.ask(EXAMPLES[0])
    assert malformed.status == "error"
    assert malformed.trace[0].code == "invalid_tool_response"
    assert database.statements == []


def test_monthly_series_respects_inclusive_range_and_builds_chart():
    agent, _, database = make_agent(
        metric="revenue", from_month="2010-12", through_month="2011-02", view="monthly"
    )
    database.result = QueryResult(
        columns=["mois", "chiffre_affaires_gbp"],
        rows=[["2010-12", Decimal("1400.00")], ["2011-01", Decimal("1200.00")]],
        truncated=False,
    )
    analysis = agent.ask("Montre le CA par mois au Royaume-Uni de décembre 2010 à février 2011.")
    assert analysis.status == "answered"
    assert analysis.online_retail_scope is not None
    assert analysis.online_retail_scope.last_month == "2011-02"
    assert analysis.chart is not None and analysis.chart.kind == "line"
    assert analysis.sql is not None
    assert "invoice_date >= '2010-12-01'" in analysis.sql
    assert "invoice_date < '2011-03-01'" in analysis.sql
    assert "£ (2010-12)" in analysis.answer


def test_invalid_or_unsupported_scope_and_mutation_do_not_call_model():
    agent, model, database = make_agent()
    for period in (
        "",
        "en décembre 2011",
        "en octobre et novembre 2011",
        "le 2011-11-01",
        "de 2011-11 à 2011-10",
        "de 2010-11 à 2011-10",
        "en novembre dernier",
    ):
        assert agent.ask(f"Panier moyen au Royaume-Uni {period} ?").status == "clarify"
    for suffix in ("uniquement le dimanche", "hors commandes de gros", "en euros"):
        assert agent.ask(f"Panier moyen au Royaume-Uni en novembre 2011 {suffix} ?").status == (
            "clarify"
        )
    assert agent.ask("Supprime les factures annulées en novembre 2011.").status == "refused"
    assert agent.ask("Combien de commandes en France en novembre 2011 ?").status == "clarify"
    assert agent.ask("Donne le panier moyen et le CA en novembre 2011.").status == "clarify"
    assert model.calls == 0
    assert database.statements == []
