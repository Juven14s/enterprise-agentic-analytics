import os

import altair as alt
import httpx
import streamlit as st

from analytics_agent.models import Analysis, RetailQuery
from analytics_agent.online_retail import EXAMPLES as ONLINE_RETAIL_EXAMPLES
from analytics_agent.online_retail import METRIC_DEFINITION, SOURCE
from analytics_agent.retail import GROUP_LABELS, METRIC_LABELS, STATUS_LABELS

EXAMPLES = (
    "Quel est le chiffre d’affaires net par région ?",
    "Combien de commandes ont été annulées en janvier 2025 ?",
    "Montre le panier moyen dans le Nord en mars 2025.",
    "Quel est le chiffre d’affaires par mois de janvier à mars 2025 ?",
)


def request_analysis(question: str | RetailQuery) -> Analysis:
    base_url = os.environ.get("ANALYTICS_API_URL", "http://127.0.0.1:8072")
    with httpx.Client(base_url=base_url, timeout=420) as client:
        if isinstance(question, RetailQuery):
            response = client.post("/v1/retail", json=question.model_dump(mode="json"))
        else:
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
    if query := analysis.interpretation:
        label = (
            "Régions sans commande au statut indiqué"
            if query.absence
            else METRIC_LABELS[query.metric]
        )
        details = [label, query.region or "Toutes les régions"]
        details.append(STATUS_LABELS[query.status] if query.status else "Tous statuts")
        if query.date_from and query.date_to:
            details.append(f"Du {query.date_from:%d/%m/%Y} au {query.date_to:%d/%m/%Y} inclus")
        elif query.date_from:
            details.append(f"À partir du {query.date_from:%d/%m/%Y}")
        elif query.date_to:
            details.append(f"Jusqu’au {query.date_to:%d/%m/%Y} inclus")
        else:
            details.append("Toutes les dates disponibles")
        if query.group_by:
            details.append(f"Par {GROUP_LABELS[query.group_by]}")
        st.caption(" · ".join(details))
    if scope := analysis.online_retail_scope:
        st.caption(f"Royaume-Uni · {scope.month} · livres sterling")
        st.caption(METRIC_DEFINITION)
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
        if analysis.online_retail_scope:
            st.markdown(f"[Source des données · UCI Online Retail]({SOURCE})")
        elif analysis.sources:
            st.text("Références du catalogue : " + ", ".join(analysis.sources))
        st.dataframe([stage.model_dump() for stage in analysis.trace], hide_index=True)
        st.caption(
            f"{analysis.latency_ms:.0f} ms · {analysis.corrections} correction(s) · "
            f"Requête {analysis.request_id}"
        )


def question_form(examples: tuple[str, ...] = EXAMPLES) -> str | None:
    example = st.selectbox("Exemples", ("", *examples), format_func=lambda s: s or "Choisir…")
    if st.session_state.get("last_example") != example:
        st.session_state.last_example = example
        if example:
            st.session_state.question = example
    with st.form("analysis"):
        question = st.text_area("Question métier", key="question", max_chars=1500)
        submitted = st.form_submit_button("Analyser", type="primary")
    return question.strip() if submitted else None


def guided_form() -> RetailQuery | None:
    metric = st.selectbox("Indicateur", list(METRIC_LABELS), format_func=METRIC_LABELS.__getitem__)
    allowed_groups = [None, "region", "month"]
    if metric == "orders":
        allowed_groups.append("status")
    with st.form("guided"):
        first, second, third = st.columns(3)
        region = first.selectbox(
            "Région", [None, "Nord", "Sud", "Ouest"], format_func=lambda value: value or "Toutes"
        )
        group = second.selectbox(
            "Regroupement",
            allowed_groups,
            format_func=lambda value: GROUP_LABELS[value].capitalize() if value else "Aucun",
        )
        if metric == "orders":
            status = third.selectbox(
                "Statut",
                [None, *STATUS_LABELS],
                format_func=lambda value: STATUS_LABELS.get(value, "Tous"),
            )
        else:
            status = "completed"
            third.caption("Commandes terminées")
        start, end = st.columns(2)
        date_from = start.date_input("Date de début", value=None, format="DD/MM/YYYY")
        date_to = end.date_input("Date de fin", value=None, format="DD/MM/YYYY")
        submitted = st.form_submit_button("Calculer", type="primary")
    if not submitted:
        return None
    if date_from and date_to and date_from > date_to:
        st.session_state.pop("analysis_result", None)
        st.warning("La date de début doit précéder ou égaler la date de fin.")
        return None
    return RetailQuery(
        metric=metric,
        group_by=group,
        region=region,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )


def main() -> None:
    st.set_page_config(page_title="Analyse des ventes", layout="wide")
    st.title("Analyse des ventes")
    if os.environ.get("ANALYTICS_DATASET") == "online_retail":
        st.caption("Panier moyen mensuel au Royaume-Uni, de décembre 2010 à novembre 2011.")
        inquiry = question_form(ONLINE_RETAIL_EXAMPLES)
    else:
        st.caption(
            "Chiffre d’affaires, commandes et panier moyen sur des ventes fictives "
            "de janvier à mars 2025."
        )
        question_tab, guided_tab = st.tabs(["Poser une question", "Utiliser le formulaire"])
        with question_tab:
            question = question_form()
        with guided_tab:
            guided = guided_form()
        inquiry = question if question is not None else guided
    if inquiry is not None:
        st.session_state.pop("analysis_result", None)
        if isinstance(inquiry, str) and len(inquiry) < 3:
            st.warning("Saisis une question d’au moins trois caractères.")
        else:
            try:
                with st.spinner("Analyse en cours…"):
                    st.session_state.analysis_result = request_analysis(inquiry)
            except (httpx.HTTPError, ValueError):
                st.error("L’API d’analyse est indisponible ou a renvoyé une réponse inattendue.")
    if "analysis_result" in st.session_state:
        display_analysis(st.session_state.analysis_result)


if __name__ == "__main__":
    main()
