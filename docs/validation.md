# Validation locale

## Démo retail

La suite contient cinq questions analytiques, trois demandes hors périmètre et une question ambiguë. Elle compare les valeurs renvoyées à des résultats de référence établis à partir du jeu de données fictif. Les tests vérifient aussi le parcours HTTP, la qualification des tables, la troncature, la correction d’une colonne inconnue et l’arrêt après épuisement du budget de corrections.

| Mesure locale du 22 novembre 2026 | PostgreSQL dans Docker | SQLite |
|---|---:|---:|
| Résultats analytiques exacts | 5/5 | 5/5 |
| Demandes hors périmètre refusées | 3/3 | 3/3 |
| Clarifications attendues | 1/1 | 1/1 |
| Latence moyenne / p95 | 21,9 / 42,0 ms | 8,2 / 25,9 ms |
| Coût API moyen | 0 USD | 0 USD |

Les 23 tests Python sont passés. Le test Docker a vérifié l’analyse et le refus via FastAPI, l’affichage du résultat dans Streamlit et les privilèges du rôle PostgreSQL. Ces durées proviennent d’une seule exécution locale de neuf cas. Lors d’un essai LLM séparé sur SQLite, Qwen a aussi généré une requête qui a correctement compté les sept commandes terminées.

## Premier essai BIRD avec Qwen2.5 1,5B

