# Bot Discord — design du socle (morceau A)

- **Date** : 2026-09-08
- **État** : **complet, en attente de relecture**. Sections 1 et 2 validées. Section 3
  validée le 2026-09-08 : la question « base indisponible » est tranchée (fast fail).
  Section 4 rédigée le 2026-09-08.
- **Plan d'implémentation** : `docs/superpowers/plans/2026-09-08-bot-discord-socle-plan.md`
  (rédigé le 2026-09-08, à valider). Il ordonne ces décisions sans en rejuger aucune.

## Contexte

Le dépôt est aujourd'hui un squelette : une API Litestar servant `/api/hello` et
`/api/health`, une page Svelte, aucune base de données, aucun code Discord. La demande
initiale portait sur un agent Discord complet (voir « Périmètre global » ci-dessous),
ce qui représente six sous-projets et non un seul. Ce document ne couvre que le
premier, le socle.

Contraintes venant de l'utilisateur : la couche LLM utilisera **LangChain et LiteLLM**
(pertinent pour les morceaux C, D, E — pas pour le socle).

## Périmètre global et découpage

Chaque morceau reçoit son propre cycle spec → plan → implémentation.

| # | Morceau | Dépend de | État |
|---|---------|-----------|------|
| A | Socle : worker Discord + Postgres + ingestion messages | — | **ce document** |
| B | Dashboard de statistiques (front + agrégats) | A | à faire |
| C | Agent LLM mentionnable, permissions héritées de l'appelant, outils | A | à faire |
| D | Règles éditables (prompt système versionné, CRUD) | C | à faire |
| E | Commandes TLDR (conversation, YouTube, arXiv, article) | A, C | à faire |
| F | Automatismes : accueil, rappel de présentation, auto-bump, page arXiv | A, D | à faire |
| G | Suivi du temps passé en vocal | A | à faire (sorti du socle) |

