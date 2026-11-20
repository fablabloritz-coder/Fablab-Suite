# FabLab Suite - Installation Ubuntu (guide rapide et fiable)

Ce guide installe la suite complète depuis un **monorepo unique**.

Applications incluses:
- FabHome (hub central)
- Fabtrack (machines, stats, stock, missions)
- PretGo (emprunts)
- FabBoard (dashboard TV)

## 1) Préparer le serveur Ubuntu

Depuis votre machine locale, copiez les fichiers d'installation:

```bash
scp fabsuite-ubuntu.sh fabsuite-ubuntu.env.example INSTALL_UBUNTU.md user@SERVER_IP:~/fabsuite-installer/
```

Puis en SSH:

```bash
ssh user@SERVER_IP
cd ~/fabsuite-installer
chmod +x fabsuite-ubuntu.sh
./fabsuite-ubuntu.sh prepare-host
```

Optionnel (évite d'ouvrir une nouvelle session SSH):

```bash
newgrp docker
```

## 2) Configurer le monorepo

```bash
cp fabsuite-ubuntu.env.example fabsuite-ubuntu.env
nano fabsuite-ubuntu.env
```

Paramètres importants:
- `GIT_REPO_URL` : URL Git du monorepo (ex: `https://github.com/fablabloritz-coder/Fablab-Suite.git`)
- `GIT_BRANCH` : branche à déployer (`main` par défaut)
- `INSTALL_DIR` : dossier cible sur le serveur (`$HOME/fablab-suite` conseillé)
- Ports (`FABHOME_PORT`, `FABTRACK_PORT`, `PRETGO_PORT`, `FABBOARD_PORT`)
- URLs inter-apps (`FABTRACK_URL`, `PRETGO_URL`, `FABBOARD_URL`) : laissez vide pour utiliser automatiquement `host.docker.internal:<port>`

Important:
- Le mode legacy (un repo Git par app) n'est plus utilisé pour `install`/`update`.
- La mise à jour globale passe uniquement par le monorepo (`INSTALL_DIR` doit être un checkout Git racine du monorepo).
- Si un ancien dossier legacy est détecté (ex: `$HOME/fabsuite`), `repair-env` bascule automatiquement `INSTALL_DIR` vers `$HOME/fablab-suite` et conserve les chemins de données existants via les variables `*_DATA_PATH`.

## 3) Installer la suite

```bash
./fabsuite-ubuntu.sh install
```

Ce que fait la commande:
1. Clone (ou met à jour) le monorepo.
2. Génère `FLASK_SECRET_KEY` si vide.
3. Démarre chaque app avec son propre `docker-compose.yml`.
4. Auto-enregistre Fabtrack/PretGo/FabBoard dans FabHome.

## 4) Commandes de maintenance

```bash
# Mettre à jour le code puis rebuild/redémarrer toutes les apps
# (prend d'abord un snapshot vérifié dans ~/backup-fabsuite)
./fabsuite-ubuntu.sh update

# Restaurer un snapshot dans un dossier de staging vérifié
./fabsuite-ubuntu.sh restore --snapshot ~/backup-fabsuite/suite-2026-11-19-120000

# Restaurer seulement certains volumes (--source APP|ROLE répétable)
./fabsuite-ubuntu.sh restore --snapshot ~/backup-fabsuite/suite-2026-11-19-120000 \
  --source Fabtrack/data

# Vérifier un snapshot (ou tous ceux du dossier) sans rien modifier
./fabsuite-ubuntu.sh verify --snapshot ~/backup-fabsuite/suite-2026-11-19-120000
./fabsuite-ubuntu.sh verify --all

# Acquitter les marqueurs (verify-degrade : après un verify --all sain ;
# backup-failed : après un snapshot valide postérieur à l'échec)
./fabsuite-ubuntu.sh clear-markers

# Planifier les snapshots (timer systemd, rotation incluse)
# échec signalé par ~/backup-fabsuite/backup-failed et l'unité systemd
./fabsuite-ubuntu.sh install-backup-timer

# Planifier verify --all (timer systemd, signal ~/backup-fabsuite/verify-degrade)
./fabsuite-ubuntu.sh install-verify-timer

# Arreter la planification (unites conservees ; reactivation par bouton
# Reactiver ou install-*-timer)
./fabsuite-ubuntu.sh disable-backup-timer
./fabsuite-ubuntu.sh disable-verify-timer

# Notifications externes des échecs planifiés (facultatif, dans fabsuite-ubuntu.env)
# FABSUITE_NOTIFY_WEBHOOK_URL=https://hooks.exemple.invalid/fabsuite
# FABSUITE_NOTIFY_EMAIL=admin@exemple.invalid
# Une seule alerte par serie d'echecs consecutifs (anti-rebond) ;
# un succes reinitialise l'etat, une nouvelle panne est donc renvoyee :
./fabsuite-ubuntu.sh notify-reset --notify-service backup
# Etat des notifications (destinations + anti-rebond), aussi lu par le GUI :
./fabsuite-ubuntu.sh notify-status
# Test manuel :
./fabsuite-ubuntu.sh notify-failure --notify-service backup --notify-code 1

# Démarrer / arrêter / redémarrer
./fabsuite-ubuntu.sh start
./fabsuite-ubuntu.sh stop
./fabsuite-ubuntu.sh restart

# Vérifier l'état
./fabsuite-ubuntu.sh status

# Diagnostiquer rapidement Docker + connectivité inter-apps
./fabsuite-ubuntu.sh audit

# Snapshot horodate et verifie des bases SQLite, uploads et configuration
./fabsuite-ubuntu.sh backup

# Logs
./fabsuite-ubuntu.sh logs
./fabsuite-ubuntu.sh logs Fabtrack
```

### Timers planifiés dans le GUI

La carte **Sauvegarde & restauration** adapte la ligne d'état et le bouton
d'installation :

- après `disable-*-timer` : « timer désactivé », prochaine exécution masquée,
  bouton **Réactiver** (au lieu d'« Installer timer ») ;
- timer relancé à la main après désactivation : « timer actif (désactivé au
  démarrage) », prochaine exécution conservée, bouton **Réactiver** pour
  rétablir l'activation au démarrage.

Le détail des états et des commandes est dans le README, section
« Timers planifiés ».

## 5) Accès navigateur

Depuis votre PC:
- `http://SERVER_IP:3001` -> FabHome
- `http://SERVER_IP:5555` -> Fabtrack
- `http://SERVER_IP:5000` -> PretGo
- `http://SERVER_IP:5580` -> FabBoard

## 6) Installation assistée via interface graphique (optionnel)

Le script local `fabsuite_ssh_gui.py` permet de piloter l'installation en SSH.

Installation:

```bash
pip install -r requirements-ssh-gui.txt
python fabsuite_ssh_gui.py
```

Ordre conseillé dans la GUI:
1. Connect SSH
2. Upload installer files
3. Audit serveur (inclut automatiquement un pré-check sécurité données)
4. Prepare host
5. Réparer env monorepo
6. Pré-check sécurité données
7. Install suite
8. Status suite

Conseil pratique:
- Si nécessaire, renseignez l'option avancée "URL repo monorepo" dans la GUI
  (ex: `https://github.com/fablabloritz-coder/Fablab-Suite.git`).
- Install et Update lancent automatiquement une réparation d'env avant exécution.
- Install et Update lancent automatiquement un pré-check sécurité données.
- Si un risque d'écrasement/perte de liaison data est détecté, l'action est stoppée et une alerte est affichée.

## Notes sécurité

- Le helper ne stocke pas de hostname utilisateur en dur dans le repo.
- Le fichier `fabsuite-ubuntu.env` est local serveur et ne doit pas être publié.
- Le mot de passe sudo saisi dans la GUI sert uniquement aux actions nécessitant privilèges (`prepare-host`, etc.).
