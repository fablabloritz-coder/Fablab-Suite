# FabLab Suite

Suite d'applications Flask/SQLite pour piloter un FabLab avec un déploiement Docker simple.

Ce monorepo regroupe :
- FabHome : hub central et tableau de supervision
- Fabtrack : machines, consommations, stock, missions
- PretGo : gestion des prêts de matériel
- FabBoard : affichage TV / dashboard visuel
- Outils d'installation : `fabsuite-ubuntu.sh` + `fabsuite_ssh_gui.py`

## Objectif

Installer rapidement une suite complète, préconfigurée pour que les applications communiquent entre elles, sans dépendre de configurations locales personnelles.

## Architecture

- Backend : Flask + SQLite
- Frontend : Bootstrap 5 + JavaScript vanilla
- Déploiement : Docker Compose
- Inter-apps : endpoints FabSuite (`/api/fabsuite/*`)

Structure principale :

- `FabHome/`
- `Fabtrack/`
- `PretGo/`
- `FabBoard/`
- `docker-compose.yml` (lancement global local)
- `.env.example` (variables de base)
- `fabsuite-ubuntu.sh` (installation et maintenance serveur)
- `fabsuite-ubuntu.env.example` (config serveur)
- `fabsuite_ssh_gui.py` (assistant SSH graphique — interface Eel/Bootstrap)
- `web/` (interface HTML/CSS/JS du GUI)
- `INSTALL_UBUNTU.md` (guide serveur pas à pas)
- `docs/APPLICATIONS.md` (détail métier par application)
- `deploy_core/` (scaffold moteur de workflow unifié)

## Installation recommandée (méthode unique)

Lancer le GUI depuis Windows (PowerShell) — utilise le cache local, télécharge si absent :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Invoke-RestMethod 'https://raw.githubusercontent.com/fablabloritz-coder/Fablab-Suite/main/run_fabsuite_ssh.ps1')))"
```

Forcer la mise à jour vers la dernière version (ajouter `-Update`) :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Invoke-RestMethod 'https://raw.githubusercontent.com/fablabloritz-coder/Fablab-Suite/main/run_fabsuite_ssh.ps1'))) -Update"
```

Cette commande :
- Télécharge le GUI et ses fichiers dans `%LOCALAPPDATA%\FabSuite\ssh-gui`
- Réutilise le cache si déjà présent (évite le téléchargement à chaque lancement)
- Prépare automatiquement un workspace monorepo pour le mode local (`%LOCALAPPDATA%\FabSuite\workspace`) si absent
- `-Update` force le re-téléchargement depuis GitHub (nouvelle version)
- Lance l'interface graphique (Edge/Chrome en mode app)
- Ferme automatiquement le terminal quand le GUI est fermé

## Utilisation du GUI

Le GUI s'ouvre dans Edge ou Chrome en mode application (sans barre de navigation).

### Mode Local

Gestion Docker Compose depuis votre poste Windows :

- Aucun serveur SSH requis : les actions locales sont utilisables meme sans connexion distante.
- Prerequis : Docker Desktop doit etre installe et demarre (moteur Docker actif).
- Sur Installer/Mettre a jour, la GUI verifie Docker et supprime automatiquement les conteneurs conflictuels (`fabhome`, `fabtrack`, `pretgo`, `fabboard`) avant relance.

1. **Audit** — vérifie l'état des services locaux
2. **Installer** — `docker compose up -d --build`
3. **Mettre à jour** — `git pull --ff-only` puis rebuild (`docker compose up -d --build`)
4. **Redémarrer** — relance rapide sans pull Git ni rebuild (`docker compose restart`)
5. **Status** — état en cours de chaque conteneur
6. **Logs** — logs de toutes les apps ou d'une app spécifique

### Serveur SSH

Déploiement et maintenance d'un serveur Ubuntu distant :

**Connexion** : saisir `user@host`, mot de passe (ou clé SSH via Options avancées)

**① Préparation**
1. Upload fichiers — envoie les scripts d'installation sur le serveur
2. Audit serveur — diagnostique Docker, projets, inter-apps
3. Cleanup Docker — sauvegarde puis nettoie les conteneurs FabSuite
4. Préparer hôte — installe Docker + Compose + Git sur Ubuntu

