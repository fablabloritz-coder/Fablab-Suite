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
- `docs/ACCEPTATION_SAUVEGARDE_RESTAURATION.md` (matrice de validation serveur)
- `docs/RESTAURATION_RUNBOOK.md` (reprise, staging et retour arrière)
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
- **Sauvegarder** : lance `backup` via le helper (snapshot vérifié, rotation incluse).
  Un verrou serveur refuse une seconde sauvegarde simultanée (timer + GUI) ;
  le bouton **Annuler** ferme le canal de capture, le helper pouvant continuer.
  La ligne « Dernier snapshot valide » affiche l'âge du dernier snapshot et
  passe en rouge au-delà de 7 jours ou en l'absence de snapshot.
- **Restaurer** : choisit un snapshot valide, affiche la date, les volumes et
  la taille décompressée totale, puis refuse une destination non absolue,
  la racine, `..` ou le HOME. La restauration écrit dans un staging vide et
  recontrole l'intégrité SQLite après extraction. Voir
  `docs/RESTAURATION_RUNBOOK.md` pour la bascule et le retour arrière.
- **Vérifier les snapshots** : lance `verify --all` sur le serveur, affiche le
  bilan et la liste des snapshots dégradés (archive altérée, base corrompue,
  manifeste modifié), avec le détail des erreurs dans le journal `[verify]`.
  Sans aucun snapshot, il affiche « Aucun snapshot » et invite à lancer
  d'abord une sauvegarde (code 2, sans marqueur de dégradation).
- **Timers vérification / sauvegarde** : **État vérification** / **État
  sauvegarde** relisent la prochaine exécution, le dernier résultat et le
  marqueur. **Installer timer vérification** / **Installer timer sauvegarde**
  installent ou actualisent l'unité et l'activent (sudo requis) ;
  **Désactiver timer vérification** / **Désactiver timer sauvegarde** demandent
  confirmation puis exécutent `disable-*-timer` (unités conservées). Un échec
  sudo ou `systemctl` est affiché avec le détail du helper. L'état désactivé
  affiche « timer désactivé » et le bouton devient **Réactiver** ; un timer
  relancé à la main affiche « timer actif (désactivé au démarrage) ». Voir
  « Timers planifiés » pour le détail des états.
- **État notifications** : lit `notify-status` du helper et affiche les
  destinations configurées (`webhook configuré/absent`, `mail configuré/absent`),
  puis, pour `backup` et `verify`, si le fichier `notify-<service>.state` existe
  avec la date de la dernière alerte envoyée (anti-rebond). L'absence de cible
  configurée est signalée en orange ; l'état est relu à la connexion et après
  un backup, un verify ou un acquittement lancés depuis le GUI, pour ne pas
  afficher un anti-rebond périmé. Un backup réussi appelle `notify-reset
  --notify-service backup`, un `verify --all` sain appelle `notify-reset
  --notify-service verify` : la prochaine panne planifiée est donc de nouveau
  notifiée, sans dupliquer la logique du helper.
- **Acquitter les alertes** : après confirmation, supprime les marqueurs via
  le helper, mais seulement si les conditions sont réunies : `verify-degrade`
  exige un `verify --all` sain, `backup-failed` exige un snapshot valide plus
  récent que l'échec. Fichiers ordinaires uniquement (liens symboliques
  refusés), puis rafraîchissement des deux lignes d'état.
- **Restaurer un snapshot** : liste les snapshots valides du serveur, puis lance
  `restore` sur celui choisi. Le modal liste aussi les volumes du snapshot
  (`Fabtrack/data`, `FabHome/icons`…) : sans sélection, tout est restauré ;
  sinon seuls les volumes choisis sont transmis au helper via `--source`.
  Sans destination saisie, le helper utilise son staging par défaut
  (`FABSUITE_RESTORE_DIR` ou `~/restore-fabsuite/<snapshot>`). Une destination
  explicite doit être un dossier vide ; rien n'est écrasé en place.
  Si le serveur a un ancien `suite_backup.py` sans les commandes `list` ou
  `volumes`, le modal l'indique clairement et invite à lancer `update` avant de
  réessayer.