- La base `california_schools` provient de l’archive `dev.zip` dont le SHA-256 est `cdd6d19faeb45a23970b98d3ef6c40a87987c95459c2cf12076897a60cf5a630`. [Archive Dev de BIRD](https://bird-bench.github.io/)
- Les dix premiers cas de cette base correspondent aux identifiants 0 à 9 des [annotations Mini-Dev SQLite, révision 38818fc](https://huggingface.co/datasets/birdsql/bird_mini_dev/blob/38818fc/data/mini_dev_sqlite.jsonl).
- Les dix références s’exécutent sous l’autorisateur SQLite. La version produite par le validateur SQLGlot renvoie les mêmes résultats sur ces dix cas.

Au premier passage, Qwen a donné 0/10 résultat exact. Deux requêtes ont franchi la validation et une a abouti. Six cas se sont terminés sur une erreur de syntaxe, deux sur un délai dépassé et un sur une colonne inconnue. La moyenne était de 1,4 correction et de 45,6 s par question, avec un p95 de 88,6 s. Le coût API estimé était nul pour les huit cas disposant d’une mesure complète.

Un diagnostic a montré que Qwen omettait les guillemets autour d’identifiants contenant des espaces et des signes de ponctuation. Le catalogue affiche désormais les identifiants SQL avec leurs guillemets, et le planificateur demande au modèle de les reprendre tels quels.

La contre-vérification sur les cas 0, 1 et 3 donne 0/3 résultat exact. Deux cas se terminent encore sur une erreur de syntaxe et un atteint le délai du modèle. La correction du catalogue n’a donc pas amélioré l’exactitude sur ces trois cas.

Les commandes suivantes reproduisent le protocole initial avec le code du commit `3e1899f`, Qwen2.5-1.5B-Instruct Q4_K_M et llama.cpp après téléchargement des données indiquées dans le README. [Modèle Qwen](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF) · [llama.cpp pour Docker](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md)

```powershell
docker run -d --name ai2-llama-benchmark -p 127.0.0.1:8772:8080 --mount source=ai2-llama-models,target=/root/.cache/llama.cpp ghcr.io/ggml-org/llama.cpp@sha256:ae28f5c2046de51dfd00c9116a36fe44b5e8703ef730d0aea69082781eecb1f7 -hf Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M --alias qwen2.5-1.5b --jinja --ctx-size 4096 --parallel 1 --threads 4 --host 0.0.0.0 --port 8080
curl.exe http://127.0.0.1:8772/health
$env:ANALYTICS_MODE = 'llm'
$env:ANALYTICS_LLM_MODEL = 'qwen2.5-1.5b'
$env:ANALYTICS_LLM_BASE_URL = 'http://127.0.0.1:8772/v1'
$env:ANALYTICS_LLM_TIMEOUT_SECONDS = '60'
$env:ANALYTICS_INPUT_USD_PER_MILLION = '0'
$env:ANALYTICS_OUTPUT_USD_PER_MILLION = '0'
analytics-eval --output artifacts/bird-qwen-local.json bird --dataset data/benchmarks/mini_dev_sqlite.jsonl --db-root data/benchmarks/dev_databases --db-id california_schools --limit 10
```

Attends que le serveur indique `status: ok` avant de lancer l’évaluation. Si le conteneur existe déjà, relance-le avec `docker start ai2-llama-benchmark`. Les indices textuels BIRD sont fournis au planificateur. L’ordre des lignes est libre et celui des colonnes doit correspondre. Ce sous-ensemble sur une seule base permet de vérifier la chaîne avec un modèle local. Une comparaison à un classement BIRD demanderait le split et le protocole officiels complets.

## Deuxième essai avec Qwen3 4B Instruct

Le planificateur envoie désormais `tool_choice=required` au serveur compatible OpenAI et vérifie qu’il reçoit un unique appel `SQLPlan`. Le prompt fournit la liste des colonnes séparément des descriptions pour préserver les identifiants quotés. Un dépassement de délai est comptabilisé séparément d’une panne du serveur.

Le protocole conserve les règles SQL, les indices textuels BIRD et la comparaison du premier essai. Il fixe une correction maximum et 75 secondes par appel. Les six cas 49, 50, 53, 295, 296 et 297 couvrent trois niveaux de difficulté sur les bases `card_games` et `student_club`. Ils ont été choisis d’après les identifiants et la difficulté, sans utiliser les résultats de référence pour modifier le planificateur. Les six références passent le contrôle d’exécution après validation. Les cas 0, 1 et 3 servent à observer les résultats sur le même échantillon que la contre-vérification précédente.

Sur les six nouveaux cas, Qwen3 a produit six requêtes validées et exécutables. Une seule a renvoyé le résultat exact (cas 296), soit **1/6**. La moyenne était de 0,17 correction et de 38,5 s par question avec un p95 de 58,3 s. Le coût API estimé était nul sur les six cas. L’exactitude et la latence mesurées sur cet échantillon restent insuffisantes pour considérer le Text-to-SQL validé.

Sur les trois cas rejoués, Qwen3 a produit trois requêtes exécutables, contre zéro lors de la contre-vérification Qwen2.5. L’exactitude reste de **0/3**. Les 27 tests Python et le test de bout en bout Docker sur PostgreSQL et Streamlit passent avec le nouveau planificateur.

Le modèle est Qwen3-4B-Instruct-2507, quantification Q4_K_M d’Unsloth. Le serveur llama.cpp utilise la même image que le premier essai, six threads CPU, une séquence à la fois et un contexte de 4 096 tokens. [Modèle Qwen3](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) · [Poids quantifiés](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) · [Appels d’outils llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

```powershell
python scripts/extract_bird.py data/benchmarks/dev.zip card_games
python scripts/extract_bird.py data/benchmarks/dev.zip student_club
docker run -d --name ai2-qwen4b-benchmark -p 127.0.0.1:8773:8080 --mount source=ai2-llama-models,target=/root/.cache/llama.cpp ghcr.io/ggml-org/llama.cpp@sha256:ae28f5c2046de51dfd00c9116a36fe44b5e8703ef730d0aea69082781eecb1f7 -hf unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M --alias qwen3-4b-instruct --jinja --ctx-size 4096 --parallel 1 --threads 6 --host 0.0.0.0 --port 8080
curl.exe http://127.0.0.1:8773/health
$env:ANALYTICS_MODE = 'llm'
$env:ANALYTICS_LLM_MODEL = 'qwen3-4b-instruct'
$env:ANALYTICS_LLM_BASE_URL = 'http://127.0.0.1:8773/v1'
$env:ANALYTICS_LLM_TIMEOUT_SECONDS = '75'
$env:ANALYTICS_MAX_CORRECTIONS = '1'
$env:ANALYTICS_INPUT_USD_PER_MILLION = '0'
$env:ANALYTICS_OUTPUT_USD_PER_MILLION = '0'
analytics-eval --output artifacts/bird-qwen4b-holdout.json bird --dataset data/benchmarks/mini_dev_sqlite.jsonl --db-root data/benchmarks/dev_databases --case-id 49 --case-id 50 --case-id 53 --case-id 295 --case-id 296 --case-id 297 --progress
```