**② Déploiement**
1. Repair env — corrige/crée `fabsuite-ubuntu.env`
2. Data safety — vérifie les risques avant migration
3. Installer — déploiement complet de la suite
4. Mettre à jour — sync GitHub + rebuild des apps

**③ Monitoring**
- Redémarrer (sans pull/rebuild), Status, Logs (toutes apps ou app spécifique)

**Sauvegarde & restauration**
- **Sauvegarder** : lance `backup` via le helper (snapshot vérifié, rotation incluse)
- **Restaurer un snapshot** : liste les snapshots valides du serveur, puis lance
  `restore` sur celui choisi. Le modal liste aussi les volumes du snapshot
  (`Fabtrack/data`, `FabHome/icons`…) : sans sélection, tout est restauré ;
  sinon seuls les volumes choisis sont transmis au helper via `--source`.
  Sans destination saisie, le helper utilise son staging par défaut
  (`FABSUITE_RESTORE_DIR` ou `~/restore-fabsuite/<snapshot>`). Une destination
  explicite doit être un dossier vide ; rien n'est écrasé en place.

**Maintenance dossiers** : scan, inspection, correction permissions, archivage, suppression

## Méthodes alternatives (avancées)

### Démarrage rapide local (dev)

```bash
cp .env.example .env
docker compose up -d --build
```

Accès local :
- FabHome : `http://localhost:3001`
- Fabtrack : `http://localhost:5555`
- PretGo : `http://localhost:5000`
- FabBoard : `http://localhost:5580`

### Installation serveur Ubuntu manuelle

Guide complet : `INSTALL_UBUNTU.md`

```bash
scp fabsuite-ubuntu.sh fabsuite-ubuntu.env.example INSTALL_UBUNTU.md user@SERVER_IP:~/fabsuite-installer/
ssh user@SERVER_IP
cd ~/fabsuite-installer
chmod +x fabsuite-ubuntu.sh
./fabsuite-ubuntu.sh prepare-host
cp fabsuite-ubuntu.env.example fabsuite-ubuntu.env
nano fabsuite-ubuntu.env
./fabsuite-ubuntu.sh install
```

### Lancement manuel du GUI depuis un clone local

```bash
pip install eel paramiko
python fabsuite_ssh_gui.py
```

## Notes de version (popup « Nouveautés »)

Chaque application affiche une popup des dernières évolutions, pilotée par `static/release-notes.json`
(FabHome, Fabtrack, PretGo, FabBoard, FabInventory). Le fichier contient un tableau `history`, version la
plus récente en tête :

```json
{
  "history": [
    {
      "version": "2.7.0",
      "date": "2026-11-14",
      "title": "Garde-fou contre les doubles saisies",
      "notes": ["Une saisie identique a moins de deux minutes est signalee."]
    }
  ],
  "_source": "manuel"
}
```

La popup s'affiche une fois par signature d'historique (clé `localStorage` propre à chaque application,
par exemple `fabtrack:last_seen_release`) et reste consultable via le lien « Nouveautés » du pied de page.
Une entrée peut porter une clé `url` vers sa release GitHub (`releases/tag/<app>-vX.Y.Z`) ; la popup
n'affiche « Voir la release » que si ce champ existe. Le générateur le renseigne pour la version
publiée et pour celles qui ont un tag local, et `--sync-release-links` resynchronise un historique
existant, de sorte qu'aucune version sans tag ne produise de lien mort.
Les notes sont injectées en HTML : le gras `<strong>` est utilisé dans PretGo. L'historique est plafonné
à 30 entrées par le générateur.

### Convention de tags

Les tags sont **préfixés par l'application**, car le dépôt héberge plusieurs logiciels qui numérotent
leurs versions indépendamment (`v1.7.1` de FabHome et `v1.10.1` de PretGo ne doivent pas se confondre) :

| Application | Préfixe | Exemple |
|---|---|---|
| Fabtrack | `fabtrack-v` | `fabtrack-v2.7.0` |
| FabBoard | `fabboard-v` | `fabboard-v3.5.2` |
| PretGo | `pretgo-v` | `pretgo-v1.10.1` |
| FabHome | `fabhome-v` | `fabhome-v1.7.1` |
| FabInventory | `fabinventory-v` | `fabinventory-v2.1.0` |

