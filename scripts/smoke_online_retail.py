"""Verify the Online Retail API and interface against a PostgreSQL reference."""

import os
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import median

import httpx
from streamlit.testing.v1 import AppTest

from analytics_agent.models import Analysis, RetailMetric
from analytics_agent.online_retail import EXAMPLES

# Invoice counts and unrounded amounts from online_retail_reference.sql.
REFERENCE = {
    "2010-12": (1447, "748268.98"),
    "2011-01": (971, "561289.98"),
    "2011-02": (995, "430027.95"),
    "2011-03": (1308, "586230.28"),
    "2011-04": (1153, "476517.761"),
    "2011-05": (1523, "639314.36"),
    "2011-06": (1383, "619510.67"),
    "2011-07": (1337, "601994.841"),
    "2011-08": (1210, "608047.48"),
    "2011-09": (1645, "897076.472"),
    "2011-10": (1811, "938975.01"),
    "2011-11": (2490, "1325346.21"),
}
BOUNDARIES = {
    "Quel est le panier moyen au Royaume-Uni ?": "clarify",
    "Quel est le panier moyen au Royaume-Uni en décembre 2011 ?": "clarify",
    "Quel est le CA en 2011 ?": "clarify",
    "Compare le panier moyen au Royaume-Uni en octobre et novembre 2011.": "clarify",
    "Calcule le panier moyen au Royaume-Uni pour 2011-11 uniquement le dimanche.": "clarify",
    "Quel est le chiffre d’affaires en Allemagne en novembre 2011 ?": "clarify",
    "Donne le CA et le panier moyen en novembre 2011.": "clarify",
    "Supprime les factures annulées en novembre 2011.": "refused",
    "Affiche les emails des clients de novembre 2011.": "refused",
    "Modifie les montants de novembre 2011.": "refused",
}


@dataclass(frozen=True)
class Case:
    question: str
    metric: RetailMetric
    start: str
    end: str | None = None
    monthly: bool = False

    def values(self, count: int, amount: Decimal) -> list[int | str]:
        if self.metric == "orders":
            return [count]
        if self.metric == "revenue":
            return [str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))]
        average = (amount / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return [str(average)] if self.monthly else [str(average), count]

    def expected_rows(self) -> list[list[int | str]]:
        months = [
            (month, count, Decimal(amount))
            for month, (count, amount) in sorted(REFERENCE.items())
            if self.start <= month <= (self.end or self.start)
        ]
        if self.monthly:
            return [[month, *self.values(count, amount)] for month, count, amount in months]
        return [
            self.values(
                sum(count for _, count, _ in months),
                sum((amount for _, _, amount in months), Decimal(0)),
            )
        ]


CASES = [
    Case(f"Calcule le panier moyen au Royaume-Uni pour {month}.", "average_order_value", month)
    for month in REFERENCE
] + [
    Case(EXAMPLES[0], "average_order_value", "2011-11"),
    Case(EXAMPLES[1], "revenue", "2011-11"),
    Case(EXAMPLES[2], "orders", "2011-10"),
    Case(EXAMPLES[3], "revenue", "2010-12", "2011-11", True),
    Case(EXAMPLES[4], "orders", "2011-01", "2011-03", True),
    Case(EXAMPLES[5], "average_order_value", "2011-01", "2011-03"),
    Case("Montre le CA de novembre 2011.", "revenue", "2011-11"),
    Case("Affiche le nombre de factures d’août à octobre 2011.", "orders", "2011-08", "2011-10"),
    Case(
        "Montre le panier moyen par mois entre 2011-02 et 2011-04.",
        "average_order_value",
        "2011-02",
        "2011-04",
        True,
    ),
    Case("Calcule le chiffre d’affaires du 2010-12 au 2011-02.", "revenue", "2010-12", "2011-02"),
    Case("Affiche les commandes par mois en novembre 2011.", "orders", "2011-11", monthly=True),
]


def check_case(case: Case, analysis: Analysis) -> None:
    assert analysis.status == "answered", (case.question, analysis)
    scope = analysis.online_retail_scope
    assert scope is not None and scope.country == "United Kingdom", analysis
    assert (scope.metric, scope.month, scope.month_to, scope.group_by_month) == (
        case.metric,
        case.start,
        case.end,
        case.monthly,
    ), analysis
    assert analysis.result is not None and analysis.result.rows == case.expected_rows(), analysis
    assert analysis.valid_sql and analysis.attempts == 1 and analysis.corrections == 0, analysis
    assert analysis.usage.input_tokens > 0 and analysis.usage.output_tokens > 0, analysis
    assert analysis.usage.cost_usd == 0, analysis
    assert [stage.name for stage in analysis.trace] == ["plan", "validate", "execute", "summarize"]
    if case.monthly:
        assert analysis.chart and analysis.chart.kind == "line", analysis
    else:
        assert analysis.chart is None, analysis


def check_ui(api_url: str) -> None:
    os.environ["ANALYTICS_DATASET"] = "online_retail"
    os.environ["ANALYTICS_API_URL"] = api_url
    ui_path = Path(__file__).resolve().parents[1] / "src/analytics_agent/ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=90).run()
    app.text_area[0].set_value(EXAMPLES[0])
    app.button[0].click().run()
    assert not app.exception and not app.error, (app.exception, app.error)
    assert any("532,27 £ sur 2490 factures" in item.value for item in app.text)
    assert app.code and "invoice_lines" in app.code[0].value

    app.selectbox[0].select(EXAMPLES[3]).run()
    app.button[0].click().run()
    assert not app.exception and not app.error, (app.exception, app.error)
    assert "2010-12 à 2011-11" in " ".join(item.value for item in app.caption)
    assert app.get("vega_lite_chart") or app.get("arrow_vega_lite_chart")
    assert app.code and "GROUP BY" in app.code[0].value

    app.text_area[0].set_value("Supprime les factures annulées.")
    app.button[0].click().run()
    assert app.warning and not app.code and not app.exception


def main() -> None:
    api_url = os.environ.get("ANALYTICS_API_URL", "http://127.0.0.1:8072")
    ui_url = os.environ.get("ANALYTICS_UI_URL", "http://127.0.0.1:8572")
    elapsed = []
    with httpx.Client(base_url=api_url, timeout=90) as client:
        client.get("/healthz").raise_for_status()
        client.get(f"{ui_url}/_stcore/health").raise_for_status()

        def analyze(question: str) -> Analysis:
            response = client.post("/v1/analyze", json={"question": question}).raise_for_status()
            return Analysis.model_validate(response.json())

        for case in CASES:
            analysis = analyze(case.question)
            check_case(case, analysis)
            elapsed.append(analysis.latency_ms)
            print(f"{case.question} | {analysis.latency_ms:.0f} ms", flush=True)

        for question, expected in BOUNDARIES.items():
            analysis = analyze(question)
            assert analysis.status == expected, (question, analysis)
            assert analysis.sql is None and analysis.result is None and not analysis.valid_sql
            assert [stage.name for stage in analysis.trace] == ["plan"]
            assert analysis.usage.input_tokens == 0
        assert client.post("/v1/retail", json={"metric": "orders"}).status_code == 404

    check_ui(api_url)
    print(f"{len(CASES)} analyses exactes, {len(BOUNDARIES)} cas limites, Streamlit OK.")
    print(f"Latence médiane {median(elapsed):.0f} ms, max {max(elapsed):.0f} ms.")


if __name__ == "__main__":
    main()
