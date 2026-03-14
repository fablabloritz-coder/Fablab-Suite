# FabBoard

<img width="520" height="520" alt="logo fabboard" src="https://github.com/user-attachments/assets/b806fd6c-6712-4771-accc-bd4646e1ea89" />


**Dashboard TV temps réel pour Fablab** — Affiche statistiques, calendrier, météo et état des machines sur un écran dédié via un système de slides configurables.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-green?logo=flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local-lightgrey?logo=sqlite&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-purple?logo=bootstrap&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker&logoColor=white)

---

## 📋 Présentation

FabBoard transforme n'importe quel écran TV en tableau de bord interactif pour votre Fablab. Il agrège les données de **Fabtrack** (consommations machines), de votre **calendrier Nextcloud/CalDAV**, de la **météo** (Open-Meteo), et affiche le tout dans un diaporama plein écran entièrement configurable.

### Fait partie de la FabLab Suite

| Application | Description | Port |
|---|---|---|
| **[FabHome](https://github.com/fablabloritz-coder/FabHome)** | Hub central — portail et dashboard | 3001 |
| **[PretGo](https://github.com/fablabloritz-coder/PretGo)** | Gestion de prêts de matériel | 5000 |
| **[Fabtrack](https://github.com/fablabloritz-coder/Fabtrack)** | Suivi des consommations machines | 5555 |
| **FabBoard** | Dashboard TV temps réel | 5580 |

Les applications sont **indépendantes** — chacune peut tourner seule avec son propre Docker. FabBoard se connecte en lecture seule à l'API de Fabtrack et aux sources CalDAV configurées.

---

## ✨ Fonctionnalités

### Système de slides
- Éditeur visuel de slides avec layouts configurables (grilles 1×1 à 3×2)
- Widgets assignables par glisser-déposer
- Durée d'affichage individuelle par slide (5s à 5min)
- Fond personnalisable par slide (couleur ou image)
- Diaporama plein écran optimisé TV (1920×1080)

### Widgets disponibles

| Widget | Description |
|---|---|
| **Horloge** | Heure, date, semaine |
| **Compteurs Fabtrack** | Total interventions, filament 3D (kg), surface découpe (m²), feuilles imprimées — couleurs distinctes par métrique |
| **Statistiques Fabtrack** | Répartition par type d'activité avec barres colorées |
| **Activités récentes** | Dernières consommations Fabtrack avec couleur par type d'activité |
| **Calendrier** | Événements CalDAV/Nextcloud avec code couleur d'urgence (imminent, proche, cette semaine) |
| **Météo** | Température, icône, description, humidité, vent (Open-Meteo, sans clé API) |
| **Image** | Affichage d'image uploadée (contain/cover/fill) |
| **Texte libre** | Texte HTML personnalisé |

### Personnalisation TV
- **Échelle par widget** (×1 à ×3) — adapte la taille de tous les textes
- **Police personnalisable** parmi 8 Google Fonts (Inter, Roboto, Poppins, Montserrat, Open Sans, Source Sans, Orbitron, Rajdhani)
- **Mode sombre** natif optimisé pour écran TV
- Les emplacements non assignés sont automatiquement transparents

### Sources de données
- **Fabtrack** : connexion automatique via API REST, synchronisation configurable
- **CalDAV / Nextcloud** : événements de calendrier avec authentification
- **Open-Meteo** : météo gratuite par ville (pas de clé API requise)
- **Repetier Server / PrusaLink** : état des imprimantes 3D (prévu)
- Auto-refresh configurable (10s à 5min) + bouton de re-synchronisation forcée
- Statut de connexion visible avec badge OK/Erreur/Jamais testé

---

## 🚀 Déploiement

### Option A — Docker (recommandé)

#### Déploiement individuel (FabBoard seul)

**Prérequis :** [Docker Desktop](https://docs.docker.com/get-docker/) (Windows/macOS) ou Docker Engine (Linux).

```bash
git clone https://github.com/fablabloritz-coder/FabBoard.git
cd FabBoard
cp .env.example .env        # ajustez FABBOARD_PORT si besoin
docker compose up -d --build
```

- Application : `http://localhost:5580` (ou le port configuré dans `.env`)
- Données persistées : `./docker-data/data` (SQLite)

#### Déploiement complet (toute la FabLab Suite)

Un `docker-compose.yml` unifié est disponible à la racine du dépôt parent. Il orchestre FabHome, Fabtrack, PretGo et FabBoard en une seule commande :

```bash
# Cloner tous les dépôts dans un même dossier
mkdir ~/fablab && cd ~/fablab
git clone https://github.com/fablabloritz-coder/FabHome.git
git clone https://github.com/fablabloritz-coder/PretGo.git
git clone https://github.com/fablabloritz-coder/Fabtrack.git
git clone https://github.com/fablabloritz-coder/FabBoard.git

# Copier et configurer les variables
cp .env.example .env   # ajustez les ports si besoin

# Lancer toute la suite (ou une app : docker compose up -d --build fabboard)
docker compose up -d --build
```

> **Note Linux** : si vous avez une erreur de permissions Docker, ajoutez votre utilisateur au groupe : `sudo usermod -aG docker $USER && newgrp docker`

#### Accès aux applications

| Application | URL |
|---|---|
| FabHome | `http://IP_SERVEUR:3001` |
| PretGo | `http://IP_SERVEUR:5000` |
| Fabtrack | `http://IP_SERVEUR:5555` |
| FabBoard | `http://IP_SERVEUR:5580` |

Pour trouver l'IP du serveur : `hostname -I | awk '{print $1}'`

#### Interconnexion FabBoard ↔ Fabtrack

- En déploiement **unifié** (suite complète) : FabBoard utilise `http://host.docker.internal:5555` automatiquement.
- En déploiement **individuel** : dans FabBoard → Paramètres → Sources, ajoutez une source Fabtrack avec `http://IP_SERVEUR:5555`.

#### Variables d'environnement

| Variable | Description | Défaut |
|---|---|---|
| `FLASK_SECRET_KEY` | Clé secrète Flask (recommandé en production) | Générée automatiquement |
| `TZ` | Fuseau horaire | `Europe/Paris` |
| `FABTRACK_URL` | URL de Fabtrack pour le bootstrap automatique | `http://host.docker.internal:5555` |

#### Mise a jour / arret / relance (procedures precises)

##### A. FabBoard seul (docker-compose individuel)

Mise a jour:

```bash
cd /chemin/vers/FabBoard
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

Arret:

```bash
cd /chemin/vers/FabBoard
docker compose stop
```

Relance sans rebuild:

```bash
cd /chemin/vers/FabBoard
docker compose start
```

Redemarrage complet:

```bash
cd /chemin/vers/FabBoard
docker compose restart
```

##### B. Suite complete (PretGo + Fabtrack + FabBoard)

Mise a jour des 3 applications:

```bash
cd ~/fablab
git -C PretGo pull --ff-only origin main
git -C Fabtrack pull --ff-only origin main
git -C FabBoard pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

Arret des 3 applications:

```bash
cd ~/fablab
docker compose stop
```

Relance des 3 applications:

```bash
cd ~/fablab
docker compose start
```

Redemarrage des 3 applications:

```bash
cd ~/fablab
docker compose restart
```

Diagnostic rapide:

```bash
cd ~/fablab
docker compose ps
docker logs --tail=120 fabboard
docker logs --tail=120 fabtrack
docker logs --tail=120 pretgo
```

En cas de conflit `container name ... already in use`:

```bash
docker stop fabboard 2>/dev/null || true
docker rm fabboard
cd /chemin/vers/FabBoard
docker compose up -d --build
```

Depannage: applications inaccessibles depuis le navigateur

1. Verifier l'etat des conteneurs:

```bash
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | egrep "fabboard|fabtrack|pretgo"
```

2. En cas de conflit de nom, nettoyer puis relancer:

```bash
docker stop fabboard fabtrack pretgo 2>/dev/null || true
docker rm fabboard fabtrack pretgo 2>/dev/null || true
```

3. Choisir un seul mode de deploiement:

```bash
# Mode stack unique
cd ~/fablab
docker compose up -d --build

# Ou mode applications separees
cd /chemin/vers/Fabtrack && docker compose up -d --build
cd /chemin/vers/FabBoard && docker compose up -d --build
```

4. Verifier les logs et la reponse locale serveur:

```bash
docker logs --tail=120 fabtrack
docker logs --tail=120 fabboard
curl -I http://127.0.0.1:5555
curl -I http://127.0.0.1:5580
```

5. Si local OK mais acces distant KO, verifier le firewall:

```bash
sudo ufw allow 5555/tcp
sudo ufw allow 5580/tcp
sudo ufw status
```

#### Sauvegarde

Les données sont dans des volumes Docker nommés. Pour sauvegarder :

```bash
# Localiser les volumes
docker volume inspect fablab_fabboard-data

# Ou utiliser les fonctions de sauvegarde intégrées de chaque application
```

Chaque application propose aussi un export/import de sa base de données via son interface web.

---

### Option B — Windows (alternative, sans Docker)

> Pour les environnements sans Docker. Python 3.10+ requis.

```bash
# Double-cliquez sur start.bat
# — libère le port 5580 si occupé
# — installe l'environnement virtuel si nécessaire
# — lance le serveur et ouvre le navigateur
```

Ou manuellement :

```bash
git clone https://github.com/fablabloritz-coder/FabBoard.git
cd FabBoard
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python start.py
```

L'application est accessible sur **http://localhost:5580**.

---

## 📁 Structure du projet

```
FabBoard/
├── app.py                  # Application Flask + API REST
├── models.py               # Schéma SQLite, migrations, seed
├── sync_worker.py          # Worker de synchronisation des sources externes
├── start.py                # Script de démarrage (dev)
├── start.bat               # Lancement Windows (kill port + venv + serveur)
├── Dockerfile              # Image Docker (Python 3.11-slim + Waitress)
├── docker-compose.yml      # Docker Compose individuel
├── requirements.txt        # Dépendances Python
├── data/                   # Base SQLite (générée automatiquement)
├── static/
│   ├── css/
│   │   ├── style.css       # Styles configuration/paramètres
│   │   ├── dashboard.css   # Styles TV (widgets, échelle, polices, urgence)
│   │   └── slides.css      # Styles éditeur de slides
│   ├── js/
│   │   ├── dashboard.js    # FabBoardStore + cycle slides + rendu widgets
│   │   ├── parametres.js   # Gestion sources + paramètres
│   │   ├── slides.js       # Éditeur de slides CRUD
│   │   └── utils.js        # Utilitaires partagés
│   ├── img/                # Images statiques
│   └── manifest.json       # PWA manifest
└── templates/
    ├── base.html            # Layout principal
    ├── dashboard.html       # Page TV plein écran
    ├── parametres.html      # Configuration sources + réglages
    ├── slides.html          # Éditeur de slides
    ├── test_api.html        # Page de debug API
    └── widgets/             # Templates individuels des widgets
        ├── horloge.html
        ├── compteurs.html
        ├── fabtrack_stats.html
        ├── activites.html
        ├── calendrier.html
        ├── meteo.html
        └── image.html
```

---

## 🏗️ Architecture technique

### Stack

| Composant | Technologie |
|---|---|
| Backend | Flask 3.1 (Python) + Waitress (WSGI production) |
| Base de données | SQLite 3 (WAL mode) |
| Frontend | Bootstrap 5.3 + Bootstrap Icons + Vanilla JS ES6 |
| Synchronisation | Thread daemon (sync_worker) polling toutes les 10s |
| Météo | Open-Meteo API (gratuit, sans clé) |
| Calendrier | CalDAV (requête HTTP + parsing iCal RFC 5545) |
| Conteneurisation | Docker + Docker Compose |

### Flux de données

```
Sources externes                  FabBoard
┌─────────────┐     sync_worker    ┌──────────────────┐
│  Fabtrack   │────────────────────│  sources_cache    │
│  API REST   │   (poll 10s)       │  (SQLite)         │
├─────────────┤                    ├──────────────────┤
│  CalDAV     │────────────────────│                    │
│  Nextcloud  │                    │  /api/dashboard/  │──→ FabBoardStore (JS)
├─────────────┤                    │      data         │        │
│  Open-Meteo │────────────────────│                    │        ▼
│  API        │   (direct fetch)   │  /api/meteo       │    fabboard:refresh
└─────────────┘                    └──────────────────┘    (CustomEvent)
                                                               │
                                                               ▼
                                                        Widgets HTML
                                                        (re-render)
```

### Sync Worker

Le `sync_worker.py` tourne en tâche de fond (daemon thread) et :
1. Toutes les 10s, vérifie chaque source active
2. Si `derniere_sync + sync_interval_sec < maintenant` → fetch les données
3. Cache le résultat dans `sources_cache` (SQLite)
4. L'API sert les données depuis le cache (avec fallback direct si cache vide)

---

## 🔌 API REST

### Dashboard

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/dashboard/data` | Données agrégées (stats, activités, calendrier, machines) |
| `GET` | `/api/widget-data/<source_id>` | Données d'une source spécifique |
| `GET` | `/api/meteo?ville=Nancy,FR` | Météo par ville |

### Slides & Widgets

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/slides` | Liste des slides avec widgets |
| `POST` | `/api/slides` | Créer une slide |
| `PUT` | `/api/slides/<id>` | Modifier une slide |
| `DELETE` | `/api/slides/<id>` | Supprimer une slide |
| `POST` | `/api/slides/reorder` | Réordonner les slides |

### Sources de données

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sources` | Liste des sources configurées |
| `POST` | `/api/sources` | Ajouter une source |
| `PUT` | `/api/sources/<id>` | Modifier une source |
| `DELETE` | `/api/sources/<id>` | Supprimer une source |
| `POST` | `/api/sources/<id>/test` | Tester la connexion |
| `POST` | `/api/sources/<id>/resync` | Forcer une re-synchronisation |

### Paramètres

| Méthode | Endpoint | Description |
|---|---|---|
| `GET` | `/api/parametres` | Lire les paramètres |
| `PUT` | `/api/parametres` | Modifier les paramètres |

---

## ⚙️ Configuration

### Paramètres de l'interface (Paramètres → Général)

| Paramètre | Description | Défaut |
|---|---|---|
| Intervalle de rafraîchissement | Fréquence de mise à jour des données | 30s |
| Nom du Fablab | Affiché dans l'interface | Fablab |
| Police du dashboard | Police Google Fonts pour l'affichage TV | System |
| Mode sombre | Thème clair ou sombre | Sombre |

### Configuration des sources (Paramètres → Sources)

Ajoutez vos sources via l'interface :

1. **Fabtrack** : URL de l'API (ex : `http://fabtrack:5555`)
2. **CalDAV** : URL du calendrier + identifiants (user/pass)
3. **Open-Meteo** : configuré directement dans le widget (ville)

Chaque source affiche son statut (OK / Erreur / Jamais testé) avec auto-refresh toutes les 30s.

---

## 🔒 Sécurité

| Mesure | Détail |
|---|---|
| **XSS** | Échappement systématique via `textContent` côté JS + `escapeHtml()` |
| **SQL Injection** | Requêtes paramétrées exclusivement |
| **Réseau** | Conçu pour réseau privé Fablab (pas d'authentification) |
| **CORS** | Pas activé (même origine) |
| **Validation** | Inputs utilisateur validés côté serveur |
| **Secret key** | Configurable via `FLASK_SECRET_KEY`, générée automatiquement sinon |

---

## 📝 Licence

Ce projet est développé pour le Fablab Loritz.

MIT — © 2025-2026

## 🎨 Interface TV

Layout optimisé 1920×1080 en grille 3×2 :

```
┌─────────────┬─────────────────────┬──────────────────┐
│  COMPTEURS  │  PROCHAINES TÂCHES  │  CALENDRIER      │
├─────────────┼─────────────────────┼──────────────────┤
│  FABTRACK   │  IMPRIMANTES 3D     │  DERNIÈRES CONSO │
└─────────────┴─────────────────────┴──────────────────┘
```

Navigation :
- `/` — Dashboard TV (plein écran)
- `/activites` — Gestion des activités
- `/parametres` — Configuration sources & affichage

---

## 📝 Roadmap

### Phase 1 (en cours)
- [x] Structure projet & Docker
- [x] Base de données SQLite
- [x] API CRUD activités
- [x] Dashboard TV basique
- [x] Calcul auto urgence

### Phase 2
- [ ] Intégration Fabtrack (stats, état machines)
- [ ] Affichage cartes Fabtrack sur dashboard

### Phase 3
- [ ] Intégration Nextcloud CalDAV
- [ ] Sync événements calendrier

### Phase 4
- [ ] Intégration Repetier Server
- [ ] Cartes imprimantes 3D avec progression

### Phase 5
- [ ] PWA mobile (manifest, service worker)
- [ ] Vue mobile responsive

### Phase 6
- [ ] Support PrusaLink API
- [ ] Notifications push (optionnel)

---

## 📄 Licence

MIT — © 2026 Fablab Loritz

---

## 🤝 Projet lié

[Fabtrack](https://github.com/fablabloritz-coder/Fabtrack) — Application de suivi des consommations Fablab