Les tags sont annotés et posés sur le commit de la release. Le générateur s'en sert pour savoir depuis
quel point lire l'historique.

### Publier une version

1. Mettre à jour la constante `APP_VERSION` de l'application (manifest FabSuite, bannière de démarrage
   et pieds de page en dérivent ; `tests/test_release_versions.py` compare cette valeur aux notes).
2. Générer l'entrée de notes (toujours en `--dry-run` d'abord) :

```bash
python release_notes_gen.py --app ./Fabtrack --version 2.7.0 --title "Garde-fou contre les doubles saisies" --dry-run
python release_notes_gen.py --app ./Fabtrack --version 2.7.0 --title "Garde-fou contre les doubles saisies"
```

Le générateur renseigne aussi la clé `url` de l'entrée (lien vers la release GitHub) ; pour un
historique déjà écrit, `python release_notes_gen.py --app ./Fabtrack --sync-release-links` la
recalcule à partir des tags locaux, sans toucher aux notes.

Chaque README d'application possède une section changelog encadrée par `<!-- changelog:start -->` et
`<!-- changelog:end -->`. `python3 changelog_gen.py --app ./<app>` la synchronise avec les notes : les
blocs déjà écrits sont conservés, les versions présentes dans les notes mais absentes du README sont
ajoutées automatiquement. Après une correction des notes, `--refresh` réécrit les blocs générés ; ceux
rédigés à la main et marqués `<!-- manuel -->` restent intacts.

Sans `--since`, le générateur repart du dernier tag `<app>-v*` et ne garde que les commits touchant le
dossier de l'application. S'il n'existe aucun tag, il lit les 50 derniers commits et écarte les notes
déjà publiées. Une entrée de même numéro de version est remplacée, les autres sont conservées.
`--override "Note A" "Note B"` permet d'écrire les notes à la main.

3. Poser le tag et publier :

```bash
python3 release_publish.py --app ./Fabtrack --check   # version, tête des notes et liens
python3 release_publish.py --app ./Fabtrack           # pose le tag fabtrack-vX.Y.Z
python3 release_publish.py --app ./Fabtrack --push    # pousse la branche et le tag
```

Le push des tags peut aussi être laissé à GitHub : `.github/workflows/auto-release.yml` crée à
chaque push sur `main` les tags et releases manquants, avec le `GITHUB_TOKEN` du workflow (aucun
secret à configurer). Une application dont les notes sont en attente est ignorée, et les liens `url`
des notes sont complétés automatiquement après publication.

`release_publish.py` refuse un tag déjà posé sur une version qui a encore des notes en attente :
incrémenter `APP_VERSION` plutôt que déplacer un tag. Le mode
`--tag <app>-vX.Y.Z --output release-body.md` produit le corps Markdown de la GitHub Release
(utilisé par le workflow de publication).

Le push du tag publie automatiquement une GitHub Release titrée d'après l'entrée de notes
correspondante (`.github/workflows/github-release.yml`, corps préparé par `release_publish.py`) :
un tag sans entrée de notes fait échouer ce workflow plutôt que de créer une release vide.

4. Contrôler la cohérence version / notes et qu'il ne reste rien en attente :

```bash
python3 -m unittest discover -s tests          # manifeste == tête des notes == dernier tag
for app in Fabtrack FabBoard PretGo FabHome FabInventory/FabInventory; do
  python3 changelog_gen.py --app "./$app" --check      # changelog README complet
done
for app in Fabtrack FabBoard PretGo FabHome FabInventory/FabInventory; do
  python release_notes_gen.py --app "./$app" --check   # code 1 s'il reste une note à publier
done
```

Ces contrôles sont rejoués à chaque push par `.github/workflows/version-consistency.yml` (checkout
complet, sinon les tags seraient absents).

## Tests et intégration continue

### Tests unitaires

```bash
python3 -m unittest discover -s tests                     # racine : versions, notes, outils de release
python3 -m unittest discover -s tests -p 'contract_*.py'  # contrat FabSuite (copies de fabsuite_core)
```

Chaque application a sa propre suite, qui exige Flask et son `requirements.txt` — les cinq sont
lancées en CI :

