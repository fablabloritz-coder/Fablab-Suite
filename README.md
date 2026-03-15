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
- `MEMORY.md` (handover complet pour nouvelle session IA/dev)
- `fabsuite-ubuntu.sh` (installation et maintenance serveur)
- `fabsuite-ubuntu.env.example` (config serveur)
- `fabsuite_ssh_gui.py` (assistant SSH graphique)
- `INSTALL_UBUNTU.md` (guide serveur pas à pas)
- `docs/APPLICATIONS.md` (détail métier par application)
- `docs/CONTROL_CENTER_MVP.md` (approche app unique local + serveur)
- `deploy_core/` (scaffold moteur de workflow unifié)

## Installation recommandée (méthode unique)

Pour installer ou mettre à jour la suite sans dérive de configuration, passez uniquement par cette commande Windows (PowerShell):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression (Invoke-RestMethod 'https://raw.githubusercontent.com/fablabloritz-coder/Fablab-Suite/main/run_fabsuite_ssh.ps1')"
```

Cette commande est le parcours standard à maintenir:
- elle récupère le lanceur maintenu dans ce repo
- elle met en cache le GUI dans `%LOCALAPPDATA%\\FabSuite\\ssh-gui`
- elle lance l'assistant unique pour installation et mise à jour

Ordre conseillé dans la GUI:
1. Connect SSH
2. 1) Envoyer les fichiers installateur
3. 2) Audit serveur
4. 4) Préparer l'hôte Ubuntu
5. 5) Réparer env monorepo
6. 6) Pré-check sécurité données
7. 7) Installer la suite (ou 7b Mettre à jour la suite)
8. 8) Vérifier l'état

Le GUI est conçu pour rester neutre et stable:
- pas de serveur prérempli en dur dans le code
- pas de persistance d'identité SSH (hôte/utilisateur) dans la config locale
- bouton dédié "Réparer env monorepo" pour corriger automatiquement les problèmes d'env courants
- source unique de déploiement/mise à jour: monorepo Git (`GIT_REPO_URL`)
- si un ancien layout legacy est détecté, `repair-env` prépare automatiquement la bascule vers le monorepo (`$HOME/fablab-suite`) en conservant les chemins data
- les URLs inter-apps par défaut sont container-safe (`host.docker.internal:<port>`) pour éviter les erreurs d'auto-config
- Install et Update lancent automatiquement une réparation d'env avant exécution
- Install et Update lancent automatiquement un pré-check sécurité données (arrêt + alerte si risque détecté)
- Audit serveur lance aussi automatiquement un pré-check sécurité données informatif
- terminal Output colorisé automatiquement (OK en vert, erreurs en rouge, warnings en orange)

## Méthodes alternatives (avancées)

Ces méthodes restent disponibles pour debug/contribution, mais ne sont pas le parcours standard utilisateur.

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
pip install -r requirements-ssh-gui.txt
python fabsuite_ssh_gui.py
```

## Maintenance

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
