# Bot Discord — plan d'implémentation du socle (morceau A)

- **Date** : 2026-09-08
- **État** : à valider.
- **Spec** : `docs/superpowers/specs/2026-09-08-bot-discord-socle-design.md`. Ce plan ne
  rejuge aucune de ses décisions ; il les ordonne. Toute question de « pourquoi » se
  répond là-bas.

## La règle qui gouverne le découpage

**Chaque étape laisse `just check` vert et est mergeable seule.** Ce n'est pas une
coquetterie : l'étape 0 peut invalider tout le reste, et les étapes 3 à 5 touchent un
process qui écrit en base. Une branche qui n'est verte qu'à la fin ne dit jamais laquelle
de ses vingt modifications a cassé quoi.

Corollaire sur l'ordre : le schéma vient avant le worker, et le rattrapage après
l'ingestion temps réel — bien que ce soit la même machinerie côté écriture, le temps réel
est testable avec un seul message, le rattrapage demande une source paginée.

## Prérequis humains, à faire avant l'étape 0

- Portail développeur Discord : créer l'application, **activer les intents privilégiés
  `MESSAGE_CONTENT` et `GUILD_MEMBERS`**, récupérer le token, inviter le bot sur un
  serveur de test avec un canal dédié. Sans les intents, `content` arrive vide en
  silence — c'est le pire mode d'échec du projet et l'étape 0 existe pour le prendre au
  collet tout de suite.
