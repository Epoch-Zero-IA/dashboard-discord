# Bot Discord — design du socle (morceau A)

- **Date** : 2026-09-08
- **État** : **incomplet**. Sections 1 et 2 validées. Section 3 présentée, **non validée**
  (une question ouverte, voir plus bas). Section 4 (tests, configuration, déploiement)
  pas encore rédigée.
- **Suite prévue** : terminer la section 4, faire valider, puis passer à la skill
  `writing-plans` pour le plan d'implémentation.

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

## Section 3 — Résilience et jobs (présentée, NON validée)

- **Rate limits Discord.** discord.py gère les 429 et le backoff. Reste à notre
  charge : ne pas paralléliser. Le rattrapage traite **un canal à la fois** et écrit son
  curseur après chaque page (100 messages) ; une interruption ne perd au pire qu'une
  page.
- **Base indisponible — QUESTION OUVERTE.** Proposition : le bot ne met rien en tampon
  mémoire ; il réessaie avec un backoff borné puis **sort en code non nul**.
  L'orchestrateur le redémarre et la machinerie de rattrapage recomble le trou.
  L'argument est que la reprise existe déjà, donc la panne de base n'a pas besoin de son
  propre mécanisme et il n'y a pas de file en mémoire à perdre. **L'accord explicite de
  l'utilisateur sur ce point n'a pas été obtenu** — c'est la question à reposer avant
  d'aller plus loin.
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

## Section 4 — Tests, configuration, déploiement

**Pas encore rédigée.** À couvrir : stratégie de test du worker (base Postgres jetable
ou transactions annulées, faux objets discord.py), gestion du token Discord comme
secret, service `db` et volume dans `compose.yml` et `compose.prod.yml`, exécution des
migrations Alembic au déploiement, extension du `just` existant (`just dev` doit-il
lancer le bot ?), et ce que l'API expose au minimum pour prouver que l'ingestion
fonctionne.

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
