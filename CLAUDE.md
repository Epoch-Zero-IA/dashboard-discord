# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Travaux en cours

Le dépôt est un squelette (deux routes, une page Svelte, pas de base de données). La
cible est un agent Discord de gestion de serveur, découpé en sept sous-projets
indépendants, chacun avec sa propre spec.

**Lire `docs/superpowers/specs/2026-09-08-bot-discord-socle-design.md` avant de toucher
au code.** Le document porte le découpage complet, les décisions déjà prises et leurs
raisons, et surtout ce qui n'est *pas* encore tranché. Il est incomplet à dessein :
sections 1 et 2 validées, section 3 en attente d'un accord, section 4 à écrire.

Les décisions déjà actées qui contraignent tout code écrit dès maintenant :

- Le worker Discord est un **troisième service** (`bot`), pas un `on_startup` de
  Litestar, et il tourne en **un seul réplica définitivement** — deux instances sur la
  même gateway traitent chaque message deux fois.
- Le schéma de base appartient à un package `core/` neuf, sans dépendance à Litestar ni
  à discord.py, dont `backend/` et `bot/` dépendent tous les deux. Cette extraction
  touche du code existant.
- Postgres + advanced-alchemy + Alembic ; identifiants Discord en `BIGINT` comme clés
  primaires, donc écritures idempotentes par upsert.
- La stack LLM sera LangChain + LiteLLM, mais aucun morceau LLM n'est spécifié à ce
  jour.

Ne partez pas de la demande initiale pour improviser : elle décrit six fonctionnalités
qui ont déjà été décomposées et ordonnancées dans la spec.

## Commandes

`just` est le point d'entrée unique (les hooks pre-commit et la CI appellent les mêmes
recettes). `just` seul liste tout. Il charge `.env` automatiquement (`set dotenv-load`),
d'où l'obligation de `cp .env.example .env` avant tout.

```bash
just install      # uv sync + litestar assets install (node_modules du frontend)
just dev          # API :8000 + Vite :5173 — se consulte sur http://127.0.0.1:5173
just dev-api      # API seule, reload limité à backend/
just dev-front    # Vite seul, HMR, sans backend
just types        # exporte openapi.json et régénère frontend/src/generated/
just lint         # ruff format --check, ruff check, pyrefly, eslint, prettier, svelte-check
just format       # ruff format, ruff check --fix, prettier --write, eslint --fix
just test         # pytest + couverture de `backend`
just check        # check-types + lint + test — la porte avant push, ce que lance la CI
```

Un seul test : `uv run pytest tests/test_api.py::test_health_check`.

`litestar` se lance **toujours depuis la racine du dépôt** : depuis `frontend/`, le
package `backend` est introuvable. `LITESTAR_APP=backend.app:app` doit être dans
l'environnement (`.env` en local, `env:` du workflow en CI) — l'autodiscovery ne
cherche qu'à la racine.

Diagnostic de configuration : `uv run litestar assets doctor`.

## Architecture

Backend et frontend sont **découplés** : deux images Docker, deux déploiements, deux
dimensionnements. Le contrat entre les deux est `openapi.json`, versionné à la racine.

- `backend/` — application Litestar qui ne sert que `/api`. `/` répond 404 par
  construction, et un test (`test_root_is_not_served_by_the_api`) le verrouille.
- `frontend/` — projet Vite/Svelte 5, racine Vite. `src/generated/` (client TS, schémas
  Zod, SDK) est dérivé et git-ignoré.
- `deploy/default.conf.template` — nginx : sert le bundle et proxifie `/api` vers
  `http://api:8000`. Une seule origine côté navigateur, donc ni CORS ni URL d'API dans
  le bundle. Le proxy Vite en dev joue exactement le même rôle.

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

`tests/conftest.py` applique automatiquement le marqueur `anyio` à toute fonction de test
async — pas besoin de `pytestmark` dans chaque module. La fixture `client` (scope session)
pose `API_KEY` avant le lifespan, puisque l'app refuse de démarrer sans. La fixture
`api_key` fait de même pour un test isolé.

`pythonpath = ["."]` dans `pyproject.toml` est nécessaire, sinon `pytest` ne trouve pas
`backend`. La section `[tool.pyrefly]` l'est aussi : sans `project-includes`, pyrefly
retombe sur le preset `basic`, ne couvre aucun fichier du projet et annonce « 0 errors »
sans rien avoir vérifié (`uv run pyrefly dump-config` liste les fichiers réellement
couverts).

## Git et déploiement

Gitflow : `main` (production, taguée), `develop` (intégration), `feature/*`, `release/*`,
`hotfix/*`. La CI valide contrat + lint + tests sur `main` et `develop` ; elle ne construit
pas les images (la plateforme les rebâtit).

Commits au format Conventional Commits, vérifiés par le hook `commitizen` (étape
`commit-msg`) : `uv run cz commit` pour la rédaction guidée, `uv run cz bump` pour version,
tag et CHANGELOG. `major_version_zero` est actif, le projet ne dépasse pas `0.x`.

Installer les hooks une fois : `prek install` (les deux types, pre-commit et commit-msg,
via `default_install_hook_types`).

Un push sur `main` déclenche le déploiement Coolify, mais seulement après un run vert
(`deploy` dépend de `check`). `compose.prod.yml` est la source de vérité des variables
côté Coolify — et un changement de ce fichier n'est pas toujours repris au
redéploiement, à vérifier à la main.
