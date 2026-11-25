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
- **Double écran** : chaque slide est affectée à l'écran 1 (`/`) ou à l'écran 2 (`/ecran/2`) ; chaque fenêtre garde son propre cycle de slides et ses propres widgets. L'ordre des slides reste global et les slides existantes restent sur l'écran 1.

### Widgets disponibles

| Widget | Description |
|---|---|
| **Horloge** | Heure, date, semaine |
| **Compteurs Fabtrack** | Total interventions, filament 3D (kg), surface découpe (m²), feuilles imprimées — couleurs distinctes par métrique |
| **Statistiques Fabtrack** | Répartition par type d'activité avec barres colorées |
| **Activités récentes** | Dernières consommations Fabtrack avec couleur par type d'activité |
| **Dépendances externes** | Missions Fabtrack qui dépendent d'un autre service (CMIL, rectorat…), regroupées par service avec échéance et dernière étape ; liste de services filtrable par widget |
| **Calendrier** | Événements CalDAV/Nextcloud avec code couleur d'urgence (imminent, proche, cette semaine) |
| **Météo** | Température, icône, description, humidité, vent (Open-Meteo, sans clé API) |
| **Image** | Affichage d'image uploadée (contain/cover/fill) |
| **Texte libre** | Texte HTML personnalisé |

### Personnalisation TV
- **Échelle par widget** (×1 à ×3) — adapte la taille de tous les textes
- **Police personnalisable** parmi 8 Google Fonts (Inter, Roboto, Poppins, Montserrat, Open Sans, Source Sans, Orbitron, Rajdhani)
- **Mode sombre** natif optimisé pour écran TV
- **Orientation par écran** : paysage, portrait (rotation 90°) ou portrait inversé (-90°), réglable dans *Paramètres → Écrans*. La rotation est faite par FabBoard, l'écran reste en paysage côté système ; elle s'applique aussi à l'écran de pause et aux widgets « page web ». Le changement prend effet au rechargement de l'écran.
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

Mise a jour :

```bash
cd /chemin/vers/FabBoard
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

Arret :

```bash
cd /chemin/vers/FabBoard
docker compose stop
```

Relance sans rebuild :

```bash
cd /chemin/vers/FabBoard
docker compose start
```

Redemarrage complet :

```bash
cd /chemin/vers/FabBoard
docker compose restart
```

##### B. Suite complete (PretGo + Fabtrack + FabBoard)

Mise a jour des 3 applications :

```bash
cd ~/fablab
git -C PretGo pull --ff-only origin main
git -C Fabtrack pull --ff-only origin main
git -C FabBoard pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

Arret des 3 applications :

```bash
cd ~/fablab
docker compose stop
```

Relance des 3 applications :

```bash
cd ~/fablab
docker compose start
```

Redemarrage des 3 applications :

```bash
cd ~/fablab
docker compose restart
```

Diagnostic rapide :

```bash
cd ~/fablab
docker compose ps
docker logs --tail=120 fabboard
docker logs --tail=120 fabtrack
docker logs --tail=120 pretgo
```

En cas de conflit `container name ... already in use` :

```bash
docker stop fabboard 2>/dev/null || true
docker rm fabboard
cd /chemin/vers/FabBoard
docker compose up -d --build
```

Depannage : applications inaccessibles depuis le navigateur

1. Verifier l'etat des conteneurs :

```bash
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | egrep "fabboard|fabtrack|pretgo"
```

2. En cas de conflit de nom, nettoyer puis relancer :

```bash
docker stop fabboard fabtrack pretgo 2>/dev/null || true
docker rm fabboard fabtrack pretgo 2>/dev/null || true
```

3. Choisir un seul mode de deploiement :

```bash
# Mode stack unique
cd ~/fablab
docker compose up -d --build

# Ou mode applications separees
cd /chemin/vers/Fabtrack && docker compose up -d --build
cd /chemin/vers/FabBoard && docker compose up -d --build
```

4. Verifier les logs et la reponse locale serveur :

```bash
docker logs --tail=120 fabtrack
docker logs --tail=120 fabboard
curl -I http://127.0.0.1:5555
curl -I http://127.0.0.1:5580
```

5. Si local OK mais acces distant KO, verifier le firewall :

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

## 📜 Changelog

<!-- changelog:start -->

### v3.8.1 — 2026-11-25

#### Portrait plein écran et cache des assets

- Mode portrait corrigé : le dashboard remplit tout l'écran (bande vide à gauche et bas de contenu coupé sur un écran 16:9).
- CSS et JS du dashboard servis avec la version de l'application : un kiosque rechargé après mise à jour ne garde plus l'ancienne feuille en cache (max-age=3600).
- Vérification navigateur de / et /ecran/2 : paysage, portrait, portrait inversé, écran de pause et alignement des widgets « page web » (écart <= 0,4 px).

---

### v3.8.0 — 2026-11-25

#### Widget dépendances externes

- Nouveau widget FabBoard « Dépendances externes » : missions Fabtrack marquées comme dépendant d'un autre service, regroupées par service avec échéance et dernière étape.
- Liste de services à surveiller configurable par widget (vide = tous), missions terminées masquées par défaut, nombre max et échelle TV réglables.
- Lecture du marqueur service_id livré en Fabtrack 2.14.0 (repli sur service_id, couleur du service), aucun nouvel appel à Fabtrack.

