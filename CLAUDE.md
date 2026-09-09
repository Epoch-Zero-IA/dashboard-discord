# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Travaux en cours

La cible est un agent Discord de gestion de serveur, découpé en sept sous-projets
indépendants, chacun avec sa propre spec. **Le morceau A — le socle : worker Discord,
schéma Postgres, ingestion, rattrapage, jobs nocturnes — est écrit** (branche
`feature/core-schema`). Les morceaux B à G ne sont pas commencés.

La connexion gateway est vérifiée (2026-09-09), sur le serveur Epoch Zéro : `discord.py`
tourne sous CPython 3.14, le contrôle des intents passe sur la configuration réelle — où
Discord n'expose les deux intents privilégiés que sur ses drapeaux `_limited`, le cas que
le contrôle devait absolument accepter — et une page de cent messages a été lue et
convertie en records, contenu compris.

La couche SQL est exécutée depuis le 2026-09-09 : les deux migrations s'appliquent et
**les 130 tests passent**, tests marqués `db` compris, contre un PostgreSQL 16.2 réel.
Deux passes consécutives laissent toutes les tables à zéro ligne.

Deux réserves subsistent, mineures mais réelles : la vérification s'est faite sur
**Postgres 16**, alors que les composes déclarent 18 — la CI, elle, tourne bien sur 18 ;
et rien de tout cela ne teste le déploiement lui-même (images, entrypoint de migration,
healthchecks), qui n'a jamais été construit.

**Lire `docs/superpowers/specs/2026-09-08-bot-discord-socle-design.md` avant de toucher
au code.** Le document porte le découpage complet, les décisions déjà prises et leurs
raisons, et surtout ce qui n'est *pas* encore tranché. Ses quatre sections sont
rédigées.

`docs/superpowers/plans/2026-09-08-bot-discord-socle-plan.md` porte les huit étapes du
socle, et **chacune est suivie d'un bilan de ce que l'écriture a changé ou appris** :
les écarts au plan, les pièges rencontrés et ce qui n'a pas pu être vérifié. C'est là
qu'il faut lire avant de reprendre, plutôt que de redécouvrir.

Les décisions actées qui contraignent tout code écrit ici :

- Le worker Discord est un **troisième service** (`bot`), pas un `on_startup` de
  Litestar, et il tourne en **un seul réplica définitivement** — deux instances sur la
  même gateway traitent chaque message deux fois. C'est ce qui autorise le heartbeat et
  les jobs nocturnes à vivre dans ce process, sans verrou distribué.
- Le schéma appartient à `core/`, qui ne dépend **ni de Litestar ni de discord.py**.
  `backend/` et `bot/` en dépendent tous les deux ; `bot/` n'importe pas Litestar. Deux
  tests verrouillent ces frontières (`tests/test_package_boundaries.py`) : les violer ne
  casse rien au runtime, seulement les images.
- Postgres + Alembic ; identifiants Discord en `BIGINT` comme clés primaires, donc
  **toute écriture est un upsert** et rejouer un événement est indiscernable de le voir
  une fois. Le rattrapage rejoue des pages qui se chevauchent exprès.
- **Tout ce qui décide est une fonction pure ; tout ce qui parle à discord.py est
  mince.** `bot/adapters.py` convertit à la frontière, et rien en aval ne voit un
  `discord.Message`. C'est ce qui rend le worker testable sans simuler la bibliothèque.
- **Les fils sont des canaux.** Un `Thread` prend une ligne dans `channel`, avec
  `is_thread` et `parent_id`. Tout code qui filtre les canaux doit accepter
  `discord.Thread` autant que `discord.TextChannel` : ne garder que le second ingérait
  les fils en temps réel sans jamais les rattraper, et c'est passé inaperçu jusqu'à la
  première connexion réelle. Le rattrapage ne voit que les fils **actifs**.
- **Les tests tournent sur un vrai Postgres, jamais sur SQLite.** Deux étages : la
  logique d'ingestion en fonctions pures sur des dataclasses, sans base ; la couche SQL
  sur une instance réelle, une transaction annulée par test. `just test` saute l'étage 2
  faute de base, `just check` l'exige.
- Base injoignable = **fast fail** : le bot sort en code non nul, l'orchestrateur le
  redémarre, le rattrapage par curseurs recomble le trou. Pas de tampon en mémoire.
- `alembic upgrade head` à l'entrée de l'image `api`, avant Granian ; `bot` attend
  `api` sain. Un seul migrateur.
- La stack LLM sera LangChain + LiteLLM, mais aucun morceau LLM n'est spécifié à ce
  jour.

