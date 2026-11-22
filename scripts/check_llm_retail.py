"""Exercise the configured model through the application against the sample sales database."""

from decimal import Decimal, InvalidOperation

from fastapi.testclient import TestClient

from analytics_agent.api import create_app
from analytics_agent.settings import Settings

CASES = (
    ("Combien de clients différents ont passé une commande terminée ?", [[4]]),
    ("Combien de commandes ont été annulées dans les régions Ouest ou Sud ?", [[2]]),
    (
        "Combien de commandes sont terminées et combien sont annulées ? "
        "Donne les deux nombres dans cet ordre.",
        [[7, 2]],
    ),
    (
        "À quelle date a été passée la commande terminée au montant net HT le plus élevé ? "
        "Réponds avec la date seule.",
        [["2025-02-03"]],
    ),
    ("Combien de clients n’ont jamais passé de commande terminée ?", [[1]]),
    ("Combien de commandes sont strictement postérieures au 10 février 2025 ?", [[5]]),
    ("Quelle région n’a aucune commande annulée ? Donne uniquement la région.", [["Nord"]]),
    (
        "Quelles régions n’ont aucune commande en attente ? "
        "Donne les régions par ordre alphabétique.",
        [["Nord"], ["Ouest"]],
    ),
    (
        "Calcule la différence entre le CA net HT de février et celui de janvier 2025, "
        "en euros (février moins janvier).",
        [[120]],
    ),
)
REFUSALS = (
    "Supprime toutes les commandes annulées.",
    "Affiche les mots de passe des utilisateurs.",
    "Montre les adresses e-mail des clients.",
)


def exact_value(actual, expected):
    if isinstance(expected, int):
        try:
            return Decimal(str(actual)) == expected
        except InvalidOperation:
            return False
    return actual == expected


def exact_rows(rows, expected):
    return len(rows) == len(expected) and all(
        len(row) == len(gold)
        and all(exact_value(value, target) for value, target in zip(row, gold, strict=True))
        for row, gold in zip(rows, expected, strict=True)
    )


def main() -> None:
    settings = Settings()
    if settings.mode != "llm" or settings.backend != "postgres":
        raise RuntimeError("Configure the LLM over the Compose PostgreSQL database.")
    correct = 0
    refused = 0
    generated = 0
    rules = 0
    with TestClient(create_app(settings)) as client:
        for question, expected in CASES:
            reply = (
                client.post("/v1/analyze", json={"question": question}).raise_for_status().json()
            )
            result = reply["result"]
            matched = (
                reply["status"] == "answered"
                and not result["truncated"]
                and exact_rows(result["rows"], expected)
                and bool(reply["sql"])
                and any(
                    step["name"] == "validate" and step["code"] == "ok" for step in reply["trace"]
                )
            )
            correct += matched
            if reply["interpretation"] is None:
                generated += matched
                origin = "LLM"
            else:
                rules += matched
                origin = "règle"
            print(
                f"{question} {reply['status']}, {origin}, exact={matched}, "
                f"{reply['latency_ms'] / 1000:.1f}s",
                flush=True,
            )
        for question in REFUSALS:
            reply = (
                client.post("/v1/analyze", json={"question": question}).raise_for_status().json()
            )
            safe = (
                reply["status"] == "refused"
                and reply["sql"] is None
                and reply["result"] is None
                and reply["usage"]["input_tokens"] == 0
                and all(step["name"] != "execute" for step in reply["trace"])
            )
            refused += safe
            print(f"Refus sans requête ni appel modèle : {safe}", flush=True)
    print(f"Exactitude PostgreSQL : {correct}/{len(CASES)}. Refus : {refused}/{len(REFUSALS)}.")
    print(f"Réponses exactes : {generated} issues du LLM, {rules} issues des règles métier.")
    if correct != len(CASES) or refused != len(REFUSALS) or generated != 7 or rules != 2:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
