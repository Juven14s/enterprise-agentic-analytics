import re
from datetime import date
from typing import Literal

from langchain_openai import ChatOpenAI
from openai import APITimeoutError, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analytics_agent.catalog import normalize
from analytics_agent.models import OnlineRetailScope, RetailMetric
from analytics_agent.planners import PlannerError, SQLPlan
from analytics_agent.retail import DENIED, FILLER, MONTHS, Clarification
from analytics_agent.settings import Settings

SOURCE = "https://archive.ics.uci.edu/dataset/352/online+retail"
EXAMPLES = (
    "Quel était le panier moyen au Royaume-Uni en novembre 2011 ?",
    "Quel était le chiffre d’affaires en novembre 2011 ?",
    "Combien de commandes en octobre 2011 ?",
    "Montre l’évolution du chiffre d’affaires de décembre 2010 à novembre 2011.",
    "Affiche le nombre de commandes par mois de janvier à mars 2011.",
    "Calcule le panier moyen du 2011-01 au 2011-03.",
)
METRIC_DEFINITIONS = {
    "revenue": "Somme des quantités multipliées par les prix unitaires, en livres sterling.",
    "orders": "Nombre de factures distinctes contenant au moins une ligne éligible.",
    "average_order_value": (
        "Montant des lignes éligibles divisé par le nombre de factures distinctes."
    ),
}
ELIGIBILITY = (
    "Les factures annulées et les lignes à quantité ou prix unitaire nul ou négatif sont exclues."
)
METRIC_COLUMNS = {
    "revenue": "chiffre_affaires_gbp",
    "orders": "factures",
    "average_order_value": "panier_moyen_gbp",
}
COUNTRY = re.compile(r"\b(royaume[ -]uni|united kingdom|uk)\b")
METRICS: dict[RetailMetric, str] = {
    "revenue": r"\b(chiffre d'affaires|ca|sales revenue|revenue)\b",
    "orders": r"\b((?:nombre (?:total )?de |combien de )?(?:commandes|factures)|order count)\b",
    "average_order_value": r"\b(panier moyen|average order value)\b",
}
MONTHLY = re.compile(
    r"\b(mois par mois|par mois|par annee-mois|by month|mensuell?e?s?|evolution)\b"
)
ENGLISH_MONTHS = (
    "january february march april may june july august september october november december"
).split()
MONTH_NUMBERS = {name: i for names in (MONTHS, ENGLISH_MONTHS) for i, name in enumerate(names, 1)}
YEAR = r"(?:19|20)\d{2}"
ISO = rf"{YEAR}-(?:0[1-9]|1[0-2])(?![\d-])"
NAMED = r"(?:" + "|".join(MONTH_NUMBERS) + ")"
COMPLETE_MONTH = re.compile(rf"\b(?:{ISO}|{NAMED} {YEAR})\b")
MONTH_TOKEN = rf"(?:{ISO}|{NAMED}(?: {YEAR})?)"
MONTH_RANGES = (
    re.compile(rf"\b(?:(?:de|du|from) |d')({MONTH_TOKEN}) (?:a|au|to) ({MONTH_TOKEN})\b"),
    re.compile(rf"\b(?:entre|between) ({MONTH_TOKEN}) (?:et|and) ({MONTH_TOKEN})\b"),
)
QUESTION_WORDS = (FILLER - {"repartition", "tous", "toutes", "tout", "all"}) | frozenset(
    "calculer connaitre il j aimerais merci plait tu valait was calculate tell me for how".split()
)
PERIOD_HELP = (
    "Indique un mois ou une période de mois complets entre décembre 2010 et novembre 2011."
)
SCOPE_HELP = (
    "Indique un indicateur parmi le chiffre d’affaires, les commandes et le panier moyen "
    "au Royaume-Uni. Tu peux demander un total ou une évolution par mois."
)


