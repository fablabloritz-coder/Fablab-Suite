# FabLab Suite

Suite d'applications Flask/SQLite pour piloter un FabLab avec un déploiement Docker simple.

Ce monorepo regroupe:
- FabHome: hub central et tableau de supervision
- Fabtrack: machines, consommations, stock, missions
- PretGo: gestion des prêts de matériel
- FabBoard: affichage TV / dashboard visuel
- Outils d'installation: `fabsuite-ubuntu.sh` + `fabsuite_ssh_gui.py`

## Objectif

Installer rapidement une suite complète, préconfigurée pour que les applications communiquent entre elles, sans dépendre de configurations locales personnelles.

## Architecture

- Backend: Flask + SQLite
- Frontend: Bootstrap 5 + JavaScript vanilla
- Déploiement: Docker Compose
- Inter-apps: endpoints FabSuite (`/api/fabsuite/*`)

Structure principale:

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

**Maintenance dossiers** : scan, inspection, correction permissions, archivage, suppression

## Méthodes alternatives (avancées)

### Démarrage rapide local (dev)

```bash
cp .env.example .env
docker compose up -d --build
```

Accès local:
- FabHome: `http://localhost:3001`
- Fabtrack: `http://localhost:5555`
- PretGo: `http://localhost:5000`
- FabBoard: `http://localhost:5580`

### Installation serveur Ubuntu manuelle

Guide complet: `INSTALL_UBUNTU.md`

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
leurs versions indépendamment (`v1.7.1` de FabHome et `v1.9.0` de PretGo ne doivent pas se confondre) :

| Application | Préfixe | Exemple |
|---|---|---|
| Fabtrack | `fabtrack-v` | `fabtrack-v2.7.0` |
| FabBoard | `fabboard-v` | `fabboard-v3.5.1` |
| PretGo | `pretgo-v` | `pretgo-v1.9.0` |
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

Sans `--since`, le générateur repart du dernier tag `<app>-v*` et ne garde que les commits touchant le
dossier de l'application. S'il n'existe aucun tag, il lit les 50 derniers commits et écarte les notes
déjà publiées. Une entrée de même numéro de version est remplacée, les autres sont conservées.
`--override "Note A" "Note B"` permet d'écrire les notes à la main.

3. Poser le tag et publier :

```bash
git tag -a fabtrack-v2.7.0 -m "Fabtrack 2.7.0 - garde-fou contre les doubles saisies"
git push origin main --tags
```

Le push du tag publie automatiquement une GitHub Release titrée d'après l'entrée de notes
correspondante (`.github/workflows/github-release.yml`, corps préparé par `release_publish.py`) :
un tag sans entrée de notes fait échouer ce workflow plutôt que de créer une release vide.

4. Contrôler la cohérence version / notes et qu'il ne reste rien en attente :

```bash
python3 -m unittest discover -s tests          # manifeste == tête des notes == dernier tag
for app in Fabtrack FabBoard PretGo FabHome FabInventory/FabInventory; do
  python release_notes_gen.py --app "./$app" --check   # code 1 s'il reste une note à publier
done
```

Ces contrôles sont rejoués à chaque push par `.github/workflows/version-consistency.yml` (checkout
complet, sinon les tags seraient absents).

## Maintenance serveur

```bash
./fabsuite-ubuntu.sh audit
./fabsuite-ubuntu.sh status
./fabsuite-ubuntu.sh update
./fabsuite-ubuntu.sh logs
./fabsuite-ubuntu.sh logs Fabtrack
```

## Confidentialité et publication

Le repo est préparé pour une publication propre:
- valeurs serveur spécifiques retirées du code d'installation
- variables sensibles externalisées (`.env`, `fabsuite-ubuntu.env`)
- fichiers locaux/outils de debug exclus via `.gitignore`
