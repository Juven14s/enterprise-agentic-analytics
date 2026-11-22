"""Check the running PostgreSQL API and Streamlit services."""

import os
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest


def main() -> None:
    api_url = os.environ.get("ANALYTICS_API_URL", "http://127.0.0.1:8072")
    ui_url = os.environ.get("ANALYTICS_UI_URL", "http://127.0.0.1:8572")
    with httpx.Client(timeout=20) as client:
        health = client.get(f"{api_url}/healthz").raise_for_status().json()
        assert health == {"status": "ok", "mode": "demo", "database": "postgres"}, health
        response = (
            client.post(
                f"{api_url}/v1/analyze",
                json={
                    "question": "Quel est le chiffre d’affaires net par région ?",
                },
            )
            .raise_for_status()
            .json()
        )
        assert response["status"] == "answered", response
        assert response["result"]["rows"] == [
            ["Nord", "410.00"],
            ["Ouest", "155.00"],
            ["Sud", "190.00"],
        ], response
        assert response["chart"]["kind"] == "bar"
        assert 'FROM "analytics".orders' in response["sql"]
        refusal = (
            client.post(
                f"{api_url}/v1/analyze",
                json={
                    "question": "Supprime les commandes annulées",
                },
            )
            .raise_for_status()
            .json()
        )
        assert refusal["status"] == "refused" and refusal["sql"] is None, refusal
        ui = client.get(f"{ui_url}/_stcore/health").raise_for_status()
        assert ui.text == "ok", ui.text
    interface = Path(__file__).resolve().parents[1] / "src" / "analytics_agent" / "ui.py"
    app = AppTest.from_file(str(interface), default_timeout=20).run()
    app.selectbox[0].select("Quel est le chiffre d’affaires net par région ?").run()
    app.button[0].click().run()
    assert not app.exception and not app.error, (app.exception, app.error)
    assert any("410.00" in item.value for item in app.text)
    assert any("FROM" in item.value for item in app.code)
    print("PostgreSQL result, audited refusal and Streamlit workflow verified.")


if __name__ == "__main__":
    main()