- **Pendant une action** (sauvegarde, restauration, vérification) : les boutons
  d'action sont verrouillés, et **Connecter** / **Déconnecter** sont refusés
  avec « Action en cours » pour ne jamais couper le worker. **⚡ Débloquer**
  (barre du terminal) libère l'interface si elle semble figée, mais
  n'interrompt jamais l'action : Connecter, Déconnecter et le lancement d'une
  seconde action restent bloqués jusqu'à la fin du worker. Fermer la fenêtre
  ou recharger la page pendant l'action demande confirmation, car cela
  interromprait le worker.

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

### Créer un snapshot

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

Avant d'écrire la moindre archive, un préflight vérifie que la destination est
inscriptible (fichier sonde créé puis supprimé) et que le disque a assez de
place : taille des sources + marge de 10 %, au moins 64 Mo. Un refus affiche
les volumes libres et nécessaires, code de sortie 2, sans créer de snapshot.

Sauvegardes et restaurations sont sérialisées par un verrou commun
(`$TMPDIR/.fabsuite-commun-<uid>.lock`, surchargeable avec
`FABSUITE_COMMON_LOCK_FILE`) en plus du verrou propre à chaque opération :
une restauration ne peut pas lire un snapshot pendant qu'une sauvegarde
l'écrit. `FABSUITE_LOCK_FILE` force un fichier unique qui sert alors de verrou
commun.

Après un snapshot valide, la rotation s'applique : `FABSUITE_BACKUP_RETENTION_COUNT`
(défaut 7) garde les N snapshots les plus récents, ou
`FABSUITE_BACKUP_RETENTION_DAYS` (> 0) supprime ceux plus vieux que N jours —
le mode jours est prioritaire quand il est activé. Le snapshot qui vient d'être
créé et le dernier snapshot valide ne sont jamais supprimés, même hors quota.
Une valeur `0` désactive la rotation. Un snapshot invalide ne déclenche jamais
de rotation, pour qu'une série d'échecs n'efface pas l'historique sain.

### Vérifier un snapshot

```bash
# relire un snapshot (manifeste, marqueur, sha256, archives, intégrité SQLite)
./fabsuite-ubuntu.sh verify --snapshot ~/backup-fabsuite/suite-2026-11-19-120000
# contrôler tous les snapshots du dossier (exit 2 si l'un est invalide)
./fabsuite-ubuntu.sh verify --all
./fabsuite-ubuntu.sh verify --all --dest /srv/sauvegardes
```

`verify` ne modifie rien : il recalcule les empreintes, relit chaque archive et
repasse `PRAGMA integrity_check` sur chaque base. Utile en tâche planifiée pour
détecter un snapshot dégradé avant d'en avoir besoin ; un nom de dossier copié
ou renommé se vérifie via `--snapshot` explicite.

Codes de sortie : `0` snapshots sains, `3` au moins un snapshot dégradé,
`2` vérification impossible (arguments, dossier sans snapshot, module ancien).
Cette distinction permet aux automatisations de ne signaler que les vraies
dégradations.

### Timers planifiés (sauvegarde et vérification)

Deux timers systemd indépendants planifient les opérations récurrentes :

```bash
./fabsuite-ubuntu.sh install-backup-timer
./fabsuite-ubuntu.sh install-verify-timer
```

Le helper installe `fabsuite-backup.service`/`fabsuite-backup.timer` et
`fabsuite-verify.service`/`fabsuite-verify.timer`, puis les active. Les services
tournent sous l'utilisateur opérateur (pas root) et réutilisent `backup` et
`verify --all` avec leurs contrôles et marqueurs habituels. `Persistent=true`
rattrape un démarrage manqué.

| Timer | Fonction | Horaire par défaut | Variable |
| --- | --- | --- | --- |
| `fabsuite-backup.timer` | snapshot vérifié + rotation | tous les jours à 03:30 | `FABSUITE_BACKUP_ON_CALENDAR` |
| `fabsuite-verify.timer` | `verify --all` | tous les jours | `FABSUITE_VERIFY_ON_CALENDAR` |

Les horaires se personnalisent dans `fabsuite-ubuntu.env` :

```bash
FABSUITE_BACKUP_ON_CALENDAR="*-*-* 03:30:00"   # syntaxe systemd OnCalendar
FABSUITE_VERIFY_ON_CALENDAR="*-*-* 04:30:00"
```

