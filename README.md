# Enterprise Agentic Analytics

AI-2 permet d’explorer des ventes en français et de vérifier les chiffres affichés dans PostgreSQL. Le parcours Online Retail fonctionne sur des données réelles, avec un modèle local et sans clé API.

## Online Retail

Dans Streamlit, sélectionne un exemple ou saisis ta question. Le périmètre couvre les ventes au Royaume-Uni pendant les douze mois complets de décembre 2010 à novembre 2011. Tu peux demander un total sur une plage de mois ou une évolution mois par mois. [Interface Streamlit](http://127.0.0.1:8572)

| Question | Résultat vérifié |
|---|---|
| Quel était le panier moyen en novembre 2011 ? | 532,27 £ sur 2 490 factures |
| Quel était le chiffre d’affaires en novembre 2011 ? | 1 325 346,21 £ |
| Combien de commandes en octobre 2011 ? | 1 811 factures |
| Montre l’évolution du chiffre d’affaires de décembre 2010 à novembre 2011. | Courbe de 12 mois, avec un maximum en novembre 2011 |

Indique un mois et son année, ou une plage comme « de janvier à mars 2011 ». Le format `AAAA-MM` fonctionne aussi. Les demandes portant sur d’autres pays, des jours précis, des périodes incomplètes ou plusieurs indicateurs nécessitent une reformulation.

1. Qwen3 4B appelle un outil structuré avec l’indicateur et les bornes de la période.
2. LangGraph vérifie que l’interprétation correspond à la question et compile le SQL à partir de formules prédéfinies. SQLGlot contrôle la requête puis PostgreSQL l’exécute avec le rôle de lecture.
3. Streamlit affiche la réponse, le tableau, le graphique pour les séries, la définition métier, la source, le SQL et les étapes d’exécution.

Le chiffre d’affaires est la somme des quantités multipliées par les prix unitaires. Les commandes comptent les factures distinctes avec au moins une ligne éligible. Le panier moyen divise le chiffre d’affaires par ce nombre de factures. Une ligne est éligible si sa facture ne commence pas par `C` et si sa quantité et son prix sont strictement positifs. Les montants sont en livres sterling. Décembre 2011 est partiel et sort du périmètre. L’import conserve les 541 909 lignes, avec les cinq colonnes utiles au calcul. Données de Daqing Chen (2015), CC BY 4.0. [UCI Online Retail](https://doi.org/10.24432/C5BW33)

### Démarrer localement

Depuis la racine du dépôt, avec Python 3.12 et Docker Desktop démarré, exécute ces commandes PowerShell. Si `docker` est déjà dans le PATH, il remplace le script `./scripts/docker.ps1`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[import-retail,ui]"
curl.exe -fL --create-dirs -o data/benchmarks/online-retail/uci.zip "https://archive.ics.uci.edu/static/public/352/online+retail.zip"
./scripts/docker.ps1 compose up -d --wait db
.\.venv\Scripts\python.exe scripts/import_online_retail.py data/benchmarks/online-retail/uci.zip
./scripts/docker.ps1 compose -f compose.yaml -f compose.online-retail.yaml up -d --build --wait --wait-timeout 360
```

Le premier démarrage télécharge environ 2,4 Gio de poids. Le volume Docker conserve ces poids pour les relances. Après ce téléchargement, `$env:ANALYTICS_MODEL_OFFLINE = '1'` permet de démarrer le modèle à partir du cache. Les appels métier restent dans Docker et ne nécessitent ni clé API ni service payant.

### Reproduire la vérification

```powershell
./scripts/docker.ps1 compose exec -T db psql -X -U analytics_reader -d analytics -c (Get-Content -Raw scripts/online_retail_reference.sql)
.\.venv\Scripts\python.exe scripts/smoke_online_retail.py
```

La référence SQL agrège d’abord les lignes de chaque facture. Le test de bout en bout vérifie les trois indicateurs, les courbes mensuelles, la clarification des questions incomplètes et le refus des opérations non autorisées. Il mesure aussi la latence et le coût de l’API.

Sur le poste de test, les 23 analyses ont donné les résultats attendus. Les 7 demandes à clarifier et les 3 demandes refusées se sont arrêtées avant tout SQL. Les tests Streamlit ont vérifié l’affichage d’un total, d’un graphique et d’un refus. La latence médiane des analyses était de 8,0 s, avec un maximum de 9,8 s. Le coût d’API était nul.

## Démo sur données fictives

```powershell
docker compose up --build --wait -d
```

Sous Windows, utilise `scripts\docker.ps1` à la place de `docker` si Docker Desktop est absent du PATH.

- Interface Streamlit sur [localhost:8572](http://127.0.0.1:8572).
- Documentation interactive de l’API sur [localhost:8072/docs](http://127.0.0.1:8072/docs).
- Essaie « Donne-moi le CA du Nord en février 2025. » Le résultat attendu est **150 EUR HT**.
- Essaie « Combien de commandes ont été annulées en janvier 2025 ? » Le résultat attendu est **1 commande**.
- L’onglet **Utiliser le formulaire** permet de saisir les mêmes critères directement si une formulation nécessite une précision. Le résultat affiche les critères retenus, les valeurs, le graphique si applicable et le SQL de vérification.

Arrêter la démo avec `docker compose stop`. Les services HTTP sont liés à la boucle locale et PostgreSQL reste sur le réseau Compose. Les comptes et mots de passe fournis par défaut sont réservés à la base de démonstration.

## Périmètre métier de la démo fictive

| Indicateur | Calcul |
|---|---|
| Chiffre d’affaires net HT | Somme des montants des commandes terminées, en euros. Zéro si aucune commande ne correspond à un total demandé. |
| Nombre de commandes | Nombre de commandes de tous statuts. Un filtre ou un regroupement par statut peut être appliqué. |
| Panier moyen net HT | Montant moyen des commandes terminées, en euros. Le calcul est indisponible quand aucune commande terminée ne correspond. |

Tu peux filtrer sur le Nord, le Sud ou l’Ouest et demander un résultat par mois ou par région. Le nombre de commandes accepte aussi un regroupement par statut. Les dates de début et de fin sont incluses. Le champ texte comprend notamment une année, un mois avec son année, `2025-02` et des dates ISO comme « du 2025-02-03 au 2025-02-10 ». Utilise le formulaire pour une borne seule. Les périodes relatives, comparaisons et autres dimensions demandent une reformulation. Une période sans commandes produit un total nul ou un résultat groupé vide selon la question.

Le champ texte sait aussi lister les régions sans commande d’un statut sur une période donnée. Exemple de question « Liste les régions sans commande en attente en février 2025. »

## Du besoin au résultat, étape par étape

1. Une question écrite ou le formulaire fournit un indicateur, des filtres et un éventuel regroupement. Une demande ambiguë reçoit une clarification avant toute requête.
2. Le calcul compile ces critères en SQL à partir des définitions métier. Le chiffre est recalculé à partir des lignes présentes dans la base à chaque demande.
3. La politique SQL et le rôle de lecture PostgreSQL limitent les requêtes aux données autorisées. SQLite offre le même parcours pour les tests locaux.
4. Le tableau et la synthèse proviennent du même résultat que le graphique. Les critères, le SQL et les sources utilisées sont accessibles pour vérifier les chiffres.

L’API accepte une question sur `POST /v1/analyze` ou des critères structurés sur `POST /v1/retail`. Ces deux routes utilisent le même calcul en mode par défaut (`ANALYTICS_MODE=demo`).

## Mode LLM expérimental

La génération SQL libre et BIRD sont des expériences séparées. Le parcours métier décrit ci-dessus utilise son catalogue et ses règles de calcul directement. Les essais BIRD n’ont pas validé la qualité du mode LLM. Le formulaire reste disponible dans les deux modes.

Copie `.env.example` vers `.env` et configure `ANALYTICS_MODE=llm`, `ANALYTICS_LLM_MODEL` et `OPENAI_API_KEY`, ou indique un `ANALYTICS_LLM_BASE_URL` compatible Chat Completions et tool calling. Pour un serveur local sur Windows avec Docker Desktop, utilise `http://host.docker.internal:<port>/v1` depuis Compose et `http://127.0.0.1:<port>/v1` hors conteneur. Lance de nouveau `docker compose up --build --wait -d`. Le fournisseur du LLM reçoit la question et le contexte du catalogue, avec la proposition SQL précédente lors d’une correction. La synthèse locale utilise les lignes SQL.

Le réglage `ANALYTICS_LLM_OUTPUT=sql` permet d’utiliser un modèle spécialisé qui renvoie du SQL directement, comme XiYanSQL. Ses requêtes passent par le même validateur et le même rôle en lecture seule. Le [rapport de validation](docs/validation.md#essai-avec-xiyansql-7b) contient la commande de démarrage locale et les résultats de cet essai. Le réglage par défaut `tool` reste adapté aux modèles qui appellent des outils.

Les tarifs par million de tokens sont configurables. Le coût LLM est estimé à partir des tarifs et de l’usage communiqué par le fournisseur. Une estimation indisponible apparaît comme `null`. Le mode démo affiche un coût fournisseur de zéro. Pour LangSmith, renseigne `ANALYTICS_LANGSMITH=true` et `LANGSMITH_API_KEY`. Les traces envoyées contiennent des métriques et des codes d’étape, sans questions, SQL ni lignes de résultat. [Instrumentation LangSmith](https://docs.langchain.com/langsmith/annotate-code)

Le périmètre démo repose sur une base dédiée et des comptes de service. Un déploiement partagé demanderait une authentification, l’affectation des droits aux utilisateurs, et la définition des vues et colonnes autorisées.

## Tests et benchmarks

```powershell
python -m pip install -e '.[ui,dev]'
python -m pytest -q
python -m ruff check .
analytics-eval --output artifacts/demo.json demo
python scripts/smoke_compose.py
```

Sans Docker, `python -m uvicorn analytics_agent.api:app --port 8072` lance l’API sur une copie SQLite temporaire du jeu de démonstration. `python -m streamlit run src/analytics_agent/ui.py --server.port 8572` lance l’interface.

Pour BIRD, place les annotations SQLite Mini-Dev (`.jsonl` ou tableau JSON) dans `data/benchmarks`. Les bases peuvent être chargées depuis le [paquet Dev de BIRD](https://bird-bench.github.io/) et extraites une par une avec le script fourni. Les versions des annotations et des bases doivent correspondre au protocole que tu souhaites reproduire. [BIRD Mini-Dev](https://huggingface.co/datasets/birdsql/bird_mini_dev)

```powershell
python scripts/extract_bird.py data/benchmarks/dev.zip california_schools
analytics-eval --output artifacts/bird-references.json bird --dataset data/benchmarks/mini_dev_sqlite.jsonl --db-root data/benchmarks/dev_databases --db-id california_schools --limit 10 --reference-check
analytics-eval --output artifacts/bird-llm.json bird --dataset data/benchmarks/mini_dev_sqlite.jsonl --db-root data/benchmarks/dev_databases --db-id california_schools --limit 10
```

La deuxième commande vérifie les références seules. La troisième utilise le planificateur LLM. Les lignes sont comparées en conservant les doublons, dans l’ordre des colonnes, avec `--ordered` pour imposer aussi l’ordre des lignes. Une référence indisponible ou tronquée est comptabilisée séparément. Une prédiction tronquée est incorrecte. Cette comparaison locale fournit une mesure d’exactitude d’exécution pour le projet. Le protocole officiel BIRD et les métriques R-VES et Soft F1 sont documentés par le benchmark. [Évaluation BIRD Mini-Dev](https://github.com/bird-bench/mini_dev#evaluation)

Le moteur SQLite et le comparateur constituent une base pour une extension au sous-ensemble SQLite de Spider 2.0-Lite. Cette extension demanderait un chargeur pour ses métadonnées et ses sorties de référence. [Spider 2.0-Lite](https://spider2-sql.github.io/)

Le [rapport de validation](docs/validation.md) présente les résultats de la démo et le protocole de l’essai BIRD.
