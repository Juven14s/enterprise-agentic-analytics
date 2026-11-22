import re
from calendar import monthrange
from datetime import date

from pydantic import ValidationError

from analytics_agent.catalog import normalize
from analytics_agent.models import RetailQuery
from analytics_agent.policy import Dialect

METRIC_LABELS = {
    "revenue": "Chiffre d’affaires net HT",
    "orders": "Nombre de commandes",
    "average_order_value": "Panier moyen net HT",
}
GROUP_LABELS = {"region": "région", "month": "mois", "status": "statut"}
STATUS_LABELS = {"completed": "Terminée", "cancelled": "Annulée", "pending": "En attente"}

DENIED = re.compile(
    r"\b(supprime\w*|efface\w*|modifie\w*|insere\w*|ajoute\w*|delete|drop|truncate|"
    r"update|insert|alter|grant|revoke|mots? de passe|passwords?|secrets?|e-?mails?|courriels?)\b"
)
METRICS = {
    "revenue": r"\b(chiffre d'affaires(?: net)?(?: ht)?|ca(?: net)?(?: ht)?|net revenue)\b",
    "orders": r"\b((?:nombre|volume)(?: total)? (?:de|des) commandes|combien de commandes|"
    r"commandes|orders|order count)\b",
    "average_order_value": r"\b(panier moyen(?: net)?(?: ht)?|average order value)\b",
}
GROUPS = {
    "region": r"\b((?:par|selon la) regions?|by region)\b",
    "month": r"\b(par mois|mensuell?e?s?|by month)\b",
    "status": r"\b(par statuts?|by status)\b",
}
REGIONS = {name: rf"\b(?:region )?{name.lower()}\b" for name in ("Nord", "Sud", "Ouest")}
STATUSES = {
    "completed": r"\b(termine[es]*|complete[es]*|finalise[es]*|completed)\b",
    "cancelled": r"\b(annule[es]*|cancelled)\b",
    "pending": r"\b(en attente|pending)\b",
    "all": r"\b(tous (?:les )?statuts(?: confondus)?)\b",
}
FILLER = frozenset(
    """
    a affiche affichez analyse analysez au aux avec avoir calcule calculez cela ce ces chiffres
    combien d dans de des donne donnez du en est et ete eu evolue evolution fait fais il indique
    etait indiquez je l la le les m me moi montre montrez notre nous ont obtenir observe pour
    presente presentez peux peut quel quelle quelles quels releve repartition s sont svp t te
    total toutes tous tout un une veuillez voir voudrais y show what is the please in of all
""".split()
)

MONTHS = (
    "janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre"
).split()
MONTH = "|".join(MONTHS)
DAY_ISO = r"\d{4}-\d{2}-\d{2}"
PERIOD_PATTERNS = (
    ("range", re.compile(rf"\b(?:du|entre(?: le)?) ({DAY_ISO}) (?:au|et(?: le)?) ({DAY_ISO})\b")),
    ("months", re.compile(rf"\b(?:de|du) ({MONTH}) (?:a|au) ({MONTH}) (\d{{4}})\b")),
    ("day", re.compile(rf"\b({DAY_ISO})\b")),
    ("month_iso", re.compile(r"\b(\d{4})-(\d{2})\b")),
    ("month_named", re.compile(rf"\b({MONTH})(?: (\d{{4}}))?\b")),
    ("year", re.compile(r"\b((?:19|20)\d{2})\b")),
)


class Clarification(ValueError):
    """The requested interpretation cannot be determined safely."""


def _take(text, patterns, failure):
    matches = [key for key, pattern in patterns.items() if re.search(pattern, text)]
    if len(matches) > 1:
        raise Clarification(failure)
    if not matches:
        return None, text
    [key] = matches
    return key, re.sub(patterns[key], " ", text)


def _month_dates(year: int, first: int, last: int) -> tuple[date, date]:
    start = date(year, first, 1)
    end = date(year, last, monthrange(year, last)[1])
    if start > end:
        raise ValueError("inverted month range")
    return start, end


def _period(text: str):
    for kind, pattern in PERIOD_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        if kind == "month_named" and not match[2]:
            raise Clarification(f"De quelle année parles-tu pour {match[1]} ?")
        try:
            if kind == "range":
                dates = tuple(date.fromisoformat(value) for value in match.groups())
                if dates[0] > dates[1]:
                    raise ValueError("inverted date range")
            elif kind == "day":
                dates = (date.fromisoformat(match[1]),) * 2
            elif kind == "months":
                dates = _month_dates(
                    int(match[3]), MONTHS.index(match[1]) + 1, MONTHS.index(match[2]) + 1
                )
            elif kind == "month_named":
                month = MONTHS.index(match[1]) + 1
                dates = _month_dates(int(match[2]), month, month)
            elif kind == "month_iso":
                dates = _month_dates(int(match[1]), int(match[2]), int(match[2]))
            else:
                dates = _month_dates(int(match[1]), 1, 12)
        except ValueError as error:
            raise Clarification(
                "Vérifie les dates et indique une période dans l’ordre chronologique."
            ) from error
        rest = text[: match.start()] + " " + text[match.end() :]
        return dates, rest
    return (None, None), text


