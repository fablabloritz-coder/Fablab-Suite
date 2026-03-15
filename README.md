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
- `-Update` force le re-téléchargement depuis GitHub (nouvelle version)
- Lance l'interface graphique (Edge/Chrome en mode app)
- Ferme automatiquement le terminal quand le GUI est fermé

## Utilisation du GUI

Le GUI s'ouvre dans Edge ou Chrome en mode application (sans barre de navigation).

### Mode Local

Gestion Docker Compose depuis votre poste Windows :

1. **Audit** — vérifie l'état des services locaux
2. **Installer** — `docker compose up -d --build`
3. **Mettre à jour** — rebuild et redémarre les services
4. **Status** — état en cours de chaque conteneur
5. **Logs** — logs de toutes les apps ou d'une app spécifique

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
4. Mettre à jour — `git pull` + rebuild

**③ Monitoring**
- Status, Logs (toutes apps ou app spécifique)

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
