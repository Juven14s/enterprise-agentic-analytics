from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints


class Question(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1500)
    ]


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
    sources: list[str] = Field(default_factory=list)
    attempts: int = 0
    corrections: int = 0
    valid_sql: bool = False
    latency_ms: float
    usage: Usage
    trace: list[Stage]
