import logging
import math
import re
from decimal import Decimal
from time import perf_counter
from typing import Literal, Protocol, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langsmith import Client, trace, tracing_context

from analytics_agent.catalog import ContextDocument, SchemaRetriever
from analytics_agent.database import ExecutionError
from analytics_agent.models import (
    Analysis,
    Chart,
    OnlineRetailScope,
    QueryResult,
    RetailQuery,
    Stage,
    Usage,
)
from analytics_agent.online_retail import SOURCE, OnlineRetailPlan
from analytics_agent.planners import Planner, PlannerError, RetailPlan, make_retail_plan
from analytics_agent.policy import PolicyError, SQLPolicy
from analytics_agent.retail import retail_sources
from analytics_agent.settings import Settings

logger = logging.getLogger(__name__)

MESSAGES = {
    "refused": "Cette demande dépasse le périmètre des analyses autorisées en lecture seule.",
    "clarify": "Précise la métrique et, si nécessaire, la période ou le regroupement souhaité.",
    "error": "La requête n’a pas abouti. Consulte les étapes de vérification.",
}
LABELS = {
    "chiffre_affaires_eur": "Chiffre d’affaires net HT",
    "panier_moyen_eur": "Panier moyen net HT",
    "commandes": "Nombre de commandes",
    "commandes_terminees": "Commandes terminées",
}


class Database(Protocol):
    policy: SQLPolicy

    def execute(self, sql: str) -> QueryResult: ...


class State(TypedDict, total=False):
    question: str | RetailQuery
    evidence: str | None
    context: list[ContextDocument]
    trace: list[Stage]
    attempts: int
    candidate: str | None
    checked: str | None
    feedback: str | None
    route: str
    status: Literal["answered", "refused", "clarify", "error"]
    result: QueryResult | None
    chart: Chart | None
    answer: str
    valid_sql: bool
    interpretation: RetailQuery | None
    online_retail_scope: OnlineRetailScope | None
    clarification: str | None
    usage: Usage
    usage_complete: bool


def _display(value) -> str:
    if value is None:
        return "non disponible"
    return str(value)[:120].replace("\n", " ")