Le dossier des unités est `/etc/systemd/system` par défaut.
`FABSUITE_SYSTEMD_UNIT_DIR` le remplace pour l'installation, la lecture d'état
et la désactivation ; la valeur doit être un **chemin absolu** (un chemin
relatif est refusé, code 2).

**Suivre l'état**

```bash
systemctl list-timers fabsuite-backup.timer fabsuite-verify.timer
journalctl -u fabsuite-backup.service -n 100
journalctl -u fabsuite-verify.service -n 100
cat ~/backup-fabsuite/backup-failed    # dernier échec backup, si présent
cat ~/backup-fabsuite/verify-degrade   # dernier snapshot dégradé, si présent
```

Les boutons **État vérification** / **État sauvegarde** du GUI relisent ces
informations à la connexion et après une action. La ligne et le bouton
d'installation suivent l'état systemd :

| Ligne affichée | Condition | Bouton d'installation | Prochaine exécution |
| --- | --- | --- | --- |
| `timer active` | installé, actif, activé au démarrage | Installer timer | affichée |
| `timer inactive` | installé, arrêté, activé au démarrage | Installer timer | affichée (ou inconnue) |
| `timer désactivé` | installé, arrêté, désactivé au démarrage | **Réactiver** | masquée |
| `timer actif (désactivé au démarrage)` | relancé à la main après désactivation | **Réactiver** | affichée |
| `Timer non installe : ...` | unité absente | Installer timer | — |

Un marqueur (`backup-failed`, `verify-degrade`) passe la ligne en rouge, quel
que soit l'état du timer.

**Désactiver / réactiver**

```bash
./fabsuite-ubuntu.sh disable-backup-timer
./fabsuite-ubuntu.sh disable-verify-timer
sudo systemctl disable --now fabsuite-backup.timer   # équivalent direct
```

`disable-*-timer` exécute `systemctl disable --now` sans supprimer les unités.
L'action est idempotente : si le timer n'est pas installé, elle signale qu'aucune
exécution n'est planifiée, sans erreur. La réactivation passe par
`install-*-timer` ou le bouton **Réactiver** : l'unité est remise à jour puis
activée.

**Échecs et marqueurs**

- `fabsuite-backup.service` échoue → `systemctl --failed` ;
  `~/backup-fabsuite/backup-failed` contient l'horodatage, le code de sortie et
  le renvoi au journal, et il est supprimé au prochain backup réussi. Détail :
  `journalctl -u fabsuite-backup.service -n 100`.
- `fabsuite-verify.service` échoue avec le code 3 → `verify-degrade` est créé ;
  il est supprimé à la prochaine vérification saine. Le code 2 (vérification
  impossible : aucun snapshot, module ancien, argument invalide) ne pose pas de
  marqueur et laisse une ligne explicite ; un marqueur existant n'est pas effacé
  par une erreur passagère. Détail :
  `journalctl -u fabsuite-verify.service -n 100`.
- **Acquitter les alertes** (GUI) ou `./fabsuite-ubuntu.sh clear-markers`
  supprime `verify-degrade` seulement si un `verify --all` sain vient de réussir,
  et `backup-failed` seulement si un snapshot valide est postérieur à l'échec.
  Sinon les marqueurs sont conservés avec la raison.

Pour vérifier tout de suite sans attendre le timer :

```bash
sudo systemctl start fabsuite-verify.service
sudo systemctl start fabsuite-backup.service
```

### Notifications d'échec (mail ou webhook)

En plus des marqueurs, chaque exécution planifiée en échec (code non nul) peut
envoyer une notification externe. Configurer dans `fabsuite-ubuntu.env` :

```bash
# Webhook : POST JSON, curl requis (timeout 10 s)
FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.exemple.invalid/fabsuite
# Email : necessite la commande mail ou sendmail sur l'hote
FABSUITE_NOTIFY_EMAIL=admin@exemple.invalid
```

Les deux cibles sont indépendantes et facultatives ; sans configuration,
aucune tentative n'est faite (simple ligne `[notify]` dans le journal).

- Webhook : `Content-Type: application/json`, corps
  `{"service":"backup|verify","status":"failed","code":N,"host":"...","message":"..."}`.