Le vocal a été **volontairement sorti du socle** : c'était un « si possible » dans la
demande, et son piège principal (un redémarrage du bot laisse des sessions vocales
ouvertes qu'il faut réconcilier au démarrage) mérite sa propre spec.

L'idée directrice du morceau C, à ne pas perdre : **l'agent ne peut faire que ce que
peut faire la personne qui le mentionne**. Les permissions sont héritées de l'appelant,
ce qui borne les dégâts d'une injection de prompt. C'est la brique de sécurité dont
dépendent toutes les capacités d'écriture de l'agent, et elle se conçoit avant la
première action.

## Section 1 — Découpage des process et propriété des données (validée)

Quatre process, dont trois nouveaux :

```
web   nginx      : bundle Svelte + proxy /api            (existant)
api   Litestar   : lit la base, sert /api                (existant, + DB)
bot   discord.py : écoute la gateway, écrit en base      (nouveau, 1 réplica)
db    Postgres                                            (nouveau)
```

`bot` et `api` partagent le schéma mais pas le code applicatif. Le dépôt passe donc de
`backend/` seul à trois packages Python :

- `core/` — modèles SQLAlchemy, session, migrations Alembic. Aucune dépendance à
  Litestar ni à discord.py. Seul propriétaire du schéma.
- `backend/` — l'API Litestar, dépend de `core/`. Principes inchangés : routes sous
  `/api`, `openapi.json` versionné, garde par clé d'API.
- `bot/` — le worker Discord, dépend de `core/`. Ne connaît pas Litestar.

Un seul `pyproject.toml` à la racine, avec des groupes de dépendances optionnels
(`api`, `bot`) : `Dockerfile.api` n'embarque pas discord.py, `Dockerfile.bot`
n'embarque pas Litestar, les deux copient `core/`.

**Décisions et leur raison :**

- **Base : Postgres + advanced-alchemy + Alembic.** Les agrégats du dashboard
  (messages par jour et par mois, plus tard temps vocal) sont des requêtes de
  fenêtrage, que Postgres fait bien et SQLite mal. SQLite posait en plus un problème
  de verrous en écriture avec deux process sur un même fichier.
- **Bibliothèque : discord.py.** Retenue surtout pour son modèle de permissions natif
  (`Permissions`, `Member.guild_permissions`), qui est exactement la brique dont
  l'héritage de permissions du morceau C aura besoin.
- **`bot` en un seul réplica, définitivement.** Deux instances connectées à la même
  gateway traitent chaque message deux fois. La contrainte est invisible et la
  tentation de scaler existera : elle doit être écrite dans `compose.prod.yml`.
  `WEB_CONCURRENCY` reste un levier valable pour `api` seul.
- **Healthcheck du bot par heartbeat en base**, pas par un port HTTP. Le heartbeat
  prouve à la fois que le process vit, que la connexion gateway est établie et que la
  base est joignable ; un `/health` HTTP ne prouve que le premier.

## Section 2 — Schéma et ingestion (validée)

Les identifiants Discord sont des snowflakes 64 bits : `BIGINT` partout, et ce sont les
clés primaires naturelles — pas de clé de substitution. Toute écriture devient donc
**idempotente par upsert**, propriété dont dépend tout le reste du design.

| Table | Contenu |
|---|---|
| `guild`, `channel`, `discord_user` | dimensions, mises à jour à la volée quand on les croise |
| `message` | `id`, `channel_id`, `author_id`, `created_at`, `edited_at`, `deleted_at`, `content`, `reply_to_id`, compteurs de pièces jointes |
| `reaction` | `message_id`, `emoji`, `user_id` |
| `daily_activity` | agrégat `(date, channel_id, author_id)` → nb messages, nb caractères |
| `ingest_cursor` | par canal : plus ancien et plus récent message ingéré, état de complétion |

- **Rétention.** Un job nocturne agrège dans `daily_activity` puis purge `message`
  au-delà de `MESSAGE_RETENTION_DAYS` (défaut 90). L'agrégat survit indéfiniment : le
  dashboard garde son historique sans garder les conversations. L'agrégation est
  recalculable et idempotente.
- **Suppression.** Sur `on_message_delete` : poser `deleted_at` et **vider `content`**,
  en gardant la ligne. La suppression du membre est respectée, la statistique reste
  juste.
- **Édition.** Écraser `content`, poser `edited_at`. Pas d'historique de versions.
- **Le trou de gateway.** Quand le bot est arrêté (redéploiement), il ne reçoit rien.
  C'est le même problème que le backfill, dans l'autre sens : **une seule machinerie**
  — « rattraper un canal entre deux curseurs, en respectant les rate limits,
  reprenable » — sert au backfill initial et au rattrapage au démarrage. Un composant,
  deux usages, testable seul.

### Prérequis manuels (à faire dans le portail développeur Discord)

Les intents `MESSAGE_CONTENT` et `GUILD_MEMBERS` sont **privilégiés** et doivent être
activés à la main. Sans eux, `content` arrive vide et les arrivées de membres sont
muettes — silencieusement, ce qui est le pire mode d'échec possible. Le bot doit donc
vérifier ses intents au démarrage et refuser de démarrer s'ils manquent, dans l'esprit
de `ensure_api_key_configured` qui existe déjà dans `backend/security.py`.

## Section 3 — Résilience et jobs (validée)

- **Rate limits Discord.** discord.py gère les 429 et le backoff. Reste à notre
  charge : ne pas paralléliser. Le rattrapage traite **un canal à la fois** et écrit son
  curseur après chaque page (100 messages) ; une interruption ne perd au pire qu'une
  page.
- **Base indisponible : fast fail** (tranché le 2026-09-08). Le bot ne met rien en
  tampon mémoire et ne tente pas de réessai prolongé : dès qu'une écriture échoue faute
  de base, il logue et **sort en code non nul**. Le backoff est délégué à
  l'orchestrateur (`restart: unless-stopped`), et la machinerie de rattrapage par
  curseurs recomble le trou au redémarrage — exactement comme pour un trou de gateway.
  Un seul mécanisme de reprise couvre les deux pannes, et il n'y a aucune file en
  mémoire à perdre. Corollaire assumé : une base qui bat de l'aile produit une boucle de
  redémarrages, visible dans les logs comme dans le heartbeat. C'est le comportement
  voulu, préférable à un bot vivant et silencieusement sourd. La connexion à la base est
  donc vérifiée au démarrage, à côté du contrôle des intents.
- **Événements orphelins.** Une édition ou une suppression peut arriver pour un message
  absent de la base (backfill pas encore arrivé là, ou message purgé). L'upsert couvre
  le premier cas ; le second est un **no-op explicite, pas une exception**. Cas de test
  à part entière : il se produira tous les jours après 90 jours de vie.
- **Isolation des handlers.** Une exception dans un handler ne doit pas tuer la
  connexion gateway : handler enveloppé, erreur dans structlog avec l'identifiant de
  l'événement, le bot continue. Un canal problématique ne fait pas tomber les 49 autres.
- **Jobs planifiés**, dans le process du bot via `tasks.loop` : heartbeat en base toutes
  les 30 s (ce que lit le healthcheck), agrégation + purge une fois par nuit. C'est le
  dividende de la contrainte « un seul réplica » : aucun verrou distribué, aucun
  scheduler séparé, aucun risque de double exécution. Le jour où le bot devra scaler,
  ces jobs devront déménager.

## Section 4 — Tests, configuration, déploiement (rédigée le 2026-09-08)

### Tests : deux étages, et aucun SQLite

**Décision : les tests ne touchent jamais SQLite.** Deux étages, séparés par ce que le
code testé fait réellement.

**Étage 1 — sans base du tout.** `bot/` convertit l'événement gateway en dataclass plate
à la frontière (`bot/adapters.py`), et la logique d'ingestion ne prend que ces
dataclasses : quoi upserter, ce qui est un no-op, sur quel jour tombe un message, quand
s'arrêter dans une page de rattrapage. Ce sont des fonctions pures. Ces tests ne
construisent aucun faux `discord.Message` et n'ouvrent aucune connexion — c'est cette
frontière, et non un jeu de mocks, qui rend le worker testable. Les adaptateurs restent
fins et ne sont pas testés unitairement. **C'est de cet étage que vient la rapidité, pas
du choix d'un moteur allégé.**

**Étage 2 — Postgres réel**, pour la couche qui parle SQL. Une instance par session de
tests, `alembic upgrade head` appliqué une fois, chaque test dans une transaction
annulée : pas de `TRUNCATE` entre les tests, donc pas de coût par test.

- **En CI** : un bloc `services: postgres:18` dans `ci.yml`. Le runner `ubuntu-latest` a
  déjà Docker, il n'y a donc aucune dépendance Python à ajouter.
- **En local** : le service `db` de `compose.yml`, qui doit exister de toute façon pour
  `just dev-bot`, sur une base séparée. `TEST_DATABASE_URL` (défaut
  `…/dashboard_discord_test`) pour ne jamais migrer ni vider la base de développement
  par accident.
- Ces tests portent `@pytest.mark.db`. Sans base joignable, `just test` les saute avec
  un message bruyant qui nomme la commande à lancer (`just db`) ; `just check`, la porte
  avant push, les **exige** et échoue s'ils sont sautés. Un test sauté en silence est un
  test qui n'existe pas.

**Pourquoi pas SQLite en mémoire**, qui était le premier jet de cette section : il est
faible exactement là où l'étage 2 sert. Quatre pannes qu'il ferait passer au vert :

- **Les timestamps.** SQLite n'a pas de type tz-aware : l'offset est perdu au stockage et
  la valeur revient naïve. Or les snowflakes Discord donnent de l'UTC aware, et la purge
  de rétention comme la découpe par jour sont des comparaisons de datetimes.
- **`ON CONFLICT` sur clé composite ou index unique partiel** — dialecte Postgres
  uniquement, et `reaction` a une clé composite.
- **Index partiel `WHERE deleted_at IS NULL`, `date_trunc`, fonctions de fenêtrage** :
  c'est-à-dire précisément ce pour quoi la section 1 a écarté SQLite en production.
- **Les migrations elles-mêmes.** Avec un vrai Postgres, `alembic upgrade head` en début
  de session les teste gratuitement ; avec SQLite, dont le DDL diffère, elles ne
  s'exécuteraient pour la première fois qu'au déploiement.

À quoi s'ajoutait un effet de bord pire que les quatre : les garde-fous nécessaires pour
maintenir SQLite honnête (un helper d'upsert dialect-aware, l'interdiction du SQL propre
à un moteur) sont une taxe qui s'érode. La première personne qui écrit
`postgresql.insert()` directement, parce que c'est la chose naturelle à écrire, casse
l'équivalence sans qu'aucun signal ne se déclenche.

Deux conséquences positives à ne pas laisser filer :

- **`postgresql.insert().on_conflict_do_update()` s'écrit directement**, sans couche
  d'abstraction de dialecte. Un seul module de `core/` reste malgré tout le lieu des
  upserts, pour la lisibilité, plus par hygiène que par contrainte.
- La découpe par jour reste calculée **en Python** avant l'insert dans `daily_activity` —
  non plus faute de `date_trunc`, mais parce que l'agrégation doit être recalculable et
  rejouable. La timezone de cette découpe est une vraie décision (une soirée française
  déborde sur le lendemain en UTC) : `daily_activity.date` est en **UTC** pour le socle,
  documenté comme tel, et l'arbitrage définitif appartient au morceau B — l'agrégat
  étant recalculable depuis `message` sur la fenêtre de rétention, le choix n'est pas
  scellé.

Piège d'implémentation de l'étage 2 : la transaction annulée par test suppose une session
**greffée sur une transaction externe**. Avec SQLAlchemy async, c'est
`AsyncConnection.begin()` puis un `async_sessionmaker(bind=conn,
join_transaction_mode="create_savepoint")`. Sans ce paramètre, un `commit()` du code
testé valide pour de bon et les tests se contaminent dans un ordre difficile à
reproduire.

Cas de test à ne pas oublier, tous déjà nommés en sections 2 et 3 : réingestion du même
message (idempotence), suppression qui vide `content` en gardant la ligne, édition
orpheline en no-op, exception dans un handler qui n'interrompt pas la boucle, reprise du
rattrapage depuis un curseur au milieu d'un canal. Les deux premiers et le dernier sont
de l'étage 2, les autres de l'étage 1.

Deux réglages à étendre, sinon les nouveaux packages sont ignorés **en silence** — c'est
exactement le piège déjà documenté dans `CLAUDE.md` :

- `addopts` et `[tool.coverage.run]` : `--cov=backend --cov=core --cov=bot`.
- `[tool.pyrefly] project-includes` : ajouter `core` et `bot`, faute de quoi pyrefly
  annonce « 0 errors » sans avoir lu une ligne du worker.

### Configuration et secrets

Une seule variable de connexion, `DATABASE_URL`, en driver async
(`postgresql+asyncpg://`). L'`env.py` d'Alembic tourne en mode async pour lire la même
URL : maintenir une URL sync pour les migrations et une async pour l'application,
c'est garantir qu'elles divergent un jour.

`TEST_DATABASE_URL` est son équivalent pour l'étage 2 des tests. Son défaut porte un nom
de base distinct (`dashboard_discord_test`) et ne retombe **jamais** sur `DATABASE_URL` :
un défaut trop serviable ferait migrer et vider la base de développement au premier
`pytest`.

`DISCORD_TOKEN` suit le régime d'`API_KEY` : **échec en fermé**. `${DISCORD_TOKEN:?}`
dans les deux composes (une valeur absente bloque le déploiement au lieu de démarrer un
bot muet), déclarée sans valeur dans `.env.example`, vérifiée au démarrage à côté du
contrôle des intents. Le token n'apparaît dans aucun log : pas de log de l'objet de
configuration, et l'erreur de démarrage nomme la variable, jamais sa valeur. Il n'entre
évidemment jamais dans le bundle — aucun `VITE_*`.

`MESSAGE_RETENTION_DAYS` (défaut 90) et l'heure du job nocturne sont des variables
d'environnement, pas des constantes : la rétention est une décision de politique, pas un
choix technique.

### Déploiement

`db` devient un service dans les deux composes : `postgres:18-alpine`, volume nommé
`pgdata`, healthcheck `pg_isready`, `POSTGRES_PASSWORD` en `${...:?}`. `api` et `bot`
attendent `db: condition: service_healthy`. En production, `db` n'a **ni `ports:` ni
domaine** : comme `api`, il reste privé au réseau du projet.

**Les migrations tournent à l'entrée de l'image `api`**, `alembic upgrade head` avant
Granian. Un seul migrateur, ordre garanti sans coordination, rien à lancer à la main sur
Coolify. `bot` dépend de `api` en `service_healthy`, donc il démarre sur un schéma à
jour. Trois corollaires :

- Une migration qui échoue empêche `api` de démarrer, et donc `bot` aussi. C'est voulu :
  un worker qui écrit dans un schéma périmé corrompt les données.
- Avec `WEB_CONCURRENCY > 1`, l'entrypoint doit tourner **une fois** avant les workers
  Granian, pas une fois par worker. À vérifier plutôt qu'à supposer.
- Les migrations doivent rester compatibles avec la version précédente du code le temps
  du redéploiement : pas de `DROP COLUMN` dans la même release que l'arrêt de son usage.

`bot` déclare `restart: unless-stopped` — c'est lui qui fournit le backoff du fast fail
de la section 3 — et **aucun `replicas`** ; le commentaire du service doit porter la
raison, deux instances traitant chaque message deux fois.

`Dockerfile.bot` est un troisième Dockerfile : il copie `core/` et `bot/` et installe le
groupe de dépendances `bot`. Ni Litestar, ni nginx, ni `frontend/`. Son healthcheck lit
le heartbeat en base (section 1), pas un port HTTP.

`compose.prod.yml` reste la source de vérité côté Coolify, et `CLAUDE.md` rappelle qu'un
changement de ce fichier n'est pas toujours repris au redéploiement : le passage de deux
à quatre services est précisément le genre de modification à vérifier à la main.

### Extension du justfile

`just dev` reste **API + Vite**. Travailler sur le front n'exige pas de token Discord et
ne doit pas ouvrir une vraie connexion gateway.

```
just db                  # démarre le service db seul (docker compose up -d db)
just migrate             # alembic upgrade head
just migration m="..."   # alembic revision --autogenerate
just dev-bot             # le worker seul, sur la base locale
just dev-all             # api + vite + bot, pour la stack complète
```

`just test` reste lançable sans Docker : l'étage 1 tourne, l'étage 2 est sauté avec un
message qui nomme `just db`. `just check` en revanche **exige la base** et échoue si des
tests marqués `db` sont sautés — la porte avant push ne doit pas pouvoir passer au vert
en n'ayant testé que la moitié. Un `just db` en dépendance de `just check` rend le cas
courant indolore.

Côté CI, `ci.yml` gagne un bloc `services:` sur `postgres:18` et la variable
`TEST_DATABASE_URL` dans son `env:`. C'est le seul changement du workflow : `just check`
reste la commande unique qu'il appelle.

### Ce que l'API doit exposer pour prouver que l'ingestion fonctionne

Un seul endpoint suffit, et il constitue le critère de recette du morceau A :
`GET /api/ingest/status` → par canal, les deux curseurs, l'état de complétion, le nombre
de messages en base, et la date du dernier heartbeat du bot.

- Réponse annotée par une dataclass ou un `msgspec.Struct`, jamais un `dict[str, str]` —
  sinon `openapi.json` ne décrit rien (piège documenté dans `CLAUDE.md`). `just
  check-types` doit être relancé et `openapi.json` committé.
- Protégé par `require_api_key`, et déclarant `security=[{"APIKey": []}]`, sans quoi le
  schéma le présente comme libre d'accès.
- **`/api/health` ne touche pas à la base.** Le healthcheck compose s'en sert pour savoir
  si l'API répond ; le lier à Postgres ferait redémarrer une API saine parce que la base
  est lente. La santé de l'ingestion est une autre question, servie par la route
  ci-dessus.

## Risque technique à vérifier en premier

`discord.py` 2.7.1 déclare `requires_python >=3.8` mais ne publie de classifiers que
jusqu'à Python 3.12, alors que le projet impose 3.14 (`pyproject.toml`,
`.python-version`). Ça marchera probablement — les classifiers sont souvent en retard —
mais ce n'est pas certifié. **Première étape du plan d'implémentation : installer
discord.py sous 3.14 et ouvrir une connexion gateway.** Si ça casse, l'arbitrage retenu
est d'épingler le worker sur Python 3.13 dans sa propre image, pas de changer de
bibliothèque.

## Alternatives écartées

- **Bot dans le process Litestar** (`on_startup`) : imposerait `WEB_CONCURRENCY=1` pour
  toujours, donc plus de dimensionnement vertical de l'API, et un crash du bot
  emporterait l'API.
- **Bot appelant l'API en HTTP** au lieu d'écrire en base : chaque message ingéré
  devient un appel HTTP, et il faut authentifier le bot comme un client tiers.
- **Métadonnées seules, sans contenu** : exposition minimale, mais chaque TLDR devrait
  relire l'historique via l'API Discord (rate limits, lenteur) et aucune recherche ne
  serait possible.
- **Archive complète sans purge** : conserverait indéfiniment les conversations privées
  des membres.
- **SQLite en mémoire pour les tests** : `just check` sans Docker, mais au prix d'une
  suite de tests qui n'exerce pas le SQL de production (timestamps tz-aware, `ON
  CONFLICT` sur clé composite, index partiels, migrations jamais jouées) et de
  garde-fous de dialecte qui s'érodent silencieusement. Détail du raisonnement en
  section 4 — c'est le seul arbitrage de ce document à avoir été retourné après coup.