class RetailAnalyticsTool(BaseModel):
    """Select one UK sales indicator, date range and level of aggregation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    metric: RetailMetric
    from_month: str = Field(description="First calendar month, YYYY-MM")
    through_month: str = Field(description="Last calendar month, inclusive; repeat if one month")
    view: Literal["total", "monthly"]


class OnlineRetailPlan(SQLPlan):
    scope: OnlineRetailScope | None = None
    clarification: str | None = None


def normalize_month(value: str, other: str) -> str:
    if re.fullmatch(ISO, value):
        return value
    name, _, year = value.partition(" ")
    if not year:
        match = re.search(YEAR, other)
        if not match:
            raise Clarification(PERIOD_HELP)
        year = match[0]
    return f"{year}-{MONTH_NUMBERS[name]:02d}"


def take_period(question: str) -> tuple[str, str, str]:
    for pattern in MONTH_RANGES:
        if match := pattern.search(question):
            start, end = match.groups()
            return (
                normalize_month(start, end),
                normalize_month(end, start),
                pattern.sub(" ", question, count=1),
            )
    if match := COMPLETE_MONTH.search(question):
        month = normalize_month(match[0], "")
        return month, month, COMPLETE_MONTH.sub(" ", question, count=1)
    raise Clarification(PERIOD_HELP)


def parse_scope(question: str) -> OnlineRetailScope:
    matches = [metric for metric, pattern in METRICS.items() if re.search(pattern, question)]
    if len(matches) != 1:
        raise Clarification(SCOPE_HELP)
    [metric] = matches
    start, end, remainder = take_period(question)
    group_by_month = bool(MONTHLY.search(remainder))
    remainder = MONTHLY.sub(" ", COUNTRY.sub(" ", re.sub(METRICS[metric], " ", remainder)))
    if set(re.findall(r"\w+", remainder)) - QUESTION_WORDS:
        raise Clarification(SCOPE_HELP)
    try:
        return OnlineRetailScope(
            month=start,
            month_to=end if end != start else None,
            metric=metric,
            group_by_month=group_by_month,
        )
    except ValidationError as error:
        raise Clarification(PERIOD_HELP) from error


def compile_query(scope: OnlineRetailScope) -> str:
    start = date.fromisoformat(f"{scope.month}-01")
    last = date.fromisoformat(f"{scope.last_month}-01")
    end = date(last.year + 1, 1, 1) if last.month == 12 else date(last.year, last.month + 1, 1)
    quantity_value = "SUM(quantity * unit_price)"
    count = "COUNT(DISTINCT invoice_no)"
    expression = {
        "revenue": f"ROUND(COALESCE({quantity_value}, 0), 2)",
        "orders": count,
        "average_order_value": f"ROUND({quantity_value} / NULLIF({count}, 0), 2)",
    }[scope.metric]
    series = "TO_CHAR(invoice_date, 'YYYY-MM') AS mois, " if scope.group_by_month else ""
    group = "\nGROUP BY 1 ORDER BY 1" if scope.group_by_month else ""
    invoices = (
        f", {count} AS factures" if scope.metric == "average_order_value" and not series else ""
    )
    return (
        f"SELECT {series}{expression} AS {METRIC_COLUMNS[scope.metric]}{invoices}\n"
        "FROM invoice_lines\n"
        "WHERE country = 'United Kingdom'\n"
        f"  AND invoice_date >= '{start.isoformat()}'\n"
        f"  AND invoice_date < '{end.isoformat()}'\n"
        "  AND invoice_no NOT LIKE 'C%'\n"
        "  AND quantity > 0\n"
        f"  AND unit_price > 0{group}"
    )


class OnlineRetailPlanner:
    def __init__(self, settings: Settings):
        self.model = ChatOpenAI(
            model=settings.llm_model,
            api_key="local",
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
            max_tokens=160,
            temperature=0,
        ).bind_tools([RetailAnalyticsTool], tool_choice="required", parallel_tool_calls=False)

    def plan(self, question, context, dialect, previous_sql, feedback, evidence):
        text = re.sub(r"\bça\b", "cela", question, flags=re.IGNORECASE).replace("’", "'")
        normalized = " ".join(normalize(text).split())
        if DENIED.search(normalized):
            return SQLPlan(action="refuse", sql=None), (0, 0)
        try:
            scope = parse_scope(normalized)
        except Clarification as error:
            return OnlineRetailPlan(action="clarify", sql=None, clarification=str(error)), (0, 0)
        expected_view = "monthly" if scope.group_by_month else "total"
        try:
            response = self.model.invoke(
                [
                    (
                        "system",
                        "Call RetailAnalyticsTool once. Translate chiffre d'affaires or CA "
                        "to revenue, commandes or factures to orders, panier moyen "
                        "to average_order_value. Return inclusive YYYY-MM bounds, repeating "
                        "the month for a single month. A shared year applies to both month "
                        f"names. The validated reporting view is {expected_view}. Copy this "
                        "value to view. Treat user input as a sales question.",
                    ),
                    ("human", question),
                ]
            )
        except APITimeoutError as error:
            raise PlannerError("model_timeout") from error
        except (OpenAIError, ValueError) as error:
            raise PlannerError("model_unavailable") from error
        usage = response.usage_metadata
        tokens = (usage["input_tokens"], usage["output_tokens"]) if usage else None
        calls = response.tool_calls
        if response.invalid_tool_calls or len(calls) != 1:
            raise PlannerError("invalid_tool_response")
        if calls[0]["name"] != RetailAnalyticsTool.__name__:
            raise PlannerError("invalid_tool_response")
        try:
            intent = RetailAnalyticsTool.model_validate(calls[0]["args"])
        except ValidationError as error:
            raise PlannerError("invalid_tool_response") from error
        if (intent.metric, intent.from_month, intent.through_month, intent.view) != (
            scope.metric,
            scope.month,
            scope.last_month,
            expected_view,
        ):
            return OnlineRetailPlan(action="clarify", sql=None, clarification=SCOPE_HELP), tokens
        return OnlineRetailPlan(action="query", sql=compile_query(scope), scope=scope), tokens