def parse_question(question: str) -> RetailQuery:
    text = normalize(re.sub(r"\bça\b", "cela", question, flags=re.IGNORECASE).replace("’", "'"))
    text = re.sub(r"[,!?]", " ", text).strip().rstrip(".")
    text = re.sub(r"\s+", " ", text)
    (date_from, date_to), text = _period(text)
    status, text = _take(
        text, STATUSES, "Indique un seul statut ou groupe les commandes par statut."
    )
    group, text = _take(text, GROUPS, "Choisis un seul regroupement : région, mois ou statut.")
    region, text = _take(
        text, REGIONS, "Précise une seule région ou demande un résultat par région."
    )
    metric, text = _take(
        text, METRICS, "Pose une question par indicateur pour obtenir un résultat."
    )
    if not metric:
        raise Clarification(
            "Quel indicateur veux-tu voir : chiffre d’affaires, commandes ou panier moyen ?"
        )
    if re.search(r"\b(evolue|evolution)\b", text) and group != "month":
        raise Clarification(
            "Pour une évolution, demande le résultat par mois et indique la période."
        )
    if "repartition" in text.split() and not group:
        raise Clarification(
            "Pour répartir le résultat, précise un regroupement : région, mois ou statut."
        )
    if set(re.split(r"[\s'-]+", text.strip())) - FILLER - {""}:
        raise Clarification(
            "Un élément de la question reste à préciser. Indique l’indicateur, la région "
            "et la période qui t’intéressent, ou utilise le formulaire."
        )
    if status == "all" and metric != "orders":
        raise Clarification(
            "Le chiffre d’affaires et le panier moyen portent sur les commandes terminées."
        )
    try:
        return RetailQuery(
            metric=metric,
            group_by=group,
            region=region,
            status=None if status == "all" else status,
            date_from=date_from,
            date_to=date_to,
        )
    except ValidationError as error:
        raise Clarification(
            "Choisis le nombre de commandes pour filtrer ou regrouper par statut."
        ) from error


def compile_query(query: RetailQuery, dialect: Dialect) -> str:
    count_alias = "commandes_terminees" if query.status == "completed" else "commandes"
    aggregates = {
        "revenue": "COALESCE(ROUND(SUM(o.net_amount_cents) / 100.0, 2), 0.00) "
        "AS chiffre_affaires_eur",
        "orders": f"COUNT(*) AS {count_alias}",
        "average_order_value": "ROUND(AVG(o.net_amount_cents) / 100.0, 2) AS panier_moyen_eur",
    }
    month = (
        "TO_CHAR(DATE_TRUNC('month', o.order_date), 'YYYY-MM')"
        if dialect == "postgres"
        else "STRFTIME('%Y-%m', o.order_date)"
    )
    dimensions = {
        "region": "c.region AS region",
        "month": f"{month} AS mois",
        "status": (
            "CASE o.status WHEN 'completed' THEN 'Terminée' "
            "WHEN 'cancelled' THEN 'Annulée' ELSE 'En attente' END AS statut"
        ),
    }
    base = "FROM orders AS o"
    if query.region or query.group_by == "region":
        base += " JOIN customers AS c ON o.customer_id = c.customer_id"
    filters = []
    if query.effective_status:
        filters.append(f"o.status = '{query.effective_status}'")
    if query.region:
        filters.append(f"c.region = '{query.region}'")
    if query.date_from:
        filters.append(f"o.order_date >= '{query.date_from.isoformat()}'")
    if query.date_to:
        filters.append(f"o.order_date <= '{query.date_to.isoformat()}'")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    dimension = dimensions[query.group_by] + ", " if query.group_by else ""
    grouping = " GROUP BY 1 ORDER BY 1" if query.group_by else ""
    return f"SELECT {dimension}{aggregates[query.metric]} {base}{where}{grouping}"


def retail_sources(query: RetailQuery) -> list[str]:
    metric = {
        "revenue": "net_revenue",
        "orders": "completed_orders" if query.status == "completed" else "order_count",
        "average_order_value": "average_order_value",
    }[query.metric]
    sources = [f"metric:{metric}", "table:orders"]
    if query.region or query.group_by == "region":
        sources.extend(["table:customers", "relation:orders_customer"])
    return sources