- Email : sujet `[FabLab Suite] <service> en echec (code N) - <hote>`, corps
  avec le renvoi vers `journalctl -u fabsuite-<service>.service`.
- `verify` notifie aussi le code 2 (vérification impossible), pas seulement le
  code 3 (snapshot dégradé).
- Un échec d'envoi (webhook injoignable, `mail` absent) n'échoue jamais
  l'unité : l'erreur est seulement journalisée (`[notify] ERROR`).

**Anti-rebond** : une alerte n'est envoyée qu'une fois par série d'échecs
consécutifs. Après le premier envoi réussi, l'état est écrit dans
`~/backup-fabsuite/notify-backup.state` (ou `notify-verify.state`) ; les
exécutions suivantes en échec ne renvoient rien et journalisent seulement
`[notify] anti-rebond: alerte deja envoyee`. Une exécution réussie supprime cet
état, donc une nouvelle panne après un succès est de nouveau notifiée.

- l'état est propre à chaque service : un échec de `verify` n'est pas masqué par
  une alerte `backup` déjà envoyée ;
- un backup réussi (même lancé à la main depuis le GUI) ou un `verify --all`
  sain réinitialise l'anti-rebond du service correspondant via `notify-reset` ;
- un envoi totalement échoué (webhook injoignable, `mail` absent) n'arme pas
  l'anti-rebond : la tentative est refaite à l'exécution suivante ;
- `notify-status` donne les destinations configurées et l'état détaillé des
  deux services (présence du fichier, horodatage de la dernière alerte, lien
  symbolique), tel quel consommé par le bouton **État notifications** du GUI
  SSH.

```bash
./fabsuite-ubuntu.sh notify-reset --notify-service backup   # reautoriser une alerte
rm -f ~/backup-fabsuite/notify-backup.state                 # équivalent manuel
```

La configuration est lue à chaque exécution. Les unités installées passent
explicitement `--env-file` avec le `fabsuite-ubuntu.env` utilisé lors de
`install-backup-timer` / `install-verify-timer` : les exécutions planifiées
lisent donc le même fichier que le GUI, et une notification configurée une
seule fois est réellement utilisée. Si le fichier est déplacé, relancer les deux
`install-*-timer`.

Si les unités ont été installées avant cette version, relancer
`install-backup-timer` et `install-verify-timer` pour ajouter l'appel
`notify-failure` **et** l'appel `notify-reset` après un succès. Test manuel (le
second appel immédiat est filtré par l'anti-rebond) :

```bash
./fabsuite-ubuntu.sh notify-failure --notify-service backup --notify-code 1
./fabsuite-ubuntu.sh notify-failure --notify-service backup --notify-code 1  # anti-rebond
./fabsuite-ubuntu.sh notify-reset --notify-service backup
./fabsuite-ubuntu.sh notify-failure --notify-service backup --notify-code 1  # renvoye
journalctl -u fabsuite-backup.service -n 50 | grep notify
```

Sans systemd, l'équivalent cron (à adapter au chemin d'installation) :

```cron
17 4 * * * cd /srv/fablab-suite && NON_INTERACTIVE=1 ./fabsuite-ubuntu.sh verify --all >> "$HOME/backup-fabsuite/verify.log" 2>&1; code=$?; if [ "$code" -eq 3 ]; then echo "DEGRADE $(date -Is)" > "$HOME/backup-fabsuite/verify-degrade"; NON_INTERACTIVE=1 ./fabsuite-ubuntu.sh notify-failure --notify-service verify --notify-code "$code"; elif [ "$code" -eq 0 ]; then rm -f "$HOME/backup-fabsuite/verify-degrade"; NON_INTERACTIVE=1 ./fabsuite-ubuntu.sh notify-reset --notify-service verify; elif [ "$code" -ne 1 ]; then NON_INTERACTIVE=1 ./fabsuite-ubuntu.sh notify-failure --notify-service verify --notify-code "$code"; fi
```

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
Un préflight vérifie ensuite qu'elle est inscriptible et que le disque peut
accueillir les volumes décompressés (somme des tailles du manifeste + marge),
avant toute extraction. Dans le GUI SSH, le modal de restauration lit l'espace
libre distant (`df -Pk`, sans privilège) et compare le total décompressé des
volumes du snapshot au chemin saisi, avant de lancer le helper.

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