- Docker en local (l'étage 2 des tests et le service `db` en dépendent).

## Étape 0 — Dérisquer discord.py sous Python 3.14

**Avant toute autre ligne.** `discord.py` 2.7.1 déclare `requires_python >=3.8` mais ne
publie de classifiers que jusqu'à 3.12, alors que le dépôt impose 3.14.

- `uv add --group bot "discord.py>=2.7"`.
- Un script jetable qui ouvre une connexion gateway, logue `on_ready`, lit **un** message
  du canal de test et sort. Il vérifie deux choses d'un coup : la bibliothèque tourne
  sous 3.14, et les intents privilégiés sont réellement actifs (`content` non vide).

**Fini quand** : le script se connecte et imprime le contenu d'un message réel.

**Résultat partiel du 2026-09-08 — la moitié « bibliothèque » du risque est levée.**
Vérifié dans un projet jetable hors du dépôt, sous CPython **3.14.7** :

- `discord.py` **2.7.1** s'installe et s'importe sans avertissement. Les classifiers
  étaient simplement en retard sur la réalité.
- `aiohttp` **3.14.3** fournit des wheels `cp314` : **aucune compilation depuis les
  sources**, ce qui était le vrai risque derrière le doute — une roue manquante aurait
  imposé une toolchain C dans l'image du worker.
- La résolution tire automatiquement `audioop-lts`, backport du module retiré de la
  stdlib en 3.13 : la bibliothèque a donc déjà traité les suppressions de ce genre.
- `discord.Client(intents=...)` se construit avec `message_content` et `members` activés.

Reste à vérifier, et seul un token peut le faire : **l'ouverture réelle de la connexion
gateway** et l'arrivée d'un message avec `content` non vide. C'est la moitié qui valide
les prérequis du portail, pas la bibliothèque.

**Si ça casse** : la spec prévoit d'épingler le worker sur 3.13 dans sa propre image.
Attention, l'arbitrage est plus coûteux qu'il n'y paraît — `requires-python` ne peut pas
valoir `>=3.14` et `3.13` à la fois, donc il faudrait soit deux toolchains et deux jobs
CI, soit reculer tout le dépôt en 3.13. **Re-arbitrer à ce moment-là** plutôt
qu'appliquer mécaniquement ; reculer le dépôt entier est probablement plus simple que
maintenir deux environnements.

## Étape 1 — `core/` et le harnais de tests de l'étage 2

Aucun modèle encore : cette étape installe la plomberie et la manière de la tester.

- `core/` : `config.py` (lit `DATABASE_URL`), `db.py` (engine async, `async_sessionmaker`,
  `metadata`). Aucune dépendance à Litestar ni à discord.py — c'est vérifiable et ça
  mérite un test d'import.
- Alembic monté à vide : `alembic.ini`, `env.py` **en mode async** sur la même
  `DATABASE_URL`. Zéro révision pour l'instant ; `upgrade head` sur un historique vide
  est un no-op valide, ce qui permet au harnais de tests d'exister avant le schéma.
- `pyproject.toml` : groupes `api` / `bot`, `--cov=backend --cov=core --cov=bot`,
  `project-includes = ["backend", "core", "bot", "tests"]`, marqueur `db`.
- `compose.yml` : service `db` (`postgres:18-alpine`, volume `pgdata`, healthcheck
  `pg_isready`).
- `.env.example` : `DATABASE_URL`, `TEST_DATABASE_URL`, `DISCORD_TOKEN` (sans valeur),
  `MESSAGE_RETENTION_DAYS`.
- `justfile` : `just db`.
- `tests/conftest.py` : fixture de session (`alembic upgrade head` une fois), fixture par
  test greffée sur une transaction externe avec
  `join_transaction_mode="create_savepoint"`, et le skip bruyant nommant `just db` quand
  la base est absente.

**Fini quand** : `just db && just check` vert ; un test `db` trivial passe, et est sauté
avec un message utile quand la base est éteinte ; `uv run pyrefly dump-config` liste bien
`core/`.

**Le point délicat est le harnais, pas le schéma.** Il se vérifie par deux tests
solidaires : le premier écrit une ligne et **commit**, le second affirme que la table est
vide. Sans `create_savepoint`, le second échoue — c'est exactement le test qu'on veut
avoir écrit avant d'en dépendre.

## Étape 2 — Schéma, première migration, upserts

- Modèles : `guild`, `channel`, `discord_user`, `message`, `reaction`, `daily_activity`,
  `ingest_cursor`, plus **une table de heartbeat** que la liste de la section 2 de la
  spec ne mentionnait pas alors que la section 1 en dépend pour le healthcheck. Ajout
  assumé ici.
- `BIGINT` en clés primaires, `DateTime(timezone=True)` partout, index `(channel_id,
  created_at)` et `(author_id, created_at)` pour les agrégats à venir, index partiel
  `WHERE deleted_at IS NULL`.
- `core/upserts.py` : un `on_conflict_do_update()` par dimension et pour `message` ;
  `on_reaction_remove` supprime la ligne de `reaction`.
- Première révision Alembic, **relue à la main** : l'autogenerate ne devine pas les index
  partiels.

**Fini quand** : réingestion du même message → une seule ligne ; suppression → `content`
vidé et `deleted_at` posé, ligne conservée ; édition d'un message absent → no-op sans
exception ; `upgrade head` → `downgrade base` → `upgrade head` sur une base vierge.

## Étape 3 — Le worker : frontière, handlers, heartbeat, fast fail

- `bot/events.py` : dataclasses plates. `bot/adapters.py` : conversion depuis les objets
  discord.py, et **rien d'autre**. `bot/ingest.py` : la logique, qui ne connaît que les
  dataclasses. C'est la frontière dont dépend tout l'étage 1 des tests.
- `bot/runner.py` : client, intents déclarés, `setup_hook`. Vérification des intents au
  démarrage via `application_info()`, refus de démarrer s'ils manquent, dans l'esprit de
  `ensure_api_key_configured`. **Piège confirmé à l'étape 0** : `ApplicationFlags` expose
  *deux* drapeaux par intent privilégié — `gateway_message_content` et
  `gateway_message_content_limited`, de même pour `gateway_guild_members`. Le second est
  celui d'un bot présent sur moins de cent serveurs, donc **le nôtre**. Un contrôle qui
  ne teste que le premier refuserait de démarrer alors que l'intent est parfaitement
  activé : la condition est un `or` sur les deux, et ce cas mérite son test.
- Isolation des handlers : un décorateur qui logue l'erreur avec l'identifiant de
  l'événement et rend la main. Un canal en vrac ne fait pas tomber les autres.
- Heartbeat en base toutes les 30 s via `tasks.loop`.
- Fast fail base : la sortie passe par `client.close()` puis un code de retour non nul
  depuis `__main__`, **pas** un `os._exit` au milieu d'un handler — sinon la connexion
  gateway reste à demi fermée et le redémarrage traîne.

**Fini quand** : un message posté sur le serveur de test est en base en moins d'une
seconde ; couper la base fait sortir le process en code non nul ; une exception injectée
dans un handler n'interrompt pas la boucle (étage 1).

## Étape 4 — Le rattrapage, un composant pour deux usages

Backfill initial et trou de gateway sont le même problème dans deux sens : un seul
composant, `bot/catchup.py`.

- Un canal à la fois, pages de 100, `ingest_cursor` écrit **après chaque page** : une
  interruption ne perd qu'une page. Pas de `asyncio.gather` — c'est notre seule
  protection contre les rate limits, discord.py gérant déjà les 429.
- La source des messages est derrière un protocole étroit (`MessageSource`), que les
  tests remplacent par une liste. La décision « où reprendre » redevient une fonction
  pure.
- Appelé depuis `setup_hook` au démarrage (rattrapage) et par une sous-commande
  (backfill initial).

**Fini quand** : reprise depuis un curseur au milieu d'un canal, sans trou ni doublon
(étage 1) ; un rattrapage rejoué deux fois est idempotent (étage 2) ; et en réel :
arrêter le bot, poster cinq messages, redémarrer, retrouver les cinq.

## Étape 5 — Jobs nocturnes : agrégation puis purge

- `tasks.loop` à heure fixe : recalcul complet de `daily_activity` pour le jour écoulé
  (upsert, donc rejouable), puis purge de `message` au-delà de
  `MESSAGE_RETENTION_DAYS`.
- **Règle non négociable : la purge ne supprime qu'un jour dont l'agrégat existe.** Une
  purge qui suit une agrégation échouée détruirait les données pour de bon.
- Découpe par jour calculée en Python, en UTC pour le socle (cf. section 4 de la spec).

**Fini quand** : agrégation rejouée deux fois → même résultat ; agrégat manquant → rien
n'est purgé ; un message supprimé, dont `content` est vidé, compte toujours dans
l'agrégat.

## Étape 6 — La surface API de recette

- `GET /api/ingest/status` dans un nouveau contrôleur, enregistré dans l'`api_router` de
  `app.py` (jamais de préfixe codé en dur) : curseurs et complétion par canal, nombre de
  messages, dernier heartbeat.
- Réponse en `msgspec.Struct`, `guards=[require_api_key]`, `security=[{"APIKey": []}]`.
- Session injectée par dépendance ; advanced-alchemy fournit la configuration Litestar,
  cohérent avec le choix de la spec.
- `/api/health` **inchangé** et sans accès base. `test_root_is_not_served_by_the_api`
  reste vert.

**Fini quand** : `just check-types` vert avec `openapi.json` committé, un test couvre le
401 sans clé, et le schéma décrit les champs (pas un `dict[str, str]`).

## Étape 7 — Déploiement

- `Dockerfile.bot` : `core/` + `bot/` + groupe `bot`. Ni Litestar, ni nginx, ni
  `frontend/`. Healthcheck sur le heartbeat en base.
- Entrypoint de `Dockerfile.api` : `alembic upgrade head` puis Granian. Vérifier qu'il
  tourne **une fois** et non une fois par worker avec `WEB_CONCURRENCY > 1`.
- `compose.yml` et `compose.prod.yml` à quatre services ; `bot` en
  `restart: unless-stopped`, sans `replicas`, avec le commentaire qui dit pourquoi.
- `ci.yml` : bloc `services:` sur `postgres:18` et `TEST_DATABASE_URL` dans `env:`.
  `just check` reste la commande unique appelée.
- Variables Coolify : `DISCORD_TOKEN`, `DATABASE_URL`, `POSTGRES_PASSWORD`,
  `MESSAGE_RETENTION_DAYS`. `CLAUDE.md` prévient qu'un changement de `compose.prod.yml`
  n'est pas toujours repris au redéploiement : passer de deux à quatre services est
  précisément le cas à vérifier à la main, volume `pgdata` compris.

**Fini quand** : `docker compose up` donne quatre services sains et un message réel
arrive en base ; la CI est verte sur `develop`.

## Découpage en branches

- `init-claude-md` → `develop` : la spec, ce plan, `CLAUDE.md`. Docs seules.
- `feature/core-schema` : étapes 0 à 2.
- `feature/bot-ingestion` : étapes 3 et 4.
- `feature/ingest-ops` : étapes 5 à 7.

## Ce que ce plan ne fait pas

Ni vocal (morceau G), ni LLM (C, D, E), ni dashboard (B). `GET /api/ingest/status` est un
point de recette, pas l'API du dashboard : le morceau B définira la sienne à partir de
`daily_activity`.

## Points à trancher pendant l'implémentation, pas avant

- Le repli 3.13 s'il reste nécessaire : deux toolchains ou tout le dépôt en arrière. La
  moitié « bibliothèque » du risque étant levée, ce point ne se posera plus que si la
  connexion gateway elle-même échoue sous 3.14 — nettement moins probable.
- La timezone de `daily_activity.date` : UTC pour le socle, arbitrage définitif au
  morceau B, l'agrégat restant recalculable sur la fenêtre de rétention.
