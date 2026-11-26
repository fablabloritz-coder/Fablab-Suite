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
- Éditeur visuel de slides avec 17 gabarits (grilles jusqu'à 4×2 ou 3×3, blocs asymétriques et barre latérale)
- Largeur des colonnes et hauteur des lignes ajustables à la souris dans l'aperçu, par slide, avec réinitialisation en un clic
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

> **Politique de publication** — seules les versions effectivement publiées
> (`fabboard-vX.Y.Z`) reçoivent un tag et une release GitHub. Les versions
> intermédiaires d'un lot restent dans l'historique sans lien et ne sont pas
> taguées rétroactivement : les versions 3.8.3 à 3.8.11 sont regroupées dans
> la release 3.8.12, et la 3.8.13 sera livrée avec la release 3.9.0.

<!-- changelog:start -->

### v3.9.0 — 2026-11-26

#### Layouts variés et tailles personnalisables

- Sept nouveaux gabarits (grand gauche + 3 droite, grand droite + 2 gauche, grilles 4×1, 4×2 et 3×3, grand haut + 4 bas, barre gauche + grille 2×2) sont proposés en plus des dix existants.
- Chaque slide peut désormais ajuster à la souris la largeur de ses colonnes et la hauteur de ses lignes : l'outil « Ajuster » affiche des séparateurs dans l'aperçu, le glisser est sauvegardé automatiquement, et un bouton réinitialise la disposition.
- La personnalisation est stockée par slide dans une colonne optionnelle ; sans réglage, les gabarits gardent exactement leurs pistes égales et le ratio de hauteur historique. La migration est idempotente et ne modifie aucun layout existant.

---

### v3.8.13 — 2026-11-26

#### Cache des assets et widgets lisibles en cellules étroites

- Tous les CSS/JS locaux (style.css, utils.js, slides.css, parametres.js et Bootstrap) suivent désormais la version de l'application : un changement de version invalide leur cache au lieu de laisser d'anciens fichiers.
- Le timer passe en grille 2×2 sans séparateurs dans les cellules de moins de 300 px, et garde une seule ligne au-delà : plus de débordement à 1280/1366/1440, avec des unités lisibles dans les cellules étroites.
- La météo contient son chiffre principal dans sa boîte de ligne (line-height 1.2), sans débordement vertical.

---

### v3.8.12 — 2026-11-25

#### Défilement automatique : la page courante survit aux rafraîchissements

- Widget « Dépendances externes » : chaque rafraîchissement du store (30 s par défaut) réécrivait la liste et relançait le défilement en page 1. La page 2, à 53 px, ne restait affichée que quelques instants.
- Le rendu n'est réécrit que si le HTML change : à contenu identique, la liste, la position de défilement et le minuteur sont conservés. Un vrai changement de contenu repart en page 1, et le minuteur est relancé s'il s'était arrêté.
- Couverture : le pilote Node simule deux pages et deux tours (0 → 200 → 0 → 200) avec un refresh entre chaque ; la page 2 reste à 200 px sans réécriture ni minuteur recréé, et un changement de contenu réécrit une fois puis repart à 0. Vérifié aussi dans la slide sur deux cycles de rafraîchissement réels (intervalle 10 s).

---

### v3.8.11 — 2026-11-25

#### Écran de pause : largeur logique sans container-type

- Écran de pause : le texte se dimensionne désormais avec `--lw`, la même largeur logique que les widgets (1vw en paysage, 1vh en portrait), au lieu d'unités de conteneur (cqw).
- `container-type` est retiré de l'overlay : plus de containment (layout/style/inline-size) ni de containing block implicite pour d'éventuels descendants fixed, et plus de doublons de déclarations vw/cqw.
- Vérification navigateur : géométrie, centrage, padding et tailles de texte identiques au pixel en paysage, portrait et portrait inversé (1920×1080 et 1440×810) ; modes image et vidéo toujours en 1920×1080, `object-fit: cover`.

---

### v3.8.10 — 2026-11-25

#### Filtre de services simplifié, message d'attente raccourci

- Widget « Dépendances externes » : une seule normalisation (sans accents ni casse) sert désormais au filtre de services, aux statuts et aux regroupements ; le helper `sansAccents` et le remplacement ad hoc de `estTerminee` disparaissent.
- Quand aucun service ne correspond au filtre, le message ne liste plus jusqu'à 8 noms de services (plusieurs lignes sur une TV en portrait) : il reste « Aucune mission en attente des services sélectionnés ».
- Couverture : les six scénarios de filtre du pilote Node sont ramenés à trois — saisie partielle + plusieurs services + accents oubliés, noms complets, aucun résultat — avec une fixture de missions partagée ; le filtre vide reste couvert par les scénarios sans config `services`.

---

### v3.8.9 — 2026-11-25

#### Échéances du widget Dépendances externes : plus de décalage d'un jour

- Widget « Dépendances externes » : une échéance `YYYY-MM-DD` était lue à minuit UTC puis affichée en heure locale. À l'ouest de Greenwich, « 2026-11-15 » s'affichait « 14 nov. » et le décompte « En retard » pouvait glisser d'un jour.
- La date est désormais construite dans le fuseau du navigateur (date civile), sans changer le format ni les seuils des badges.
- Couverture : le pilote Node est rejoué avec `TZ=America/New_York` ; deux scénarios à date fixe vérifient qu'une échéance terminée et une échéance active restent au 15 novembre (échec avec l'ancienne lecture UTC).