```bash
cd Fabtrack && python3 -m unittest discover -s tests     # idem FabBoard/, PretGo/, FabHome/
cd FabInventory/FabInventory && python3 tests/run_all.py # un processus par module de test
```

FabInventory passe par `tests/run_all.py` parce que `app.py` fige son chemin de base à l'import :
lancer chaque module dans son propre processus garantit à chacun sa base temporaire isolée.

### Smoke test des cinq applications

`smoke_test_apps.py` démarre chaque application, vérifie sa page d'accueil et son manifeste
`/api/fabsuite/manifest`, puis l'arrête. Le script n'utilise que la bibliothèque standard, mais
l'interpréteur qui lance les applications doit avoir leurs dépendances installées :

```bash
python3 -m venv .venv
.venv/bin/pip install -r Fabtrack/requirements.txt -r FabBoard/requirements.txt \
  -r PretGo/requirements.txt -r FabHome/requirements.txt -r FabInventory/FabInventory/requirements.txt
.venv/bin/python smoke_test_apps.py
```

```
Smoke test FabLab Suite — interpreteur : .venv/bin/python
  Fabtrack (port 5555) … OK en 0.5s — / 200, /api/fabsuite/manifest 200
  ...
5 application(s) demarree(s) et verifiee(s) avec succes.
```

Options : `--list` (applications et ports), `--only Fabtrack` (répétable), `--timeout 45`,
`--verbose`, `--python .venv/bin/python`. Code retour 0 si les cinq répondent, 1 sinon, 2 si
l'application demandée n'existe pas. Un port déjà occupé ou une application qui ne répond pas
est signalé avec les dernières lignes de son journal.

Réglages par application, utiles pour lancer une application seule :

| Application | Port du smoke test | Variable à connaître |
|---|---|---|
| Fabtrack | 5555 | port fixé dans `app.py` |
| FabBoard | 5560 | `FABBOARD_PORT` (défaut 5580, `PORT` est ignoré) |
| PretGo | 5000 | port fixé dans `app.py` |
| FabHome | 3000 | `PORT` ou `FABHOME_APP_PORT` |
| FabInventory | 5590 | `PORT` ; `DB_PATH` et `UPLOAD_FOLDER` visent `/data` par défaut (chemin de conteneur) et sont redirigés vers un dossier temporaire pendant le test |

### Workflows GitHub Actions

| Workflow | Déclencheur | Rôle |
|---|---|---|
| `version-consistency.yml` | fin de `Auto release`, manuel | Job « Versions et tags » (tests racine, changelogs, notes en attente, contrat FabSuite), matriciel « Tests Fabtrack / FabBoard / PretGo / FabHome / FabInventory », puis « Demarrage des cinq applications » qui installe les dépendances et exécute `smoke_test_apps.py` une fois les tests unitaires verts |
| `auto-release.yml` | push sur `main` | Crée les tags `<app>-vX.Y.Z` et les releases manquantes avec le `GITHUB_TOKEN`, puis commite les liens `url` des notes |
| `github-release.yml` | push d'un tag `*-v*` | Publie la release dont le corps vient de `release_publish.py --tag … --output …` |

Les workflows sont **enchaînés** : `auto-release` crée les tags, puis `version-consistency`
contrôle que chaque version publiée a bien le sien. Le contrôle n'est donc plus lancé sur le push
lui-même — c'est ce qui provoquait des rouges sur une release publiée quelques secondes après le
début du contrôle, et obligeait à repousser un commit vide pour relancer la CI.

`github-release` couvre le cas d'un tag poussé à la main, `auto-release` celui d'un push sans tag.
Une application dont les notes sont en attente est ignorée par `auto-release` ; si elle n'a encore
aucun tag, la publication initiale est autorisée (`release_publish.py` revalide version, tête des
notes et liens avant de poser le tag).

## Dépannage : ports et variables d'environnement

Chaque application choisit son port différemment : c'est la première cause de « ça ne répond pas » en
local. Tableau des réglages réellement lus par le code (hors Docker) :