Ne partez pas de la demande initiale pour improviser : elle décrit six fonctionnalités
qui ont déjà été décomposées et ordonnancées dans la spec.

## Commandes

`just` est le point d'entrée unique (les hooks pre-commit et la CI appellent les mêmes
recettes). `just` seul liste tout. Il charge `.env` automatiquement (`set dotenv-load`),
d'où l'obligation de `cp .env.example .env` avant tout.

```bash
just install       # uv sync + litestar assets install (node_modules du frontend)
just db            # démarre le service Postgres seul — prérequis des tests marqués db
just dev           # API :8000 + Vite :5173 — se consulte sur http://127.0.0.1:5173
just dev-api       # API seule, reload limité à backend/
just dev-front     # Vite seul, HMR, sans backend
just dev-bot       # le worker Discord seul (exige DISCORD_TOKEN et les intents)
just dev-all       # les trois : api + vite + bot
just migrate       # alembic upgrade head
just migration m="…"  # alembic revision --autogenerate (exige une base à jour)
just backfill      # importe tout l'historique lisible, puis sort
just types         # exporte openapi.json et régénère frontend/src/generated/
just lint          # ruff format --check, ruff check, pyrefly, eslint, prettier, svelte-check
just format        # ruff format, ruff check --fix, prettier --write, eslint --fix
just test          # pytest — les tests marqués db sont sautés sans base
just test-with-db  # la même suite, base exigée (--require-db)
just check         # check-types + lint + test-with-db — la porte avant push, ce que lance la CI
```

`just dev` ne lance **pas** le bot : travailler sur le front n'exige pas de token et ne
doit pas ouvrir une vraie connexion gateway.

Un seul test : `uv run pytest tests/test_api.py::test_health_check`.

`litestar` se lance **toujours depuis la racine du dépôt** : depuis `frontend/`, le
package `backend` est introuvable. `LITESTAR_APP=backend.app:app` doit être dans
l'environnement (`.env` en local, `env:` du workflow en CI) — l'autodiscovery ne
cherche qu'à la racine.

Diagnostic de configuration : `uv run litestar assets doctor`.

## Architecture

**Quatre services, trois images, trois packages Python.** Backend et frontend sont
découplés : deux déploiements, deux dimensionnements, et pour contrat `openapi.json`,
versionné à la racine.

```
web   nginx      : bundle Svelte + proxy /api          Dockerfile.web
api   Litestar   : lit la base, sert /api, migre       Dockerfile.api
bot   discord.py : écoute la gateway, écrit en base    Dockerfile.bot  (1 réplica)
db    Postgres 18
```

- `core/` — **propriétaire du schéma**. Modèles, migrations Alembic (`core/migrations/`),
  session, upserts (`upserts.py`), lectures (`queries.py`), agrégation nocturne
  (`aggregation.py`), et les dataclasses plates de `records.py` qui sont le contrat
  entre l'observation de Discord et la base. Ne dépend ni de Litestar ni de discord.py.
- `backend/` — application Litestar qui ne sert que `/api`. `/` répond 404 par
  construction, et un test (`test_root_is_not_served_by_the_api`) le verrouille.
- `bot/` — le worker. `adapters.py` est la frontière discord.py, `catchup.py` le
  rattrapage (backfill et trou de gateway, même machine), `nightly.py` les jobs,
  `failures.py` la distinction panne / bug dont dépend le fast fail.
- `frontend/` — projet Vite/Svelte 5, racine Vite. `src/generated/` (client TS, schémas
  Zod, SDK) est dérivé et git-ignoré.
- `deploy/default.conf.template` — nginx : sert le bundle et proxifie `/api` vers
  `http://api:8000`. Une seule origine côté navigateur, donc ni CORS ni URL d'API dans
  le bundle. Le proxy Vite en dev joue exactement le même rôle.
- `deploy/api-entrypoint.sh` — `alembic upgrade head` puis la commande de l'image. Un
  seul migrateur, et `bot` attend `api` sain pour ne jamais démarrer sur un schéma
  périmé.

Les dépendances sont scindées en groupes : ce que les deux moitiés partagent est dans
`[project.dependencies]`, Litestar dans le groupe `api`, discord.py dans `bot`. Chaque
Dockerfile installe `--no-default-groups --group <le sien>`, donc aucune image ne porte
la pile de l'autre.

### Le contrat openapi.json

`just types` exporte le schéma depuis les handlers puis en dérive les types. **Committez
`openapi.json`** : `just check-types` régénère et fait `git diff --exit-code`, donc la CI
échoue si le fichier a dérivé des handlers.

