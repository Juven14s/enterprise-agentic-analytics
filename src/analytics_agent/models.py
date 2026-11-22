from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Question(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1500)
    ]


class RetailQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: Literal["revenue", "orders", "average_order_value"]
    group_by: Literal["region", "month", "status"] | None = None
    region: Literal["Nord", "Sud", "Ouest"] | None = None
    status: Literal["completed", "cancelled", "pending"] | None = None
    date_from: date | None = None
    date_to: date | None = None
    absence: bool = False

    @model_validator(mode="after")
    def validate_scope(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("La date de début doit précéder ou égaler la date de fin.")
        if self.metric != "orders" and (
            self.status not in (None, "completed") or self.group_by == "status"
        ):
            raise ValueError("Les métriques de vente couvrent uniquement les commandes terminées.")
        if self.absence and (
            self.metric != "orders" or self.group_by != "region" or not self.status or self.region
        ):
            raise ValueError(
                "Une recherche d’absence exige un statut et le regroupement par région."
            )
        return self

    @property
    def effective_status(self):
        return self.status if self.metric == "orders" else "completed"


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


class Chart(BaseModel):
    kind: Literal["bar", "line"]
    x: str
    y: str


class Stage(BaseModel):
    name: str
    code: str
    attempt: int
    elapsed_ms: float


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class Analysis(BaseModel):
    request_id: str
    status: Literal["answered", "refused", "clarify", "error"]
    answer: str
    sql: str | None = None
    result: QueryResult | None = None
    chart: Chart | None = None
    interpretation: RetailQuery | None = None
    sources: list[str] = Field(default_factory=list)
    attempts: int = 0
    corrections: int = 0
    valid_sql: bool = False
    latency_ms: float
    usage: Usage
    trace: list[Stage]