| Application | Port local | Variables reconnues | Particularité |
|---|---|---|---|
| Fabtrack | 5555 | `FLASK_DEBUG` | Port fixé dans `app.py`, non configurable ; `FLASK_SECRET_KEY` et `TZ` viennent de `docker-compose.yml` |
| FabBoard | 5580 | `FABBOARD_PORT`, `FABTRACK_URL`, `FABBOARD_FABTRACK_CACHE_TTL`, `FLASK_DEBUG` | `PORT` est **ignoré** : sans `FABBOARD_PORT`, l'app écoute sur 5580 |
| PretGo | 5000 | — | Port fixé dans `app.py` (aucune variable de port) |
| FabHome | 3000 | `PORT`, `FABHOME_APP_PORT` (prioritaire), `FABHOME_DB`, `FABHOME_DATA`, `FLASK_DEBUG` | Base `data/fabhome.db` et dossier `data/` par défaut, déplaçables par `FABHOME_DB`/`FABHOME_DATA` |
| FabInventory | 5590 | `PORT`, `DB_PATH`, `UPLOAD_FOLDER`, `SECRET_KEY`, `DEBUG`, `PERF_LOG` | `DB_PATH` et `UPLOAD_FOLDER` visent `/data` par défaut, chemin de conteneur |

En Docker, ce sont les ports **hôtes** de `docker-compose.yml` qui changent (variables du `.env`) :

| Service | Hôte → conteneur | Variable du `.env` |
|---|---|---|
| fabhome | 3001 → 3000 | `FABHOME_PORT` |
| fabtrack | 5555 → 5555 | `FABTRACK_PORT` |
| pretgo | 5000 → 5000 | `PRETGO_PORT` |
| fabboard | 5580 → 5580 | `FABBOARD_PORT` |
| fabinventory | 5590 → 5590 | `FABINVENTORY_PORT` |

### Symptômes courants

**`port 55xx deja utilise` (smoke test) ou `Address already in use`** — une instance tourne encore.
Identifiez-la puis arrêtez ce PID précis :

```bash
ss -ltnp | grep 5555        # Linux : donne le PID à l'écoute
netstat -ano | findstr :5555  # Windows : dernière colonne = PID, puis taskkill /PID <pid>
```

**L'application démarre mais la page ne répond pas sur le port attendu** — vérifiez la variable :
FabBoard répond sur 5580 (et non 5000/5560) sans `FABBOARD_PORT` ; FabHome répond sur 3000 en local
mais sur **3001** via Docker, puisque le compose mappe 3001 → 3000.

**`PermissionError: [Errno 13] Permission denied: '/data'` (FabInventory)** — l'application attend les
chemins du conteneur. En local, pointez-les vers un dossier inscriptible :

```bash
DB_PATH=./data/fabinventory.db UPLOAD_FOLDER=./data/uploads python3 app.py
```

**`ModuleNotFoundError: No module named 'flask' / 'apscheduler' / 'caldav'`** — l'interpréteur utilisé
n'a pas les dépendances de l'application : créez un venv et installez le `requirements.txt` concerné
(voir « Smoke test des cinq applications »). C'est le cas typique de PretGo, qui a besoin
`d'apscheduler` en plus de Flask.

**Fabtrack affiche une redirection au lieu de l'accueil** — sur une base vide, `/` renvoie `302` vers
`/setup` : c'est l'assistant de première installation, pas une panne. Le smoke test accepte ce code.

