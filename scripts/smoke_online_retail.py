"""Check all complete months against the independent PostgreSQL reference."""

import os
from pathlib import Path
from statistics import median

import httpx
from streamlit.testing.v1 import AppTest

from analytics_agent.online_retail import EXAMPLES

REFERENCE = {
    "2010-12": ("517.12", 1447),
    "2011-01": ("578.05", 971),
    "2011-02": ("432.19", 995),
    "2011-03": ("448.19", 1308),
    "2011-04": ("413.29", 1153),
    "2011-05": ("419.77", 1523),
    "2011-06": ("447.95", 1383),
    "2011-07": ("450.26", 1337),
    "2011-08": ("502.52", 1210),
    "2011-09": ("545.34", 1645),
    "2011-10": ("518.48", 1811),
    "2011-11": ("532.27", 2490),
}
BOUNDARIES = {
    "Quel est le panier moyen au Royaume-Uni ?": "clarify",
    "Quel est le panier moyen au Royaume-Uni en décembre 2011 ?": "clarify",
    "Compare le panier moyen au Royaume-Uni en octobre et novembre 2011.": "clarify",
    "Calcule le panier moyen au Royaume-Uni pour 2011-11 uniquement le dimanche.": "clarify",
    "Supprime les factures annulées en novembre 2011.": "refused",
}


def main() -> None:
    api_url = os.environ.get("ANALYTICS_API_URL", "http://127.0.0.1:8072")
    ui_url = os.environ.get("ANALYTICS_UI_URL", "http://127.0.0.1:8572")
    with httpx.Client(base_url=api_url, timeout=90) as client:
        client.get("/healthz").raise_for_status()
        client.get(f"{ui_url}/_stcore/health").raise_for_status()

        def analyze(question):
            return client.post("/v1/analyze", json={"question": question}).raise_for_status().json()

        elapsed = []
        cases = [
            (month, f"Calcule le panier moyen au Royaume-Uni pour {month}.") for month in REFERENCE
        ]
        cases.append(("2011-11", EXAMPLES[0]))
        for month, question in cases:
            result = analyze(question)
            assert result["status"] == "answered", (question, result)
            assert result["online_retail_scope"] == {"month": month, "country": "United Kingdom"}
            assert result["result"]["rows"] == [list(REFERENCE[month])], result
            assert result["valid_sql"] and result["attempts"] == 1, result
            assert result["usage"]["input_tokens"] > 0 and result["usage"]["cost_usd"] == 0, result
            assert [stage["name"] for stage in result["trace"]] == [
                "plan",
                "validate",
                "execute",
                "summarize",
            ], result
            elapsed.append(result["latency_ms"])
            print(f"{month}: {result['result']['rows'][0]}, {elapsed[-1]:.0f} ms", flush=True)

        for question, expected in BOUNDARIES.items():
            result = analyze(question)
            assert result["status"] == expected, (question, result)
            assert result["sql"] is None and result["result"] is None, result
            assert [stage["name"] for stage in result["trace"]] == ["plan"], result
        assert client.post("/v1/retail", json={"metric": "orders"}).status_code == 404

    os.environ["ANALYTICS_DATASET"] = "online_retail"
    os.environ["ANALYTICS_API_URL"] = api_url
    ui_path = Path(__file__).resolve().parents[1] / "src/analytics_agent/ui.py"
    app = AppTest.from_file(str(ui_path), default_timeout=90).run()
    app.text_area[0].set_value(EXAMPLES[0])
    app.button[0].click().run()
    assert not app.exception and not app.error, (app.exception, app.error)
    assert any("532,27 £ sur 2490 factures" in item.value for item in app.text)
    assert "2011-11" in " ".join(item.value for item in app.caption)
    assert app.code and "invoice_lines" in app.code[0].value
    print(f"{len(cases)} analyses exactes, {len(BOUNDARIES)} cas limites, Streamlit OK.")
    print(f"Latence médiane {median(elapsed):.0f} ms, max {max(elapsed):.0f} ms.")


if __name__ == "__main__":
    main()
