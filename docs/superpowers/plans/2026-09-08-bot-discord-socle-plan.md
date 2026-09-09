# Bot Discord — plan d'implémentation du socle (morceau A)

- **Date** : 2026-09-08
- **État** : **les huit étapes sont écrites** (branche `feature/core-schema`). Chaque
  étape porte son bilan : les écarts au plan, ce que l'écriture a appris, et ce qui n'a
  pas pu être vérifié.
- **Ce qui reste dû, et que rien ne remplace** : aucune connexion gateway n'a été
  ouverte (il faut un `DISCORD_TOKEN` et un serveur de test), et la migration n'a jamais
  été appliquée à un vrai Postgres — les 32 tests marqués `db` sont écrits et sautés.
  `just db && just check` sur une machine avec Docker lève les deux, et la CI le fera au
  premier push.
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

**Connexion gateway vérifiée le 2026-09-09**, avec un token réel et une sonde jetable
hors du dépôt. Le websocket s'ouvre sous CPython 3.14.7, `on_ready` se déclenche,
`application_info()` répond : **le risque technique n° 1 de la spec est clos.**

Et la configuration réelle a confirmé le piège des deux drapeaux, ce qui n'était jusque-là
qu'une lecture de `dir()` :

```
message_content         : False
message_content_limited : True
guild_members           : False
guild_members_limited   : True
=> contrôle de démarrage du worker : PASSE
```

Les deux intents sont bien activés dans le portail, et Discord ne les expose **que** sur
les drapeaux `_limited`. Un contrôle lisant les seuls drapeaux validés aurait donc refusé
de démarrer en accusant le portail à tort, et il aurait échoué en fermé — de la façon la
plus convaincante possible. C'est exactement ce que le `or` de
`missing_privileged_intents` et son test couvraient.

Reste dû, faute d'invitation au moment du test : **l'arrivée d'un message avec `content`
non vide**. Le drapeau annonce l'intent actif, mais seul un contenu lu le prouve.

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

**Fait le 2026-09-08.** Trois écarts au plan ci-dessus, tous constatés en écrivant :

- La fixture qui migre est **synchrone**. L'`env.py` d'Alembic appelle `asyncio.run`,
  qui lève depuis une boucle déjà en cours : la version async de cette fixture, celle
  qui a l'air naturelle, ne peut pas marcher.
- `--cov=bot` et `bot` dans `project-includes` attendent l'étape 3. Nommer un package
  qui n'existe pas encore n'achète qu'un avertissement de coverage à chaque run.
- `testpaths = ["tests"]` s'ajoute : pytest partait de la racine, ramassait un second
  checkout du projet posé à côté, et ses modules entraient en collision de basename
  avec les vrais. La collecte méritait d'être bornée de toute façon.

La vérification qui reste due : aucune base n'était joignable dans l'environnement où
l'étape a été écrite. Les deux tests solidaires du harnais sont **sautés, pas verts**.

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

**Fait le 2026-09-08.** Ce que l'écriture a changé ou appris :

- **Les dataclasses plates vivent dans `core/records.py`**, pas dans `bot/events.py`
  comme l'annonçait l'étape 3. Elles sont le type d'argument de `core/upserts.py` : les
  laisser dans `bot/` obligerait `core` à dépendre de `bot`, ou à redéclarer les mêmes
  huit champs de l'autre côté de la frontière. L'étape 3 garde `bot/adapters.py`, qui
  convertit désormais vers ces records.
- **`reply_to_id` n'est pas une clé étrangère.** Une réponse peut viser un message plus
  ancien que le backfill, ou déjà purgé ; une contrainte rejetterait la réponse au lieu
  de la seule chose qu'on ne puisse pas réparer.
- **L'upsert de message porte `WHERE deleted_at IS NULL`.** Sans lui, un rattrapage qui
  relit un message déjà supprimé ressuscite son contenu.
- **La convention de nommage de `core.db.metadata` s'applique aussi aux opérations
  Alembic.** Un `name="ck_bot_heartbeat_single_row"` explicite dans la migration
  ressortait en `ck_bot_heartbeat_ck_bot_heartbeat_single_row` : il faut donner le nom
  nu, comme le modèle.
- **Un test referme un trou que la section 4 de la spec déclarait ouvert.** Les deux DDL
  — celui des modèles via un mock engine, celui des migrations via le mode `--sql`
  d'Alembic — se rendent hors base et se comparent. Un modèle modifié sans migration
  échoue donc dans la suite rapide, sans Docker
  (`tests/test_migrations_match_models.py`), et le test a été vérifié par mutation. Ce
  qu'il ne prouve pas : que ce SQL s'exécute.

Même réserve qu'à l'étape 1 : les onze tests d'upsert sont écrits et **sautés**, faute
de base. La migration n'a jamais été appliquée à un vrai Postgres — seulement rendue.

## Étape 3 — Le worker : frontière, handlers, heartbeat, fast fail

- `bot/adapters.py` : conversion depuis les objets discord.py vers les records de
  `core/records.py`, et **rien d'autre**. C'est la frontière dont dépend tout l'étage 1
  des tests : rien en aval ne voit un `discord.Message`.
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