**Un serveur reste actif après un `Ctrl+C` ou un script interrompu** — le processus enfant a survécu au
shell. Arrêtez-le par PID (jamais par motif de nom, qui peut viser d'autres processus) :

```bash
kill <pid>          # puis kill -9 <pid> si nécessaire
```

**Une variable modifiée n'a aucun effet** — elles sont lues au démarrage : redémarrez l'application
(et non seulement le navigateur). Sous Docker, `docker compose up -d` recrée le conteneur ;
`docker compose restart` conserve l'environnement d'origine.

## Maintenance serveur

```bash
./fabsuite-ubuntu.sh audit
./fabsuite-ubuntu.sh status
./fabsuite-ubuntu.sh update
./fabsuite-ubuntu.sh logs
./fabsuite-ubuntu.sh logs Fabtrack
```

### Sauvegarde de la suite

`./fabsuite-ubuntu.sh backup` crée un snapshot horodaté des bases SQLite, des
uploads et de la configuration des cinq applications, dans
`$HOME/backup-fabsuite/suite-AAAA-MM-JJ-HHMMSS` (surchargeable avec
`FABSUITE_BACKUP_DIR`).

- Le pré-check `check-data-safety` est exécuté d'abord : un risque de chemin de
données annule le snapshot.
- Chaque volume devient une archive `tar.gz` par application (`Fabtrack-data.tgz`,
`Fabtrack-uploads.tgz`, etc.).
- Chaque base SQLite réellement présente dans les archives est relue et passe
`PRAGMA integrity_check` ; le marqueur `snapshot.ok` n'est écrit qu'après ces
vérifications, et `manifest.json` détaille sources, tailles, empreintes SHA-256
et résultat d'intégrité.
- Un volume absent est un simple avertissement (application non installée) ; un
snapshot sans aucune donnée est refusé.

Le snapshot est pris à chaud, applicatifs démarrés : pour un arrêt parfaitement
cohérent de toutes les écritures, arrêter la suite avant de le lancer.

Après un snapshot valide, la rotation s'applique : `FABSUITE_BACKUP_RETENTION_COUNT`
(défaut 7) garde les N snapshots les plus récents, ou
`FABSUITE_BACKUP_RETENTION_DAYS` (> 0) supprime ceux plus vieux que N jours —
le mode jours est prioritaire quand il est activé. Le snapshot qui vient d'être
créé et le dernier snapshot valide ne sont jamais supprimés, même hors quota.
Une valeur `0` désactive la rotation. Un snapshot invalide ne déclenche jamais
de rotation, pour qu'une série d'échecs n'efface pas l'historique sain.

### Restaurer un snapshot

```bash
./fabsuite-ubuntu.sh restore --snapshot ~/backup-fabsuite/suite-2026-11-19-120000
# ou vers un dossier de staging explicite (doit être vide) :
./fabsuite-ubuntu.sh restore --snapshot <dossier> --dest /srv/restauration
# ne restaurer que certains volumes (option répétable) :
./fabsuite-ubuntu.sh restore --snapshot <dossier> --dest /srv/restauration \
  --source Fabtrack/data --source Fabtrack/uploads
```

Avant la moindre écriture, la commande vérifie le manifeste, son empreinte
`manifest_sha256` stockée dans `snapshot.ok`, le `sha256` de chaque archive et
la lisibilité de leur contenu. Elle refuse un snapshot invalide, une archive
altérée et tout membre absolu, contenant `..`, hors `App/role` ou de type lien.
La destination doit être vide et n'être ni un fichier ni un lien symbolique.

Par défaut, tous les volumes du snapshot sont restaurés. `--source APP|ROLE`
(répétable ; `APP/ROLE` accepté aussi) restreint l'extraction aux volumes
demandés : les contrôles du snapshot (manifeste, `snapshot.ok`, destination)
restent identiques et chaque archive retenue est vérifiée comme en restauration
complète. Les volumes non demandés sont listés puis ignorés ; une sélection
absente du manifeste refuse la restauration au lieu de produire un dossier
incomplet sans le dire.

Par défaut, l'extraction se fait dans un staging
`$HOME/restore-fabsuite/<snapshot>` (ou `FABSUITE_RESTORE_DIR`) avec
l'arborescence `App/role/…` : arrêter la suite, vérifier les fichiers, puis les
recopier vers les chemins de données avant de redémarrer. Aucune donnée en
place n'est écrasée par cette commande.

`./fabsuite-ubuntu.sh update` prend automatiquement ce snapshot vérifié avant
tout `git fetch/reset` et tout rebuild. Si le snapshot est invalide :

- en terminal interactif, l'opérateur doit confirmer la poursuite (`y`) ;
- en mode non interactif (GUI/automatisation), l'update continue avec un
avertissement explicite — comportement voulu pour ne pas bloquer une mise à
jour urgente ;
- `FABSUITE_UPDATE_REQUIRE_VERIFIED_BACKUP=1` annule l'update dans ce cas ;
- `FABSUITE_UPDATE_SKIP_BACKUP=1` désactive le snapshot automatique.

## Confidentialité et publication

Le repo est préparé pour une publication propre :
- valeurs serveur spécifiques retirées du code d'installation
- variables sensibles externalisées (`.env`, `fabsuite-ubuntu.env`)
- fichiers locaux/outils de debug exclus via `.gitignore`
