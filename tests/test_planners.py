from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from analytics_agent.catalog import Catalog, SchemaRetriever, Table
from analytics_agent.planners import LLMPlanner, PlannerError
from analytics_agent.policy import PolicyError, SQLPolicy
from analytics_agent.settings import Settings


def make_planner():
    return LLMPlanner(Settings(mode="llm", llm_base_url="http://127.0.0.1:8773/v1"))


def test_planner_requires_one_tool_and_preserves_full_schema():
    planner = make_planner()
    assert planner.model.kwargs["tool_choice"] == "required"
    assert planner.model.kwargs["parallel_tool_calls"] is False
    schema = Table(
        name="sales",
        description="Sales",
        columns={"Long notes": "detail " * 550, "Rate (%)": "net rate"},
    )
    context = SchemaRetriever(Catalog(tables=[schema])).retrieve("sales")
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "SQLPlan",
                "args": {
                    "action": "query",
                    "sql": 'SELECT "Rate (%)" FROM "sales"',
                },
                "id": "case1",
            }
        ],
        usage_metadata={"input_tokens": 23, "output_tokens": 9, "total_tokens": 32},
    )
    planner.model = Mock()
    planner.model.invoke.return_value = response
    plan, tokens = planner.plan("Show rates", context, "sqlite", None, None, None)
    assert plan.sql == 'SELECT "Rate (%)" FROM "sales"'
    assert tokens == (23, 9)
    user_message = planner.model.invoke.call_args.args[0][1][1]
    assert '"sales" ("Long notes", "Rate (%)")' in user_message
    assert '"Rate (%)": net rate' in user_message
    assert "detail detail" not in user_message


@pytest.mark.parametrize(
    "tools",
    [
        [],
        [{"name": "OtherTool", "args": {"action": "query", "sql": "SELECT 1"}, "id": "case2"}],
        [{"name": "SQLPlan", "args": {"action": "query", "sql": None}, "id": "case3"}],
    ],
)
def test_planner_rejects_invalid_tool_calls(tools):
    planner = make_planner()
    planner.model = Mock()
    planner.model.invoke.return_value = AIMessage(content="", tool_calls=tools)
    with pytest.raises(PlannerError, match="invalid_tool_response"):
        planner.plan("Show rates", [], "sqlite", None, None, None)


@pytest.mark.parametrize(
    "content",
    [
        'SELECT "Rate (%)" FROM sales',
        'SELECT "Rate (%)" FROM sales\n```',
        '```sql\nSELECT "Rate (%)" FROM sales\n```',
    ],
)
def test_specialized_model_keeps_identifiers_and_extracts_one_sql_block(content):
    planner = LLMPlanner(
        Settings(mode="llm", llm_base_url="http://127.0.0.1:8774/v1", llm_output="sql")
    )
    planner.model = Mock()
    planner.model.invoke.return_value = AIMessage(content=content)
    catalog = Catalog(
        tables=[Table(name="sales", description="Sales", columns={"Rate (%)": "REAL Net rate"})]
    )
    context = SchemaRetriever(catalog).retrieve("net rate")
    plan, _ = planner.plan("Show rates", context, "sqlite", None, None, None)
    assert plan.sql == 'SELECT "Rate (%)" FROM sales'
    assert '"Rate (%)":REAL Net rate' in planner.model.invoke.call_args.args[0][0][1]
    policy = SQLPolicy("sqlite", "main", frozenset({"sales"}))
    assert 'FROM "main".sales' in policy.validate(plan.sql)


def test_specialized_model_has_same_write_controls():
    planner = LLMPlanner(
        Settings(mode="llm", llm_base_url="http://127.0.0.1:8774/v1", llm_output="sql"),
        protect_retail=True,
    )
    planner.model = Mock()
    refusal, _ = planner.plan("Supprime les commandes", [], "sqlite", None, None, None)
    assert refusal.action == "refuse"
    planner.model.invoke.assert_not_called()
    output = planner._parse_sql_output("```sql\nDELETE FROM sales\n```")
    with pytest.raises(PolicyError, match="read_query_required"):
        SQLPolicy("sqlite", "main", frozenset({"sales"})).validate(output.sql)