**Écrit le 2026-09-08, pas encore branché.** Le troisième critère ci-dessus est vérifié ;
les deux premiers demandent un token et une base, donc restent dus. Ce que l'écriture a
ajouté au plan :

- **`bot/failures.py`, un module que le plan ne prévoyait pas.** Le fast fail de la
  section 3 n'est juste que pour une vraie panne : une contrainte violée remonte aussi
  du pilote, et sortir dessus donnerait une boucle de redémarrages qui n'ingère rien et
  ressemble trait pour trait à une panne. Une panne tue le process (code 1), un bug est
  loggué et le worker continue, une erreur de configuration sort en 2 — un redémarrage
  ne la corrigera jamais. C'est la classification, pas le `close()`, qui était le point
  délicat de cette étape.
- **L'isolation des handlers n'est donc pas générale.** Le décorateur avale tout sauf la
  panne de base, laissée remonter jusqu'à `Client.on_error`, où le runner ferme
  proprement. discord.py n'arrête pas le client sur une exception de handler : sans ce
  chemin, un `raise` ne servirait à rien.
- **Les événements d'édition, de suppression et de réaction sont les variantes `raw`.**
  Les versions cachées ne se déclenchent que pour les messages encore en mémoire —
  c'est-à-dire aucun de l'historique après un redémarrage, exactement les messages que
  ce projet archive.
- **Le contrôle des intents prend quatre booléens, pas un objet.** discord.py implémente
  ces drapeaux comme ses propres descripteurs, qu'aucun `Protocol` d'attributs simples
  ne satisfait ; pyrefly l'a refusé. Les quatre lectures se font à la frontière, dans
  `runner.py`, ce qui laisse la décision pure et testée — dont le cas `_limited`.
- `bot/ingest.py` n'existe pas : entre `core/upserts.py` et les handlers, il n'avait
  aucune décision à porter. `bot/events.py` non plus, les records étant dans `core/`.

`runner.py` et `__main__.py` sont à 0 % de couverture, par construction : ce sont les
deux seuls modules qui ne décident rien.

## Étape 4 — Le rattrapage, un composant pour deux usages

Backfill initial et trou de gateway sont le même problème dans deux sens : un seul
composant, `bot/catchup.py`.

- Un canal à la fois, pages de 100, `ingest_cursor` écrit **après chaque page** : une
  interruption ne perd qu'une page. Pas de `asyncio.gather` — c'est notre seule
  protection contre les rate limits, discord.py gérant déjà les 429.
- La source des messages est derrière un protocole étroit (`MessageSource`), que les
  tests remplacent par une liste. La décision « où reprendre » redevient une fonction
  pure.
- Appelé au démarrage (rattrapage du trou) et par une sous-commande (backfill initial).

**Fini quand** : reprise depuis un curseur au milieu d'un canal, sans trou ni doublon
(étage 1) ; un rattrapage rejoué deux fois est idempotent (étage 2) ; et en réel :
arrêter le bot, poster cinq messages, redémarrer, retrouver les cinq.

**Écrit le 2026-09-08.** Les douze tests de l'étage 1 passent ; les quatre de l'étage 2
— dont la reprise au milieu d'un canal et le rejeu idempotent — sont écrits et sautés,
faute de base. Le critère réel reste dû. Trois choses que l'écriture a précisées :

- **La première page en arrière ancre aussi le curseur avant.** Sans cela, un trou de
  gateway ne saurait pas où finit l'historique connu, et `next_request` en direction
  avant renverrait `None` pour toujours. C'est le genre de détail qui ne se voit qu'au
  deuxième redéploiement.
- **Un rattrapage en avant sans ancre ne demande rien**, plutôt que de partir du début
  des temps : « tout l'historique » est le travail du backfill, pas d'un trou.
- **Le curseur est écrit dans la même transaction que la page qu'il décrit.** Le plan
  disait « après chaque page », ce qui laissait la porte ouverte à deux transactions et
  donc à un curseur en avance sur les données. Une seule transaction : soit les deux,
  soit ni l'un ni l'autre.

Deux ajouts hors plan, tous deux du côté I/O : `bot/history.py` implémente
`MessageSource` sur `channel.history()` — un canal illisible y devient une page vide,
pas une erreur, sinon un canal sans droits arrêterait le rattrapage des autres — et
`python -m bot backfill` (recette `just backfill`) fait tourner la même machine sans
budget de pages, pour l'import initial. Le rattrapage au démarrage, lui, est branché
sur `on_ready` et non `setup_hook` : le cache des canaux n'existe qu'après l'événement
READY, et `on_ready` refirant à chaque reconnexion, un garde empêche deux rattrapages
concurrents de se disputer le même curseur.

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

**Écrit le 2026-09-08.** Les cinq tests de l'étage 1 passent ; les neuf de l'étage 2,
qui portent les trois critères ci-dessus, sont écrits et sautés. Ce que l'écriture a
ajouté :