Deux pièges :

- `ENABLE_DOCS=false` fait renvoyer `None` à `build_openapi_config`, ce qui supprime le
  routeur `/schema` **et sort le schéma de la mémoire**. `litestar assets generate-types`
  n'exporterait alors plus rien, sans erreur — c'est pourquoi la recette `types` force
  `ENABLE_DOCS=true`.
- Annotez les réponses avec une dataclass ou un `msgspec.Struct` (cf. `backend/routes.py`).
  Un `dict[str, str]` produit un `{ [key: string]: string }`, c'est-à-dire rien.

Le frontend se régénère sans Python : `pnpm -C frontend generate-types`.

### Le plugin Vite est inerte

`ViteConfig(enabled=False)` dans `backend/app.py` : plus de catch-all HTML, plus de
static files, plus de process Vite. Le plugin n'est gardé que pour les commandes
`litestar assets *` (génération de types), `on_cli_init` n'étant pas court-circuité.
Le réactiver au runtime recouplerait silencieusement les deux moitiés.

### Ajouter une route

Les contrôleurs restent agnostiques du préfixe : `/api` est appliqué par
`api_router = Router(path="/api", ...)` dans `app.py`. Enregistrez tout nouveau
contrôleur là, jamais avec un préfixe codé en dur. Les erreurs métier passent par
`AppError` (`backend/exceptions.py`), mappé en `application/problem+json` (RFC 9457).

### Clé d'API

Le garde `require_api_key` (`backend/security.py`) protège `/api/hello` ;
`/api/health` reste public (le healthcheck compose l'atteint sans proxy, et il est
exempté du rate limit). Points structurants :

- **La clé n'entre jamais dans le navigateur** : nginx et le proxy Vite l'injectent en
  `proxy_set_header` côté serveur. Ne l'exposez jamais via une variable `VITE_*` — elle
  serait inlinée en clair dans le bundle.
- **Échec en fermé** : un `API_KEY` absent verrouille la route au lieu de l'ouvrir, et
  `ensure_api_key_configured` (`on_startup`) empêche l'application de démarrer. La
  vérification est au démarrage, pas à l'import, pour que la CLI puisse charger le module
  sans clé.
- Ce n'est **pas** de l'authentification utilisateur : tout visiteur du site atteint
  `hello` à travers le proxy. Pour cloisonner par utilisateur, il faut une session.
- Une route gardée déclare `security=[{"APIKey": []}]`, sinon `openapi.json` la présente
  comme libre d'accès.

### Rate limit

`build_rate_limit_config` — 120 req/min, compté via `identify_client` qui lit `X-Real-IP`
(écrasé par nginx, donc non forgeable) et non `request.client`, qui serait le proxy pour
tout le monde. Le compteur vit en mémoire, par worker : avec `WEB_CONCURRENCY=4` le quota
effectif est quadruple. Un quota exact ou multi-répliques demande un magasin partagé.

## Tests

**Deux étages, et aucun SQLite.** L'étage 1 teste les fonctions pures — sans base, sans
réseau, sans objet discord.py. L'étage 2 porte le marqueur `db` et tourne sur un vrai
Postgres, parce que c'est là que vivent les propriétés que SQLite n'aurait pas
exercées : `ON CONFLICT` sur clé composite, `timestamptz`, index partiels, et les
migrations elles-mêmes. Le raisonnement complet est en section 4 de la spec du socle.

- `just test` saute l'étage 2 avec un message nommant `just db`. `just check` passe
  `--require-db`, ce qui transforme le saut en erreur : la porte avant push ne doit pas
  passer au vert en ayant testé la moitié de la suite.
- La fixture `db_session` greffe la session sur une transaction externe avec
  `join_transaction_mode="create_savepoint"`, donc tout est annulé à la fin du test.
  **Sans ce paramètre, un `commit()` du code testé valide pour de bon** et les tests se
  contaminent selon l'ordre de collecte. Deux tests solidaires de
  `tests/test_db_harness.py` existent uniquement pour le prouver — le premier commit, le
  second constate que rien n'a survécu.
- `migrated_database` est **synchrone**, et doit le rester : l'`env.py` d'Alembic appelle
  `asyncio.run`, qui lève depuis une boucle déjà en cours.