def summarize(result: QueryResult) -> tuple[str, Chart | None]:
    rows = result.rows
    if not rows:
        return "Aucune ligne ne correspond à cette question.", None
    columns = result.columns
    suffix = " Affichage partiel : limite de lignes atteinte." if result.truncated else ""
    if len(rows) == len(columns) == 1 and not result.truncated:
        label = LABELS.get(columns[0], columns[0])
        unit = " EUR" if columns[0].endswith("_eur") and rows[0][0] is not None else ""
        return f"{label} : {_display(rows[0][0])}{unit}.", None
    if len(columns) == 2 and 2 <= len(rows) <= 30:
        values = [row[1] for row in rows]
        keys = [row[0] for row in rows]
        if (
            all(isinstance(key, str) for key in keys)
            and len(set(keys)) == len(keys)
            and all(
                isinstance(value, (int, float, Decimal))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in values
            )
        ):
            kind = (
                "line" if all(re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", k) for k in keys) else "bar"
            )
            chart = Chart(kind=kind, x=columns[0], y=columns[1])
            if result.truncated:
                return f"{len(rows)} résultats affichés.{suffix}", chart
            best = max(range(len(values)), key=values.__getitem__)
            label = LABELS.get(columns[1], columns[1])
            unit = " EUR" if columns[1].endswith("_eur") else ""
            answer = (
                f"{len(rows)} résultats. Maximum observé pour {label} : "
                f"{_display(values[best])}{unit} ({_display(keys[best])})."
            )
            return answer, chart
    return f"{len(rows)} ligne(s) et {len(columns)} colonne(s).{suffix}", None


class AnalyticsAgent:
    def __init__(
        self,
        retriever: SchemaRetriever | None,
        planner: Planner,
        database: Database,
        settings: Settings,
    ):
        self.retriever = retriever
        self.planner = planner
        self.database = database
        self.settings = settings
        self.trace_client = (
            Client(api_key=settings.langsmith_api_key.get_secret_value())
            if settings.langsmith and settings.langsmith_api_key
            else None
        )
        graph = StateGraph(State)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("plan", self._plan)
        graph.add_node("validate", self._validate)
        graph.add_node("execute", self._execute)
        graph.add_node("summarize", self._summarize)
        graph.add_conditional_edges(
            START, self._initial_node, {"plan": "plan", "retrieve": "retrieve"}
        )
        graph.add_edge("retrieve", "plan")
        graph.add_conditional_edges("plan", self._route, {"validate": "validate", "end": END})
        graph.add_conditional_edges(
            "validate", self._route, {"execute": "execute", "plan": "plan", "end": END}
        )
        graph.add_conditional_edges(
            "execute", self._route, {"summarize": "summarize", "plan": "plan", "end": END}
        )
        graph.add_edge("summarize", END)
        self.graph = graph.compile()

    @staticmethod
    def _route(state: State) -> str:
        return state["route"]

    def _initial_node(self, state: State) -> str:
        return "retrieve" if self.retriever and isinstance(state["question"], str) else "plan"

    @staticmethod
    def _stage(state: State, name: str, code: str, started: float, attempt=None) -> list[Stage]:
        return [
            *state["trace"],
            Stage(
                name=name,
                code=code,
                attempt=state["attempts"] if attempt is None else attempt,
                elapsed_ms=round((perf_counter() - started) * 1000, 2),
            ),
        ]

    def _retrieve(self, state: State) -> State:
        started = perf_counter()
        search = state["question"]
        assert self.retriever is not None and isinstance(search, str)
        if state.get("evidence"):
            search += " " + state["evidence"]
        if self.settings.llm_output == "sql":
            context = self.retriever.retrieve(search, top_k=8, include_relations=True)
        else:
            context = self.retriever.retrieve(search)
        return {"context": context, "trace": self._stage(state, "retrieve", "ok", started)}

    def _plan(self, state: State) -> State:
        started = perf_counter()
        attempt = state["attempts"] + 1
        base: State = {"attempts": attempt, "checked": None, "valid_sql": False}
        question = state["question"]
        dialect = self.database.policy.dialect
        try:
            if isinstance(question, RetailQuery):
                plan, tokens = make_retail_plan(question, dialect), (0, 0)
            else:
                plan, tokens = self.planner.plan(
                    question,
                    state.get("context", []),
                    dialect,
                    state.get("candidate"),
                    state.get("feedback"),
                    state.get("evidence"),
                )
        except PlannerError as error:
            return base | {
                "route": "end",
                "status": "error",
                "usage_complete": False,
                "trace": self._stage(state, "plan", str(error), started, attempt),
            }
        usage = state["usage"].model_copy()
        if tokens is not None:
            usage.input_tokens += tokens[0]
            usage.output_tokens += tokens[1]
        base |= {
            "usage": usage,
            "usage_complete": state["usage_complete"] and tokens is not None,
            "candidate": plan.sql,
            "trace": self._stage(state, "plan", plan.action, started, attempt),
        }
        if isinstance(plan, RetailPlan):
            base |= {"interpretation": plan.interpretation, "clarification": plan.clarification}
        if isinstance(plan, OnlineRetailPlan):
            base |= {"online_retail_scope": plan.scope, "clarification": plan.clarification}
        if plan.action != "query":
            return base | {
                "route": "end",
                "status": "refused" if plan.action == "refuse" else "clarify",
            }
        return base | {"route": "validate"}

    def _failed(
        self, state: State, stage: str, error: PolicyError | ExecutionError, started: float
    ) -> State:
        retry = (
            error.repairable
            and state.get("interpretation") is None
            and state.get("online_retail_scope") is None
            and state["attempts"] <= self.settings.max_corrections
        )
        denied = (isinstance(error, PolicyError) and not error.repairable) or (
            error.code == "db_permission_denied"
        )
        return {
            "route": "plan" if retry else "end",
            "feedback": error.code,
            "status": "refused" if denied else "error",
            "trace": self._stage(state, stage, error.code, started),
        }

    def _validate(self, state: State) -> State:
        started = perf_counter()
        try:
            checked = self.database.policy.validate(state["candidate"] or "")
        except PolicyError as error:
            return self._failed(state, "validate", error, started)
        return {
            "checked": checked,
            "valid_sql": True,
            "route": "execute",
            "trace": self._stage(state, "validate", "ok", started),
        }

    def _execute(self, state: State) -> State:
        started = perf_counter()
        try:
            result = self.database.execute(state["candidate"] or "")
        except (PolicyError, ExecutionError) as error:
            return self._failed(state, "execute", error, started)
        return {
            "result": result,
            "route": "summarize",
            "trace": self._stage(state, "execute", "ok", started),
        }

    def _summarize(self, state: State) -> State:
        started = perf_counter()
        result = state["result"]
        assert result is not None
        answer, chart = summarize(result)
        if state.get("online_retail_scope"):
            amount, invoices = result.rows[0]
            answer = (
                f"Le panier moyen est de {str(amount).replace('.', ',')} £ sur {invoices} factures."
                if amount is not None
                else "Aucune facture éligible sur ce périmètre."
            )
        interpretation = state.get("interpretation")
        if interpretation and interpretation.absence and result.rows:
            regions = ", ".join(str(row[0]) for row in result.rows)
            answer = f"Régions correspondantes : {regions}."
            if result.truncated:
                answer += " Affichage partiel."
        if (
            interpretation
            and interpretation.metric == "average_order_value"
            and result.rows == [[None]]
        ):
            answer = "Aucune commande terminée sur ce périmètre pour calculer le panier moyen."
        return {
            "status": "answered",
            "answer": answer,
            "chart": chart,
            "trace": self._stage(state, "summarize", "ok", started),
        }

    def ask(self, question: str | RetailQuery, *, evidence: str | None = None) -> Analysis:
        started = perf_counter()
        request_id = str(uuid4())
        # Global LangSmith environment variables cannot log graph inputs or model payloads.
        with tracing_context(enabled=False):
            state = self.graph.invoke(
                {
                    "question": question,
                    "evidence": evidence,
                    "trace": [],
                    "attempts": 0,
                    "usage": Usage(),
                    "usage_complete": True,
                }
            )
        usage = state["usage"]
        input_price = self.settings.input_usd_per_million
        output_price = self.settings.output_usd_per_million
        if self.settings.mode == "demo" or state.get("interpretation") is not None:
            usage.cost_usd = 0.0
        elif state["usage_complete"] and input_price is not None and output_price is not None:
            usage.cost_usd = (
                usage.input_tokens * input_price + usage.output_tokens * output_price
            ) / 1_000_000
        status = state["status"]
        interpretation = state.get("interpretation")
        online_retail_scope = state.get("online_retail_scope")
        sources = [doc.id for doc in state.get("context", [])]
        if interpretation:
            sources = retail_sources(interpretation)
        if online_retail_scope:
            sources = [SOURCE]
        if status == "answered":
            answer = state["answer"]
        else:
            answer = state.get("clarification") or MESSAGES[status]
        analysis = Analysis(
            request_id=request_id,
            status=status,
            answer=answer,
            sql=state.get("checked") if status == "answered" else None,
            result=state.get("result"),
            chart=state.get("chart"),
            interpretation=interpretation,
            online_retail_scope=online_retail_scope,
            sources=sources,
            attempts=state["attempts"],
            corrections=max(0, state["attempts"] - 1),
            valid_sql=state["valid_sql"],
            latency_ms=round((perf_counter() - started) * 1000, 2),
            usage=usage,
            trace=state["trace"],
        )
        if self.trace_client:
            self._record(analysis)
        return analysis

    def _record(self, analysis: Analysis) -> None:
        safe = analysis.model_dump(
            include={
                "request_id",
                "status",
                "attempts",
                "corrections",
                "valid_sql",
                "latency_ms",
                "usage",
                "trace",
            }
        )
        try:
            with tracing_context(enabled=True, parent=False):
                with trace(
                    "analytics_request",
                    inputs={"request_id": analysis.request_id},
                    client=self.trace_client,
                    project_name=self.settings.langsmith_project,
                ) as run:
                    run.end(outputs=safe)
        except Exception as error:
            logger.warning("LangSmith trace failed (%s)", type(error).__name__)