---

### v3.8.8 — 2026-11-25

#### Filtre de services : la saisie partielle suffit

- Widget « Dépendances externes » : le filtre de services exigeait le nom exact — « Imprimerie » n'affichait rien alors que « Imprimerie municipale » fonctionnait.
- La comparaison accepte désormais une saisie partielle, sans tenir compte de la casse ni des accents (« periscolaire » trouve « Périscolaire »). Un nom complet continue de ne sélectionner que lui, et plusieurs saisies séparées par des virgules se cumulent.
- Quand le filtre ne correspond à rien, le message d'attente indique les services connus des missions, pour repérer une faute de frappe.
- Couverture : six scénarios de filtre dans le pilote Node (partiel, nom complet, multi, accents, filtre vide, inconnu).

---

### v3.8.7 — 2026-11-25

#### Réglage des missions terminées à nouveau visible

- Widget « Dépendances externes » : le réglage « Inclure les missions terminées » n'apparaissait pas dans le panneau de configuration d'un navigateur ayant déjà ouvert la page Slides — `slides.js` était chargé avec un numéro de cache figé (`?v=20260308-no-tenor`). Il suit désormais la version de l'application, comme `dashboard.js`.
- Le widget garde donc, comme le widget « Missions » avec « Afficher Terminé », la possibilité de ne pas afficher les missions terminées : case décochée par défaut, elles sont masquées.
- Couverture : test des pages (tous les scripts doivent être versionnés par `app_version`) et test du panneau de configuration du widget (case `inclure_terminees`, décochée par défaut).

---

### v3.8.6 — 2026-11-25

#### Dépendances externes : défilement automatique

- Widget « Dépendances externes » : dans une cellule 2×2, la liste dépassait la cellule et défilait en interne, mais personne ne fait défiler une TV — les groupes au-delà du visible restaient inaccessibles.
- La vue avance désormais seule : une page toutes les 10 s, chaque page démarrant sur une mission entière, puis retour en haut après la dernière. Rien ne bouge quand tout tient dans la cellule.
- La barre de défilement est masquée, elle n'avait plus d'utilité.
- Couverture : calcul des pages testé sous Node (contenu qui tient, alignement sur le haut des missions, bornes désordonnées, cas réel de la slide) ; cadence et remise en haut vérifiées dans une slide 2×2 à 1920×1080.

---

### v3.8.5 — 2026-11-25

#### Dépendances externes lisible à distance

- Widget « Dépendances externes » : à l'échelle 1 sur une TV 1920×1080, les titres de mission tombaient à 11,5 px et les badges à 8,8 px, illisibles à distance.
- Les tailles de texte suivent désormais la largeur logique de l'écran (`--lw`) avec des bornes basses et hautes : à 1920×1080 en échelle 1, titre de mission ~18 px, étape ~15 px, badges ~13 px. Les échelles 1,25 à 4 restent multiplicatives.
- Vérification navigateur dans une vraie slide 2×2 à 1920×1080 (paysage, échelle 1) : tailles relevées, aucun débordement horizontal, liste défilante conservée.

---

### v3.8.4 — 2026-11-25

#### Missions terminées lisibles dans les dépendances

- Widget « Dépendances externes » : une mission terminée pouvait s'afficher « À faire » avec une échéance « En retard (10j) ». Le badge « Terminé » s'appuie désormais sur un statut normalisé (casse, accents) et, à défaut de statut, sur `completed_at` des payloads antérieurs au marqueur.
- L'échéance d'une mission terminée reste affichée pour situer la mission, en style neutre : plus de « En retard » ni d'« Aujourd'hui ».
- L'option « Inclure les missions terminées » accepte aussi 1 et "1", pour les configurations éditées à la main qui la laissaient sans effet.
- Couverture : pilote Node `FabBoard/tests/js/dependances_widget_check.js` et 7 tests unittest sur le rendu (statut, échéance neutre, option, non-régression d'une mission active en retard).

---

### v3.8.3 — 2026-11-25

#### Texte des widgets à l'échelle de la largeur logique

- Les tailles de texte des widgets (horloge, météo, texte libre, timer) étaient en vw, c'est-à-dire sur la largeur physique du viewport : en portrait le body est tourné, sa largeur logique est plus étroite, et l'horloge débordait de sa cellule (438 px de contenu pour 384 px de place).
- Nouvelle variable --lw (1vw en paysage, 1vh en portrait et portrait inversé) utilisée par les widgets : le rendu paysage reste identique au pixel, le portrait se dimensionne sur la largeur réelle de l'écran.
- Vérification navigateur : horloge et widget texte mesurés en paysage, portrait et portrait inversé, débordement de cellule contrôlé dans les deux portraits.

---

### v3.8.2 — 2026-11-25

#### Texte de pause lisible en portrait

- Écran de pause : le texte se dimensionnait sur la largeur physique du viewport (vw) alors que le body tourné a une largeur logique plus étroite ; le titre débordait et se repliait en colonne sur un écran en portrait.
- L'overlay devient un conteneur de requête et le texte de pause se dimensionne sur sa largeur logique (cqw) : 48,6 px au lieu de 86,4 px sur un 1440×810 en portrait, paysage inchangé.
- Vérification navigateur : pause en portrait et portrait inversé (échelle 150 %, message long, redimensionnement pendant la pause) et widget « page web » aligné sur sa cellule après rotation et resize.

---

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
