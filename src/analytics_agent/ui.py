import os

import altair as alt
import httpx
import streamlit as st

from analytics_agent.models import Analysis

EXAMPLES = (
    "Quel est le chiffre d’affaires net par région ?",
    "Quel est le chiffre d’affaires net par mois ?",
    "Quel est le panier moyen ?",
    "Quel est le nombre de commandes terminées ?",
)


def request_analysis(question: str) -> Analysis:
    base_url = os.environ.get("ANALYTICS_API_URL", "http://127.0.0.1:8072")
    with httpx.Client(base_url=base_url, timeout=420) as client:
        response = client.post("/v1/analyze", json={"question": question})
        response.raise_for_status()
        return Analysis.model_validate(response.json())


def display_analysis(analysis: Analysis) -> None:
    st.subheader("Résultat")
    if analysis.status == "refused":
        st.warning(analysis.answer)
    elif analysis.status == "clarify":
        st.info(analysis.answer)
    elif analysis.status == "error":
        st.error(analysis.answer)
    else:
        st.text(analysis.answer)
    result, chart = analysis.result, analysis.chart
    if result and result.rows:
        st.dataframe(
            [dict(zip(result.columns, row, strict=True)) for row in result.rows],
            hide_index=True,
        )
        if chart:
            x, y = result.columns.index(chart.x), result.columns.index(chart.y)
            data = alt.Data(values=[{"x": row[x], "y": float(row[y])} for row in result.rows])
            plot = alt.Chart(data).encode(
                x=alt.X("x:N", title=chart.x, sort=None if chart.kind == "bar" else "ascending"),
                y=alt.Y("y:Q", title=chart.y),
                tooltip=[alt.Tooltip("x:N", title=chart.x), alt.Tooltip("y:Q", title=chart.y)],
            )
            mark = plot.mark_line(point=True) if chart.kind == "line" else plot.mark_bar()
            st.altair_chart(mark)
    with st.expander("Vérifier la requête et les étapes"):
        if analysis.sql:
            st.code(analysis.sql, language="sql")
        if analysis.sources:
            st.text("Références du catalogue : " + ", ".join(analysis.sources))
        st.dataframe([stage.model_dump() for stage in analysis.trace], hide_index=True)
        st.caption(
            f"{analysis.latency_ms:.0f} ms · {analysis.corrections} correction(s) · "
            f"Requête {analysis.request_id}"
        )


def main() -> None:
    st.set_page_config(page_title="Enterprise Agentic Analytics", layout="wide")
    st.title("Enterprise Agentic Analytics")
    st.caption("Interroge les ventes et vérifie le SQL à l’origine de chaque résultat.")
    example = st.selectbox("Exemples", ("", *EXAMPLES), format_func=lambda s: s or "Choisir…")
    if st.session_state.get("last_example") != example:
        st.session_state.last_example = example
        if example:
            st.session_state.question = example
    with st.form("analysis"):
        question = st.text_area("Question métier", key="question", max_chars=1500)
        submitted = st.form_submit_button("Analyser", type="primary")
    if submitted:
        st.session_state.pop("analysis_result", None)
        if len(question.strip()) < 3:
            st.warning("Saisis une question d’au moins trois caractères.")
        else:
            try:
                with st.spinner("Analyse en cours…"):
                    st.session_state.analysis_result = request_analysis(question.strip())
            except (httpx.HTTPError, ValueError):
                st.error("L’API d’analyse est indisponible ou a renvoyé une réponse inattendue.")
    if "analysis_result" in st.session_state:
        display_analysis(st.session_state.analysis_result)


if __name__ == "__main__":
    main()