---

### v3.7.0 — 2026-11-25

#### Double écran et orientation par écran

- Double écran : chaque slide est affectée à l'écran 1 (/) ou à l'écran 2 (/ecran/2), chaque fenêtre garde son propre cycle de slides et ses widgets.
- Orientation par écran (paysage, portrait, portrait inversé) dans Paramètres → Écrans ; la rotation inclut l'écran de pause et les widgets « page web ».
- Compatibilité totale : les slides existantes restent sur l'écran 1 et / reste l'écran principal.

---

### v3.6.0 — 2026-11-16

#### Depassements de seuil sur la TV

- Nouveau widget « Depassements de seuil » : saisies au-dessus du seuil de leur categorie sur les 7 derniers jours, toutes unites confondues
- Le widget se rafraichit avec le tableau de bord et affiche un etat vert quand toutes les saisies sont sous les seuils

---

### v3.5.4 — 2026-11-15

#### Cache et compression HTTP

- Le navigateur garde desormais les fichiers CSS, JavaScript et images une heure (Cache-Control public, max-age=3600), ce qui supprime une revalidation a chaque page ; les pages HTML et les reponses JSON sont compressees en gzip quand le navigateur l'accepte (Vary: Accept-Encoding).

---

### v3.5.3 — 2026-11-15

#### Attente de verrou SQLite

- Attente de verrou : chaque application attend jusqu'a 5 secondes qu'une ecriture concurrente libere la base SQLite, au lieu d'echouer immediatement sur un "database is locked".

---

### v3.5.2 — 2026-11-14

#### Version affichée dans À propos

- La carte « À propos » des paramètres affiche la version réelle de FabBoard au lieu d'un « v1.0 » figé.
- Le numéro vient de la constante APP_VERSION, désormais seule source de version côté serveur et dans les templates.

---

### v3.5.1 — 2026-11-14

#### Widgets plus légers sur Fabtrack

- Les widgets partageant la même source et la même période ne déclenchent plus qu'un seul appel vers Fabtrack par rafraichissement (cache de 10 secondes).
- Un rechargement forcé reste possible avec le paramètre refresh, et les erreurs ne sont jamais mises en cache.
- Nouvelle suite de tests FabBoard : bornes de période, proxy Fabtrack et comportement du cache sont couverts automatiquement.

---

### v3.5.0 — 2026-11-14

#### Période configurable par widget et listes enrichies

- Chaque widget Fabtrack choisit sa période : année scolaire (par défaut), mois en cours, semaine, jour ou cumul total.
- La période active est rappelée dans l'en-tête du widget (ex. « Année scolaire 2026-2027 »).
- Nouvelle limite d'éléments configurable pour le widget Répartition des activités, comme pour les activités récentes et les dernières consommations.
- Activités récentes et dernières consommations affichent le projet et la date de saisie, pour distinguer deux interventions du même type.
- Les compteurs, la répartition et les deux listes sont alimentés par les nouvelles routes Fabtrack (résumé par période et consommations filtrées).

---

### v3.4.0 — 2026-10-16

#### Widget page web et suivi des missions

- Nouveau widget page web : affichage plein écran d'un site dans une slide, avec conservation de l'iframe lors des changements de slide.
- Nouveau widget dernière étape de mission, alimenté par Fabtrack.

---

### v3.3.1 — 2026-05-18

#### Persistance des médias uploadés

- Les vidéos et images uploadées sont maintenant persistantes : le dossier static/uploads est monté en volume Docker (docker-data/uploads). Les fichiers ne sont plus perdus après un docker compose up --build.

---

### v3.3.0 — 2026-05-03

#### Pause hebdomadaire et indisponibilité prioritaire

- Nouvelle pause hebdomadaire récurrente configurable jour par jour (lundi à dimanche) avec horaires début/fin.
- Indisponibilité manuelle prioritaire ajoutée : prend le dessus sur toutes les slides tant qu'elle est active.
- Option d'heure de retour sur l'indisponibilité : affichable ou masquable via un simple switch.
- Taille de texte personnalisable pour la PAUSE et pour l'INDISPONIBILITÉ (réglages indépendants).
- Texte de pause personnalisé : titre et message configurables dans les paramètres.
- Nouveau mode image pour la pause récurrente (JPG/PNG) avec upload et aperçu.

---

### v3.2.0 — 2026-04-11

#### Popup de nouveautés et trace des évolutions

- Ajout de la popup Nouveautés pilotée par release-notes.json.
- Lien footer admin pour ouvrir l'historique des MAJ à la demande.
- Gestion du 'vu' local pour éviter les réaffichages répétés.

---

### v3.1.0 — 2026-04-10

#### Lisibilité dashboard horloge/timer

- Différence visuelle renforcée entre mode Horloge et mode Timer.
- Ajustement de hauteur pour le layout 'Large + 2/3 blocs' afin d'améliorer la lisibilité.

---

### v3.0.2 — 2026-03-21

#### Amélioration visuelle des missions

- Ajout de fonds couleurs pour les missions afin de clarifier l'état des cartes et leur priorité.

---

<!-- changelog:end -->

---

## 📄 Licence

MIT — © 2026 Fablab Loritz

---

## 🤝 Projet lié

[Fabtrack](https://github.com/fablabloritz-coder/Fabtrack) — Application de suivi des consommations Fablab