- `tests/test_migrations_match_models.py` porte deux garde-fous de niveaux différents, et
  la distinction compte : **hors base**, toute *table* décrite par un modèle doit être
  créée par une migration — la dérive la plus courante, attrapée sans Docker ; le
  contrôle **exact** est marqué `db` et demande à l'autogenerate d'Alembic ce qu'il
  resterait à faire après `upgrade head`. Seul le second voit une colonne manquante. Ne
  retentez pas de comparer du DDL rendu pour couvrir le second cas hors base : dès qu'une
  migration fait un `ALTER TABLE`, les deux textes ne peuvent plus coïncider — c'est
  précisément comme ça que la première version de ce test a été démentie.
- **Demandez `db_sessions` et non `db_engine`** dès que le code testé a besoin d'une
  *fabrique* de sessions — `catch_up_channel` et `run_nightly` en ouvrent une par page
  et par jour. Fabriquer la vôtre sur `db_engine` contourne le rollback et committe pour
  de vrai : c'est ce qui a fait échouer 19 tests à la première exécution contre un vrai
  Postgres, tous en lisant les lignes d'un autre test. `db_session` se déduit de
  `db_sessions`, le trick de transaction ne vivant qu'à un seul endroit.
- `migrated_database` tronque toutes les tables une fois par session : « la suite part
  d'une base vide » doit être vrai, pas espéré.
- **Sans Docker**, un Postgres réel reste accessible par un Postgres empaqueté en wheel,
  hors du projet et sans root. `pgserver` n'a pas de roue pour 3.14, mais le serveur n'a
  aucune raison de tourner sur le même Python que la suite :

  ```bash
  uv run --no-project --python 3.12 --with pgserver python -c \
    "import pgserver; s = pgserver.get_server('/tmp/pgdata_socle', cleanup_mode=None); \
     s.psql('CREATE DATABASE dashboard_discord_test'); print(s.get_uri())"
  export TEST_DATABASE_URL="postgresql+asyncpg://postgres@/dashboard_discord_test?host=/tmp/pgdata_socle"
  uv run pytest --require-db
  ```

  C'est ainsi que les 130 tests ont été exécutés pour la première fois. La version
  empaquetée est Postgres **16**, là où les composes et la CI déclarent 18 : bon pour
  lever un doute, pas pour valider un déploiement.
- `tests/factories.py` construit les records ; ne redéclarez pas un `an_event` local.

`tests/conftest.py` applique automatiquement le marqueur `anyio` à toute fonction de test
async — pas besoin de `pytestmark` dans chaque module. La fixture `client` (scope session)
pose `API_KEY` **et** `DATABASE_URL` avant le lifespan, puisque l'app refuse de démarrer
sans les deux ; l'URL est celle de la base de test quand il y en a une, un placeholder
sinon. Un seul client pour toute la session : `app` est un singleton de module, et un
second lifespan disposerait le moteur que le premier tient encore.

`pythonpath = ["."]` dans `pyproject.toml` est nécessaire, sinon `pytest` ne trouve pas
`backend`. `testpaths = ["tests"]` l'est aussi : sans lui, pytest part de la racine et
ramasse tout `test_*.py` du dépôt, y compris un second checkout posé à côté, dont les
modules entrent en collision de basename avec les vrais.

La section `[tool.pyrefly]` est le troisième piège : sans `project-includes`, pyrefly
retombe sur le preset `basic`, ne couvre aucun fichier du projet et annonce « 0 errors »
sans rien avoir vérifié. **Tout nouveau package doit être ajouté à `project-includes` et
aux `--cov=`** — les deux échouent en silence, jamais en rouge. `uv run pyrefly
dump-config` liste les fichiers réellement couverts.

## Git et déploiement

Gitflow : `main` (production, taguée), `develop` (intégration), `feature/*`, `release/*`,
`hotfix/*`. La CI valide contrat + lint + tests sur `main` et `develop` ; elle ne construit
pas les images (la plateforme les rebâtit).

Commits au format Conventional Commits, vérifiés par le hook `commitizen` (étape
`commit-msg`) : `uv run cz commit` pour la rédaction guidée, `uv run cz bump` pour version,
tag et CHANGELOG. `major_version_zero` est actif, le projet ne dépasse pas `0.x`.

Installer les hooks une fois : `prek install` (les deux types, pre-commit et commit-msg,
via `default_install_hook_types`).

La CI fait tourner un service `postgres:18-alpine` et pose `TEST_DATABASE_URL` : les
tests marqués `db` sont exigés, pas sautés.

Un push sur `main` déclenche le déploiement Coolify, mais seulement après un run vert
(`deploy` dépend de `check`). `compose.prod.yml` est la source de vérité des variables
côté Coolify — et un changement de ce fichier n'est pas toujours repris au
redéploiement, à vérifier à la main.