- **Réagréger un jour déjà purgé ne remet pas ses compteurs à zéro.** L'`INSERT …
  SELECT` n'a plus rien à compter et n'écrit donc rien, l'agrégat survit intact. C'est
  cette propriété qui rend sûre la réagrégation juste avant la purge — laquelle rattrape
  un jour dont l'agrégation avait échoué la nuit d'avant, et qui serait sinon refusé par
  `purge_day` pour toujours, les messages s'empilant.
- **Un message supprimé compte toujours comme message mais n'apporte plus de
  caractères.** Le contenu est parti : un jour recalculé après une suppression perd ses
  caractères. C'est le prix d'honorer la suppression, et il est explicite plutôt que
  découvert dans six mois sur un graphique.
- **Un budget de sept jours par exécution.** Une première nuit sur une base plus vieille
  que la fenêtre de rétention supprimerait des mois en une transaction et tiendrait la
  table plusieurs minutes. `days_to_purge` est la fonction pure de cette étape, et le
  reste part la nuit suivante.
- **`NIGHTLY_HOUR_UTC`** rejoint `MESSAGE_RETENTION_DAYS` : l'heure est lue au démarrage
  et non à l'import, `tasks.loop` évaluant son décorateur à la définition de la classe —
  ce qui figerait l'heure avant tout chargement d'environnement.

Nettoyage au passage : la fermeture sur panne de base était dupliquée trois fois dans le
runner. Elle devient `_abort_if_database_gone`, appelée par les handlers, le heartbeat,
le rattrapage et le job nocturne — un seul endroit où la règle de la section 3 est
écrite.

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

**Fait le 2026-09-08.** Les trois critères sont tenus, et le contrat a pu être vérifié
malgré l'absence de `pnpm` dans l'environnement : `litestar assets generate-types` écrit
`openapi.json` avec `json.dumps(schema, indent=2, sort_keys=True)` et un saut de ligne
final, ce qui se reproduit en Python pur — vérifié **octet pour octet** contre le fichier
committé avant modification, puis utilisé pour le régénérer. `just check-types` sur une
machine avec pnpm reste la référence.

- **Le piège du `dict[str, str]` est devenu un test.** Il affirme sur `openapi.json` que
  la réponse pointe sur `IngestStatus`, que les champs y sont, et que `security` est
  déclaré — sans quoi le schéma présenterait la route comme libre d'accès. Cela ferme la
  boucle du garde-fou que `CLAUDE.md` ne faisait que documenter.
- **La session est injectée sur l'`api_router`, pas sur le contrôleur.** `/api/health`
  n'en demande aucune et ne doit surtout pas en dépendre : le lier à Postgres ferait
  redémarrer une API saine chaque fois que la base est lente.
- **Litestar dépréciait l'inférence du paramètre de dépendance** (« stop working in
  3.0 ») ; il est annoté `NamedDependency[AsyncSession]`, et la suite passe désormais
  sous `-W error::DeprecationWarning`.
- **Un test couvre le piège classique de la double jointure externe** : deux canaux de
  deux messages qui en rapporteraient quatre chacun.

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

**Écrit le 2026-09-08, non exécuté.** Aucun des deux critères ne peut l'être ici : pas
de Docker, pas de token. Les fichiers sont validés autant qu'ils pouvaient l'être — YAML
parsé, quatre services présents dans les deux composes, aucun port ni `replicas` en
production, `deploy/api-entrypoint.sh` committé en `100755` (un COPY Docker conserve le
mode du contexte : sans le bit exécutable, l'`ENTRYPOINT` échoue au premier démarrage).

- **Le healthcheck du worker est un module, pas une ligne de shell.**
  `python -m bot.healthcheck` lit le heartbeat et tolère trois battements manqués. Sa
  décision est une fonction pure, testée. Le `except Exception` de son `main` est
  volontairement aveugle : vu de l'extérieur, un worker qui n'atteint pas sa base est
  indiscernable d'un worker arrêté, et les deux veulent un redémarrage — narrower, une
  exception imprévue ferait planter le contrôle lui-même, rapporté comme erreur de
  healthcheck plutôt que comme conteneur malsain.
- **Le `set -e` de l'entrypoint est structurant**, pas de la prudence : une migration qui
  échoue doit arrêter le conteneur plutôt que servir contre un schéma qui ne correspond
  pas. Le healthcheck ne passant alors jamais, `depends_on` retient `web` et `bot`, et le
  déploiement remonte l'échec au lieu de servir une application cassée.
- **La CI est ce qui exécutera la couche SQL pour la première fois.** Le service
  `postgres:18-alpine` et `TEST_DATABASE_URL` y rendent les tests marqués `db` exigés :
  c'est ce run qui appliquera la migration et lancera les 32 tests que l'environnement
  local ne pouvait pas lancer. Attendez-vous à y corriger quelque chose.
- Reste à vérifier à la main, comme le prévoyait le plan : que l'entrypoint tourne bien
  **une fois** et non une fois par worker avec `WEB_CONCURRENCY > 1`, et que Coolify
  reprenne le passage de deux à quatre services, volume `pgdata` compris.

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
