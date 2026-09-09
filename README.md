# dashboard-discord

[![CI](https://github.com/Epoch-Zero-IA/dashboard-discord/actions/workflows/ci.yml/badge.svg)](https://github.com/Epoch-Zero-IA/dashboard-discord/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](.python-version)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Agent Discord de gestion de serveur et son tableau de bord. Quatre services : nginx qui
sert le bundle, une API Litestar, un worker connecté à la gateway Discord, et Postgres.
Le backend Python et le frontend Vite sont découplés — images, cycles de déploiement et
dimensionnements séparés.

Le contrat entre les deux est `openapi.json`, versionné à la racine. Le backend
l'exporte depuis ses handlers, le frontend en dérive ses types TypeScript sans avoir
besoin de Python. En production, nginx sert le bundle et proxifie `/api` vers
Litestar : une seule origine côté navigateur, donc pas de CORS ni d'URL d'API dans
le bundle.

Le worker Discord est un **service à part**, et il tourne en **un seul réplica,
définitivement** : deux instances connectées à la même gateway traiteraient chaque
message deux fois. C'est aussi ce qui permet à ses jobs planifiés (heartbeat,
agrégation et purge nocturnes) de vivre dans son process, sans verrou distribué.

Litestar 2.24, discord.py 2.7, SQLAlchemy 2 + Alembic, Postgres 18, Svelte 5, Vite 8,
Tailwind 4, nginx, pnpm, uv.

## Structure

```
core/             propriétaire du schéma — ni Litestar ni discord.py
  models.py       les tables ; les snowflakes Discord sont les clés primaires
  upserts.py      toutes les écritures, idempotentes par construction
  queries.py      les lectures
  aggregation.py  agrégation quotidienne et purge de rétention
  records.py      dataclasses plates : le contrat entre Discord et la base
  migrations/     révisions Alembic
backend/          application Litestar — sert /api, rien d'autre
  app.py          handlers et configuration des plugins
  ingest.py       GET /api/ingest/status
bot/              le worker Discord
  adapters.py     la frontière : objets discord.py vers records
  runner.py       le client gateway, ses handlers et ses jobs
  catchup.py      backfill et trou de gateway, une seule machine
  nightly.py      le job nocturne : agréger puis purger
  failures.py     panne de base ou bug maison — le fast fail en dépend
frontend/         projet Vite (racine Vite)
  src/generated/  client TypeScript généré (non versionné)
deploy/           nginx, et l'entrypoint qui applique les migrations
openapi.json      contrat d'API versionné, exporté depuis les handlers
Dockerfile.api    image de l'API (Python, sans discord.py)
Dockerfile.bot    image du worker (Python, sans Litestar)
Dockerfile.web    image du frontend (bundle Vite + nginx)
justfile          raccourcis des tâches courantes
```

## Installation

Il faut [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/) et Node `^20.19`
ou `>=22.12`, contrainte de Vite 8. [`just`](https://github.com/casey/just) est
recommandé (`uv tool install rust-just`) mais facultatif.

Il faut aussi **Docker**, et pas seulement pour déployer : Postgres tourne dedans en
développement (`just db`), et les tests marqués `db` — un bon tiers de la suite —
l'exigent. `just check`, la porte avant push, échoue sans base plutôt que de sauter ces
tests en silence.

```bash
cp .env.example .env
just install          # uv sync + litestar assets install
```

Sans `just`, la même chose à la main :

```bash
uv sync
uv run litestar assets install
```

Ne sautez pas la copie du `.env` : il définit `LITESTAR_APP`. Sans lui, la CLI
cherche l'application à la racine et ne la trouve pas, puisque le code est dans
`backend/`. Trois valeurs y sont à remplir avant de démarrer quoi que ce soit :
`API_KEY`, `POSTGRES_PASSWORD` et `DISCORD_TOKEN` — les deux dernières bloquent
`docker compose up` si elles manquent, et le worker refuse de démarrer sans la
troisième.

## Commandes

Les tâches courantes passent par `just` ; `just` seul liste les recettes.

| Commande | Effet |
|----------|-------|
| `just db` | démarre Postgres seul (prérequis des tests marqués `db`) |
| `just dev` | lance l'API (:8000) et le frontend (:5173) ensemble |
| `just dev-api` | l'API seule, en rechargement à chaud |
| `just dev-front` | le frontend seul, avec HMR |
| `just dev-bot` | le worker Discord seul |
| `just dev-all` | les trois à la fois |
| `just migrate` | applique les migrations en attente |
| `just migration m="…"` | écrit une migration depuis l'écart modèles / base |
| `just backfill` | importe tout l'historique lisible, puis sort |
| `just types` | exporte `openapi.json` et régénère le client TypeScript |
| `just lint` | ruff + pyrefly (Python), eslint + prettier + svelte-check (frontend) |
| `just format` | formate et corrige (ruff côté Python, prettier + eslint côté frontend) |
| `just test` | pytest ; les tests marqués `db` sont sautés sans base |
| `just test-with-db` | la même suite, base exigée |
| `just build` | bundle de production du frontend |
| `just check` | tout : contrat + lint + tests (ce que lance la CI) |

Chaque recette reprend les commandes `uv`/`pnpm` sous-jacentes ; rien n'oblige à
passer par `just`, mais c'est le point d'entrée unique, aligné sur les hooks
pre-commit et la CI.

## Démarrer

```bash
just dev
```

Deux process démarrent : l'API sur le port 8000 et le dev server Vite sur 5173.
**Le site se consulte sur http://127.0.0.1:5173** — Vite proxifie `/api` vers
l'API, exactement comme nginx le fera en production. Le code client appelle donc
`/api` en relatif et ignore où vit le backend, en dev comme en production.

L'API seule ne sert aucune page : `http://127.0.0.1:8000/` répond 404, par
construction. Un test le vérifie.

Si quelque chose cloche dans la configuration :

```bash
uv run litestar assets doctor
```

## Le worker Discord

Avant toute chose, deux réglages **manuels** dans le portail développeur Discord, sous
*Bot > Privileged Gateway Intents* : activez `MESSAGE CONTENT INTENT` et `SERVER MEMBERS
INTENT`. Sans eux, Discord livre des messages au contenu vide et ne signale pas les
arrivées de membres — sans erreur, sans avertissement. C'est le pire mode d'échec du
projet, donc le worker vérifie ses intents au démarrage et **refuse de démarrer** s'ils
manquent, en nommant la case à cocher.

Puis mettez le token dans `.env` (`DISCORD_TOKEN=`), invitez le bot sur le serveur, et :

```bash
just db          # Postgres
just migrate     # le schéma
just dev-bot     # le worker
```

Le worker ingère en temps réel et, à la connexion, comble le trou laissé par son arrêt.
Backfill initial et trou de gateway sont la même machine : elle avance par pages de cent
messages, un canal à la fois, et écrit son curseur avec chaque page — une interruption ne
coûte donc jamais plus d'une page. Le rattrapage au démarrage est limité à vingt pages
par canal pour ne pas retenir la connexion ; `just backfill` fait le reste sans limite.

Sa santé se lit dans la base, pas sur un port : un heartbeat toutes les trente secondes,
que le healthcheck de compose interroge avec `python -m bot.healthcheck`. C'est le seul
contrôle qui prouve à la fois que le process vit, que la gateway est connectée et que
Postgres répond.

**Si Postgres devient injoignable, le worker sort en code non nul.** Il ne met rien en
tampon mémoire : l'orchestrateur le redémarre et le rattrapage par curseurs recomble le
trou, exactement comme après un redéploiement. Un bug de notre côté, lui, est loggué
sans tuer le process — la distinction est dans `bot/failures.py`.

## Base de données

Postgres, avec les identifiants Discord (des snowflakes 64 bits) comme clés primaires.
Il n'y a donc aucune clé de substitution, et **toute écriture est un upsert** : réingérer
le même message est indiscernable de l'avoir ingéré une fois.

Deux tables méritent d'être connues :

- `message` garde le contenu. Une suppression **vide le contenu et conserve la ligne** :
  la suppression demandée par le membre est honorée, la statistique reste juste.
- `daily_activity` agrège par jour, canal et auteur. Un job nocturne l'alimente puis
  purge `message` au-delà de `MESSAGE_RETENTION_DAYS` (90 par défaut). L'agrégat survit
  indéfiniment : le tableau de bord garde son historique sans garder les conversations.

La purge ne supprime **jamais** un jour qui n'a pas déjà été agrégé. C'est la seule règle
du projet dont la violation serait irréversible, et elle est vérifiée en base, pas
laissée à la vigilance de l'appelant.

Les migrations s'appliquent toutes seules au déploiement, dans l'entrypoint de l'image
`api` — un seul migrateur, avant que Granian ne démarre. En local, `just migrate`.

`GET /api/ingest/status` (protégé par la clé d'API) répond où en est l'ingestion : par
canal, les curseurs, l'état de complétion, le nombre de messages, et la date du dernier
heartbeat du worker.

## Docker

Trois images, chacune buildable sans les autres, et un Postgres officiel :

- `Dockerfile.api` — Python seul, sans Node ni outils de build. L'interpréteur, le venv,
  `backend/` et `core/`, sous un utilisateur non privilégié. **C'est cette image qui
  applique les migrations**, dans son entrypoint, avant de démarrer Granian.
- `Dockerfile.bot` — le worker : `core/` et `bot/`, sans Litestar. Aucun port ouvert ;
  sa santé se lit dans la base, pas sur une socket.
- `Dockerfile.web` — étage Node qui dérive les types de `openapi.json` et build le
  bundle, puis nginx sans privilèges qui le sert.

```bash
docker compose up --build
```

L'app répond sur http://127.0.0.1:8000, servie par nginx. L'API n'est pas exposée sur
l'hôte : seul `web` l'atteint, par le réseau interne de compose. Les dépendances sont
chaînées par healthcheck — `db` avant `api`, `api` (qui migre) avant `bot`, `api` avant
`web` — donc un `docker compose up` sain signifie que le schéma est à jour et que le
worker est connecté.

Seul Postgres publie un port en développement (5432), parce que les tests, Alembic et
`just dev-bot` tournent sur l'hôte. `compose.prod.yml` ne publie rien du tout.

### Coolify

`compose.prod.yml` est la variante pour un déploiement Coolify. Trois différences avec
`compose.yml`, toutes dues au fait de tourner derrière le Traefik de Coolify :

- **aucun `ports:`** — publier un port contournerait le proxy et exposerait le
  conteneur directement sur l'hôte ; Traefik joint `web` par le réseau du projet ;
- **`SERVICE_FQDN_WEB_8080`** — variable magique, volontairement sans valeur : Coolify
  génère un domaine, l'attache au service `web` et le route vers le port 8080 ;
- **`restart: unless-stopped`**, et `API_KEY` déclarée avec `:?` donc requise dans
  l'interface — une valeur vide bloque le déploiement.

`api` n'a ni domaine ni port publié : Coolify garde ces services privés au réseau du
projet, joignables seulement en `http://api:8000`, ce que fait nginx. Ne lui donnez un
domaine que pour ouvrir l'API à des tiers, et lisez la section sur la clé d'API avant.

nginx préserve les en-têtes `X-Forwarded-*` posés par le proxy amont au lieu de les
écraser : Traefik termine le TLS et parle à nginx en clair, donc transmettre `$scheme`
ferait croire à l'application que le visiteur n'est pas en HTTPS — de quoi casser les
cookies `Secure` et les redirections absolues. Sans proxy devant, en local, la valeur
retombe sur `$scheme`. Le module `real_ip` récupère par ailleurs l'adresse réelle du
client, en ne faisant confiance qu'aux plages privées : `X-Forwarded-For` est contrôlé
par l'appelant et ne doit jamais être cru s'il arrive directement d'Internet.

Coolify considère ce fichier comme la source de vérité : déclarez les variables ici,
pas seulement dans l'interface.

### Dimensionner

L'API porte la charge : le frontend est un bundle statique qu'un visiteur télécharge
une fois, puis met en cache (les noms sont hashés, nginx les sert en `immutable`).
C'est donc l'API qu'on dimensionne.

```bash
WEB_CONCURRENCY=4 docker compose up -d    # 4 workers Granian
```

Passer à l'horizontal ensuite ne demande que des répliques d'`api` derrière nginx —
à condition de n'avoir mis aucun état en mémoire dans le processus.

## Clé d'API

`/api/hello` est protégée par une clé, `/api/health` reste publique (le healthcheck de
compose l'atteint directement, sans proxy). Le garde vit dans `backend/security.py` et
s'accroche au handler par `guards=[require_api_key]`.

Le point important : **la clé n'entre jamais dans le navigateur**. nginx l'ajoute en
`proxy_set_header` côté serveur, et le proxy Vite fait de même en développement. Le
frontend appelle donc `/api/hello` sans rien présenter — inspectez les requêtes dans
les DevTools, il n'y a pas d'en-tête `X-API-Key`.

C'est délibéré : une variable `VITE_*` est inlinée en clair dans le bundle, donc une
clé embarquée dans un SPA est une clé publique.

```bash
API_KEY=… docker compose up -d          # ou API_KEY dans le .env
curl http://127.0.0.1:8000/api/hello    # 200, via nginx qui injecte la clé
curl -H "X-API-Key: …" http://api:8000/api/hello   # 200, client tiers
```

Sans `API_KEY`, rien ne démarre : `docker compose up` s'arrête à l'interpolation, et
l'application elle-même refuse de démarrer (`ensure_api_key_configured`, appelée par
`on_startup`). Le conteneur sort en code 1 avec la cause dans les logs, donc il ne
devient jamais *healthy* et `depends_on: service_healthy` garde le frontend éteint :
le déploiement signale l'échec au lieu de servir une application cassée.

Ce garde-fou existe parce que l'inverse a été observé : sur Coolify, `${API_KEY:?}`
ne bloque pas le déploiement comme le fait `docker compose` seul. La variable était
vide, nginx a alors **supprimé** l'en-tête — il ne transmet pas un en-tête dont la
valeur est vide — et l'API répondait 401 à chaque appel sans que rien n'indique
pourquoi. La configuration échoue en fermé, jamais en ouvert, et désormais elle
échoue bruyamment.

Ce que cela protège, et ce que cela ne protège pas : la route est réservée aux appels
passant par votre nginx ou porteurs de la clé, ce qui permet d'exposer un domaine
d'API à des clients tiers. Ce n'est **pas** de l'authentification utilisateur — tout
visiteur du site atteint `hello` à travers le proxy. Pour cloisonner des données par
utilisateur, il faut une session (un cookie `HttpOnly` fonctionne sans CORS ici,
grâce à l'origine unique).

Swagger UI (`/schema/swagger`) affiche un bouton « Authorize » et un cadenas sur la
route : le schéma de sécurité est déclaré dans `openapi.json`, donc le contrat ne
présente pas la route comme libre d'accès.

### Documentation OpenAPI

`ENABLE_DOCS` (défaut `true`) commande les routes `/schema` — Swagger, Redoc,
`openapi.json`. `compose.prod.yml` la passe à `false` : `/schema` publie l'inventaire
complet des routes, et l'API disposant de son propre domaine sur Coolify, la masquer
dans nginx ne suffirait pas. La coupure se fait donc dans l'application, où elle vaut
pour tous les chemins d'accès. Mettez `ENABLE_DOCS=true` dans les variables du projet
pour la rétablir le temps d'un diagnostic.

Techniquement, `build_openapi_config` renvoie `None`, ce qui supprime le routeur
`/schema`. Le schéma quitte alors la mémoire : `litestar assets generate-types` en a
besoin, c'est pourquoi la recette `just types` force `ENABLE_DOCS=true`. Sans ce
forçage, la commande n'exporterait plus rien — sans erreur — et le contrat dériverait
sans que personne ne le voie.

## Durcissement HTTP

nginx pose quatre en-têtes sur les réponses du frontend (`deploy/security-headers.conf`) :
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, une `Referrer-Policy`
stricte et une `Content-Security-Policy` en `'self'`. Le bundle est entièrement
auto-hébergé — pas de CDN, pas de script inline, pas de `eval` — donc la CSP n'a besoin
d'aucun `unsafe-inline`, et `connect-src 'self'` suffit pour appeler `/api` grâce à
l'origine unique.

Ce fichier est inclus dans chaque `location` plutôt que déclaré une fois sur le bloc
`server` : nginx n'hérite pas des `add_header` dans un bloc enfant qui en déclare
lui-même, et `/assets/` en pose un pour le cache — il perdrait donc silencieusement
tous les autres. `/api` et `/schema` en sont exclus : une CSP ne concerne pas une
réponse JSON, et Swagger UI a besoin de styles inline.

L'API applique un quota de 120 requêtes par minute et par client
(`build_rate_limit_config`), avec `/api/health` exempté pour ne jamais gêner le
healthcheck. Le comptage n'utilise pas le client de la connexion — derrière nginx, ce
serait le proxy pour tout le monde, donc un quota partagé — mais l'en-tête `X-Real-IP`,
que nginx écrase et qu'un appelant ne peut donc pas forger.

À savoir : le compteur vit dans le magasin en mémoire, propre à chaque worker. Avec
`WEB_CONCURRENCY=4`, le quota effectif est donc quadruple. Le rendre exact, et le faire
survivre à plusieurs répliques d'API, demande un magasin partagé comme Redis.

## Qualité

Le lint, le typage et les tests couvrent backend et frontend d'un seul point :

```bash
just lint             # ruff, pyrefly, eslint, prettier, svelte-check
just test             # pytest + couverture
just test-with-db     # la même suite, base exigée
```

Les mêmes vérifications tournent à chaque commit via [prek](https://github.com/j178/prek)
(ou pre-commit) — lancez `prek install` une fois — et dans la CI GitHub Actions.

**Les tests ne tournent pas sur SQLite.** Deux étages : les fonctions pures d'un côté,
sans base ni réseau ; de l'autre, tout ce qui parle SQL, sur un vrai Postgres. C'est là
que vivent les propriétés qu'un moteur de substitution n'aurait pas exercées — `ON
CONFLICT` sur clé composite, timestamps avec fuseau, index partiels, et les migrations
elles-mêmes.

`just test` saute le second étage avec un message nommant `just db`, pour que travailler
sans Docker reste possible. `just check` — la porte avant push, et ce que lance la CI —
l'**exige** : une porte qui passe au vert en ayant testé la moitié de la suite ne garde
rien.

## Développement

Lancez toujours `litestar` depuis la racine du dépôt. Depuis `frontend/`, Python ne
trouve pas le package `backend` et la commande échoue.

### Types TypeScript

Après avoir touché à une route ou à un type de réponse :

```bash
just types      # uv run litestar assets generate-types
```

La commande écrit `openapi.json` à la racine, puis en tire les types, les schémas
Zod, un client d'API et un helper de routage dans `frontend/src/generated/`.

**Committez `openapi.json`.** C'est le contrat : il rend le frontend buildable sans
Python, et tout changement d'API devient un diff lisible en revue. `just check`
(donc la CI) régénère le fichier et échoue s'il a dérivé des handlers.

Le frontend peut se régénérer seul, sans Python :

```bash
pnpm -C frontend generate-types
```

Un détail qui compte : annotez les réponses avec une dataclass ou un
`msgspec.Struct`. Un `dict[str, str]` donne un `{ [key: string]: string }`,
c'est-à-dire à peu près rien.

### Commits

Commits au format [Conventional Commits](https://www.conventionalcommits.org), via
[Commitizen](https://commitizen-tools.github.io/commitizen/) configuré dans `cz.toml`.

```bash
uv run cz commit     # rédaction guidée
uv run cz bump       # version, tag et CHANGELOG
```

Le format est aussi vérifié automatiquement à chaque commit : le hook `commitizen`
(étape `commit-msg`) rejette un message non conforme. Il s'installe avec le reste
via `prek install` (voir `default_install_hook_types` dans `.pre-commit-config.yaml`).

`cz bump` lit et écrit la version dans `pyproject.toml` via uv. Tant que
`major_version_zero` est actif, le projet ne dépasse pas `0.x`.

### Travailler sur le frontend seul

```bash
just dev-front            # Vite seul, sans backend
just build                # bundle de production dans frontend/dist
```

Les appels `/api` sont proxifiés vers `http://127.0.0.1:8000`. Si l'API écoute
ailleurs, pointez `API_URL` dessus dans le `.env`. Sans API lancée, l'app s'affiche
et les appels échouent — le frontend reste développable seul.

## Branches et CI

Le dépôt suit [Gitflow](https://nvie.com/posts/a-successful-git-branching-model/) :
`main` (production, taguée), `develop` (intégration), et des branches `feature/*`,
`release/*`, `hotfix/*`. La CI (`.github/workflows/ci.yml`) valide le contrat, le lint
et les tests sur `main` et `develop`.

Elle ne construit pas les images : elles ne sont poussées vers aucun registre, et la
plateforme de déploiement les rebâtit depuis le dépôt — un build en CI ne ferait que
dupliquer, quelques minutes plus tôt, un échec qui apparaîtrait de toute façon au
déploiement. Pour les vérifier en local : `docker compose build`.

### Déploiement automatique

Un push sur `main` déclenche le déploiement, mais seulement après un run vert : le job
`deploy` dépend de `check`. C'est la raison de passer par Actions plutôt que par l'*Auto
Deploy* de Coolify, qui se déclenche sur le push lui-même — un commit faisant échouer la
vérification du contrat, le lint ou les tests partirait alors en production.

Deux secrets de dépôt sont nécessaires :

| Secret | Où le trouver |
|--------|---------------|
| `COOLIFY_DEPLOY_WEBHOOK` | Coolify → application → Configuration → Webhooks, URL de déploiement |
| `COOLIFY_TOKEN` | Coolify → Keys & Tokens → API Tokens, avec la permission `deploy` |

L'URL du webhook porte déjà l'UUID de la ressource, d'où un secret de moins qu'avec
l'appel générique `/api/v1/deploy`. Tant que ces secrets sont absents, le job `deploy`
échoue avec un message explicite — un déploiement non configuré doit se voir.

Attention : un changement de `compose.prod.yml` n'est pas toujours repris par Coolify
lors d'un redéploiement ([#6995](https://github.com/coollabsio/coolify/issues/6995),
[#7084](https://github.com/coollabsio/coolify/issues/7084)). Le déclenchement
automatique couvre les changements de code ; après avoir touché au compose, vérifiez
qu'il a bien été rechargé.
