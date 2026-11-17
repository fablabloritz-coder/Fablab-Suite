<p align="center">
  <img src="static/img/Fabtrack%20(clair).png" alt="FabTrack Logo" height="80">
</p>

<h1 align="center">FabTrack</h1>

<p align="center">
  <strong>Application de suivi des consommations pour Fablabs</strong><br>
  Flask · SQLite · Bootstrap 5.3 · Chart.js
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/flask-3.1-green?logo=flask" alt="Flask">
  <img src="https://img.shields.io/badge/bootstrap-5.3-purple?logo=bootstrap" alt="Bootstrap">
  <img src="https://img.shields.io/badge/sqlite-3-lightblue?logo=sqlite" alt="SQLite">
  <img src="https://img.shields.io/badge/license-MIT-orange" alt="License">
</p>

---

## 📋 Présentation

**FabTrack** est une application web locale de suivi et d'analyse des consommations de matériaux et de l'utilisation des machines d'un Fablab. Elle remplace un suivi par tableur par une interface moderne, rapide et entièrement autonome — **aucune dépendance cloud**.

La base de données par défaut est livrée **neutre** : les machines et matériaux sont pré-configurés, mais les classes, préparateurs et référents sont vides pour que chaque fablab puisse personnaliser son installation.

<img width="1918" height="917" alt="image" src="https://github.com/user-attachments/assets/9b3909b6-e80c-4fb3-865d-842a234fcd8d" />
<img width="1917" height="689" alt="image" src="https://github.com/user-attachments/assets/d049cb31-1f66-4347-95c5-0db0bbd8fe4a" />
<img width="1918" height="915" alt="image" src="https://github.com/user-attachments/assets/c6623aae-9cbb-4d57-b3d5-5b198000b03b" />



---

## ✨ Fonctionnalités

| Module | Description |
|--------|-------------|
| **Saisie multi-action** | Formulaire dynamique : informations projet (date, intitulé, classe, référent) + N actions indépendantes (type, machine, matériau, champs spécifiques) enregistrées en une seule requête |
| **Historique** | Tableau paginé avec filtres combinables (date, type, machine, classe, référent), modification et suppression inline |
| **Statistiques** | Tableaux de bord Chart.js : répartition par type, timeline, top machines/classes, activité journalière (par heure, jour de semaine, préparateur), détail impression papier (Couleur/N&B) |
| **État des machines** | Suivi temps réel : ✅ Disponible / 🔧 En réparation / ❌ Hors service, avec notes permanentes et raison de réparation |
| **Calculateur** | Calcul de surface (rectangle, cercle, triangle) avec presets papier A0–A5, zones machine, et envoi direct vers le formulaire de saisie |
| **Paramètres** | CRUD complet pour toutes les entités, images uploadées, champs personnalisés, slider taille icônes, informations du fablab personnalisables |
| **Import / Export** | Export CSV complet ou filtré (BOM UTF-8 + `;`), gabarits d'import, import CSV en masse |
| **Sauvegarde & Restauration** | Sauvegardes automatiques (journalières/hebdomadaires) ou manuelles au format `.fabtrack`, chemin personnalisable (serveur réseau, disque externe…), export/import entre instances |
| **Démonstration** | Génération de ~150 consommations fictives réalistes avec classes, préparateurs et référents fictifs |

---

## 🚀 Déploiement

### 🐳 Docker (recommandé)

Docker est la méthode officielle — comportement identique en local et en production sur serveur Ubuntu. Aucune installation Python requise.

**Prérequis :** [Docker Desktop](https://docs.docker.com/get-docker/) (Windows/macOS) ou Docker Engine (Linux).

```bash
git clone https://github.com/fablabloritz-coder/Fabtrack.git
cd Fabtrack
cp .env.example .env        # ajustez FABTRACK_PORT si besoin
docker compose up -d --build
```

L'application est accessible à **http://localhost:5555** (ou le port configuré dans `.env`).

---

### 🪟 Alternative Windows (sans Docker)

> Pour les environnements sans Docker. Python 3.10+ requis.

| Script | Rôle |
|--------|------|
| `installer.bat` | Crée le venv, installe les dépendances, prépare les dossiers |
| `lancer.bat` | Libère le port 5555 si occupé, lance le serveur et ouvre le navigateur |

### 👨‍💻 Alternative manuelle (développeurs)

```bash
git clone https://github.com/fablabloritz-coder/Fabtrack.git
cd Fabtrack
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python app.py
```

L'application démarre sur **http://localhost:5555**.

---

### Procédures Docker détaillées

Fabtrack est prêt à être exécuté en conteneur avec persistance de la base SQLite et des images uploadées.

#### 1) Lancer en mode serveur

```bash
docker compose up -d --build
```

- Application : `http://localhost:5555`
- Données persistées sur le disque hôte :
    - `./docker-data/data` (SQLite, sauvegardes, configuration)
    - `./docker-data/uploads` (images des entités)

Arrêt :

```bash
docker compose down
```

#### 2) Variable d'environnement (serveur)

```bash
# Clé secrète Flask (recommandé en production)
export FLASK_SECRET_KEY="votre_cle_longue_et_aleatoire"

# Fuseau horaire (défaut : Europe/Paris)
export TZ="Europe/Paris"
```

Vous pouvez adapter les chemins hôte via un fichier `.env` :

```bash
FABTRACK_DATA_PATH=./docker-data/data
FABTRACK_UPLOADS_PATH=./docker-data/uploads
```

#### 3) Procédures d'exploitation Docker (Ubuntu serveur)

Commandes a executer depuis le dossier `Fabtrack/`.

Mise a jour applicative (code + image + conteneur) :

```bash
cd /chemin/vers/Fabtrack
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

Arret du service :

```bash
cd /chemin/vers/Fabtrack
docker compose stop
```

Relance du service (sans rebuild) :

```bash
cd /chemin/vers/Fabtrack
docker compose start
```

Redemarrage complet du service (sans rebuild) :

```bash
cd /chemin/vers/Fabtrack
docker compose restart
```

Relance avec rebuild force (apres mise a jour ou changement Dockerfile) :

```bash
cd /chemin/vers/Fabtrack
docker compose down
docker compose up -d --build
```

Diagnostic rapide :

```bash
cd /chemin/vers/Fabtrack
docker compose ps
docker logs --tail=120 fabtrack
```

En cas de conflit `container name ... already in use` :

```bash
docker stop fabtrack 2>/dev/null || true
docker rm fabtrack
cd /chemin/vers/Fabtrack
docker compose up -d --build
```

Depannage : application inaccessible depuis le navigateur

1. Verifier que le conteneur tourne :

```bash
cd /chemin/vers/Fabtrack
docker compose ps
docker ps -a --filter "name=^/fabtrack$"
```

2. Lire les logs de demarrage :

```bash
docker logs --tail=200 fabtrack
```

3. Tester localement sur le serveur :

```bash
curl -I http://127.0.0.1:5555
```

4. Si le service repond en local mais pas depuis un autre poste, ouvrir le firewall :

```bash
sudo ufw allow 5555/tcp
sudo ufw status
```

5. Si l'etat est incoherent, remise a plat sans perte de donnees :

```bash
docker stop fabtrack 2>/dev/null || true
docker rm fabtrack 2>/dev/null || true
cd /chemin/vers/Fabtrack
docker compose up -d --build
```

#### 4) Sauvegarde / restauration

- Données critiques à conserver :
    - `docker-data/data/fabtrack.db` (base de données)
    - `docker-data/data/backup_config.json` (configuration sauvegardes)
    - `docker-data/uploads/` (images uploadées)
- Utilisez aussi les fonctions de sauvegarde intégrées pour exporter des archives `.fabtrack`.

#### 5) Déploiement multi-applications (Fablab Suite)

Fabtrack fait partie de la FabLab Suite :

| Application | Description | Port |
|---|---|---|
| **[FabHome](https://github.com/fablabloritz-coder/FabHome)** | Hub central — portail et dashboard | 3001 |
| **[PretGo](https://github.com/fablabloritz-coder/PretGo)** | Gestion de prêts de matériel | 5000 |
| **Fabtrack** | Suivi des consommations machines | 5555 |
| **[FabBoard](https://github.com/fablabloritz-coder/FabBoard)** | Dashboard TV temps réel | 5580 |

Pour déployer toute la suite en une commande, un `docker-compose.yml` unifié est disponible à la racine du dépôt parent [fabsuite-spec](https://github.com/fablabloritz-coder/fabsuite-spec).

---

## 🏗️ Architecture

```
Fabtrack/
├── app.py                  # Application Flask — routes, API REST, backups
├── models.py               # Schéma SQLite, migrations, seed, démo
├── requirements.txt        # Dépendances Python (Flask, Werkzeug)
├── installer.bat           # Script d'installation Windows
├── lancer.bat              # Script de lancement Windows
├── fabtrack.db             # Base SQLite (générée automatiquement)
├── backup_config.json      # Configuration sauvegardes (généré)
├── backups/                # Sauvegardes .fabtrack (ou chemin personnalisé)
├── static/
│   ├── css/
│   │   ├── style.css       # Thème principal (orange/ambre)
│   │   └── dark-mode.css   # Surcharges mode sombre
│   ├── img/                # Logos et images matériaux
│   └── uploads/            # Images uploadées (entités)
└── templates/
    ├── base.html           # Layout, navbar, dark mode, toasts, escHtml()
    ├── index.html          # Saisie multi-action
    ├── historique.html      # Historique paginé + filtres
    ├── statistiques.html    # Tableaux de bord Chart.js
    ├── parametres.html      # Gestion référentiels + champs personnalisés
    ├── etat_machines.html   # État des machines (statut, notes, réparation)
    ├── calculateur.html     # Calculateur de surface
    └── export.html          # Import/Export CSV, sauvegarde, démo, réinitialisation
```

### Stack technique

| Composant | Technologie |
|-----------|-------------|
| Backend | Flask 3.1 (Python) |
| Base de données | SQLite 3 (WAL mode, foreign keys) |
| Frontend | Bootstrap 5.3.2 + Bootstrap Icons 1.11.2 |
| Graphiques | Chart.js 4.4.7 |
| Polices | Inter (Google Fonts) |
| Serveur | Werkzeug (dev) — port 5555 |

> Aucune dépendance npm, aucun build. Toutes les librairies front sont chargées via CDN.

---

## 🗄️ Modèle de données

### Schéma principal

```
              ┌──────────────────┐
              │  types_activite  │
              └────────┬─────────┘
                       │ 1
                       │
                       │ N
              ┌────────┴─────────┐         ┌─────────────────┐
              │     machines     │────N:M───│    materiaux     │
              └────────┬─────────┘         └─────────────────┘
                       │                via materiau_machine
                       │                  (table de jonction)
              ┌────────┴─────────┐
              │  consommations   │──── FK → preparateurs
              │ (dénormalisées)  │──── FK → classes
              └──────────────────┘──── FK → referents
```

### Principes clés

| Concept | Détail |
|---------|--------|
| **Table de jonction** | `materiau_machine` lie chaque matériau aux machines compatibles (N :M). Plus de doublons de matériaux. |
| **Dénormalisation** | Chaque consommation stocke les noms (`nom_preparateur`, `nom_machine`, etc.) en plus des FK, garantissant la lisibilité même après suppression d'une entité. |
| **Soft-delete** | Les entités ne sont jamais supprimées physiquement — le champ `actif` passe à `0`. Les consommations existantes restent intactes. |
| **Principes de conception** | Chaque machine porte un attribut `principes_conception` (`ajout`, `enlevement`, `deformation`) pour classification pédagogique. |
| **Migration automatique** | `_migrate_db()` ajoute les colonnes manquantes pour les bases existantes, assurant la rétrocompatibilité. |
| **Base neutre** | L'installation par défaut contient uniquement les machines, matériaux et types d'activité. Les classes, préparateurs et référents sont à configurer par chaque fablab. |

---

## 🛠️ Parc machines par défaut

L'application est pré-configurée avec **13 machines** :

| Machine | Type | Qté | Marque | Zone de travail |
|---------|------| :--- :|--------|-----------------|
| Creality CR10-S | Impression 3D | ×2 | Creality | 300×300×400 mm |
| Creality Ender 3 | Impression 3D | ×1 | Creality | 220×220×250 mm |
| Raise 3D Pro | Impression 3D | ×1 | Raise3D | 305×305×305 mm |
| Raise 3D Pro 2 | Impression 3D | ×1 | Raise3D | 305×305×300 mm |
| Raise 3D Pro 3 | Impression 3D | ×1 | Raise3D | 300×300×300 mm |
| Creabot D600 | Impression 3D | ×1 | Creabot | 600×600×600 mm |
| JAMP 78 Moy | Découpe Laser | ×1 | JAMP | 780×460 mm |
| Grande Strato | CNC / Fraisage | ×1 | — | 1200×900 mm |
| Petite Strato | CNC / Fraisage | ×1 | — | 600×450 mm |
| Formech 300XQ | Thermoformage | ×1 | Formech | 300×300 mm |
| Kyocera | Impression Papier | ×1 | Kyocera | — |
| Epson Eco-tank | Impression Papier | ×1 | Epson | — |
| Traceur HP | Impression Papier | ×1 | HP | Grand format |

> Les machines sont entièrement personnalisables depuis les Paramètres (ajout, modification, suppression).

<img width="1915" height="916" alt="image" src="https://github.com/user-attachments/assets/822cc63e-b7e8-4808-a831-c8299c6c34af" />


### Matériaux par imprimante papier

| Imprimante | Formats | Mode |
|------------|---------|------|
| **Kyocera** | A3, A4 | N&B uniquement |
| **Epson Eco-tank** | A4 | Couleur + N&B |
| **Traceur HP** | A0 → A5 | Couleur + N&B |

---

## 📊 Types d'activité

| Type | Icône | Unité | Principe |
|------| :----- :|-------|----------|
| Impression 3D | 🖨️ | g | Ajout |
| Découpe Laser | ⚡ | m² | Enlèvement |
| CNC / Fraisage | ⚙️ | m² | Enlèvement |
| Impression Papier | 📄 | feuilles | — |
| Thermoformage | 🔥 | feuilles | Déformation |
| Bricolage | 🔧 | — | — |
| Broderie | 🧵 | — | Ajout |

Les types sont entièrement personnalisables (ajout, modification, suppression) depuis Paramètres.

### Champs spécifiques par type

| Type | Champs de saisie |
|------|------------------|
| Impression 3D | Poids (g) |
| Découpe Laser / CNC | Longueur, largeur, surface auto (mm/cm/m) |
| Impression Papier | Nb feuilles, format (A0–A5), couleur/N&B |
| Thermoformage | Nb feuilles plastique, type (opaque/transparente) |

---

## 💾 Sauvegarde & Restauration

### Format `.fabtrack`

Les sauvegardes sont des copies complètes de la base SQLite avec l'extension `.fabtrack`. Elles sont directement importables sur une autre instance de FabTrack.

### Fonctionnalités

| Fonctionnalité | Description |
|----------------|-------------|
| **Sauvegarde automatique** | Fréquence configurable : désactivée / journalière / hebdomadaire |
| **Sauvegarde manuelle** | Création à la demande depuis la page Export |
| **Emplacement personnalisé** | Chemin de sauvegarde configurable (dossier local, disque réseau, serveur externe…) avec test d'écriture |
| **Export** | Téléchargement de la base actuelle ou d'une sauvegarde existante en `.fabtrack` |
| **Import** | Restauration d'une base `.fabtrack` avec validation SQLite + tables requises. Sauvegarde de sécurité automatique avant remplacement |
| **Gestion** | Liste des sauvegardes (nom, date, taille), téléchargement individuel, suppression |
| **Nettoyage** | Maximum configurable de sauvegardes conservées (30 par défaut), rotation automatique |

### Configuration

La configuration est stockée dans `backup_config.json` :

```json
{
  "frequency": "daily",
  "last_backup": "2026-02-27 15:18:08",
  "max_backups": 30,
  "backup_path": ""
}
```

- `backup_path` vide = dossier par défaut `./backups/`
- `backup_path` renseigné = chemin personnalisé (ex : `\\serveur\partage\fabtrack_backups` ou `D:\sauvegardes`)

---

## 📈 Statistiques

Les tableaux de bord incluent :

- **Résumé** — Total interventions, poids 3D (g), surface découpe (m²), feuilles papier (Couleur/N&B)
- **Répartition par type** — Doughnut chart avec pourcentages
- **Timeline** — Courbes par type d'activité (jour/semaine/mois)
- **Consommation 3D** — Par matériau (PLA, PETG, ABS, TPU)
- **Surface découpe** — Laser + CNC combinés, par matériau
- **Impressions papier** — Répartition Couleur vs N&B + évolution temporelle
- **Top 10 machines / classes** — Bar charts
- **Activité journalière** — Répartition par heure, par jour de semaine, par préparateur
- **Filtres temporels** — Presets (7j, 30j, 90j, année, tout) ou dates personnalisées

<img width="1915" height="916" alt="image" src="https://github.com/user-attachments/assets/4cdd03a3-6557-4f13-ba88-26cd449e6425" />


---

## 📐 Calculateur de surface

- 3 formes : **rectangle**, **cercle**, **triangle**
- Unités : mm, cm, m avec conversion automatique
- **Presets papier** : A0 à A5
- **Zones machines** : remplissage automatique depuis la fiche technique
- **Historique** des calculs (localStorage)
- **Envoi direct** de la surface calculée dans le formulaire de saisie

<img width="1915" height="918" alt="image" src="https://github.com/user-attachments/assets/e6960452-c1ad-4e2d-8169-bbded19197f6" />

---

## 📥 Import / Export

### Export CSV
- **Complet** : toutes les consommations
- **Filtré** : par période et/ou type d'activité
- Format Excel-compatible (BOM UTF-8, séparateur `;`)

### Import CSV
- **Gabarits** téléchargeables pour 5 entités (machines, matériaux, classes, référents, préparateurs)
- **Import en masse** avec détection automatique du séparateur
- Support des liens matériau → machines dans le gabarit matériaux

---

## 🔌 API REST

Toutes les données sont accessibles via une API JSON complète :

### Données de référence

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/reference` | Toutes les données de référence (machines, matériaux, liens, etc.) |

### Consommations

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/consommations` | Liste paginée avec filtres (date, type, classe, etc.) |
| `POST` | `/api/consommations` | Créer une consommation |
| `POST` | `/api/consommations/batch` | Créer plusieurs consommations (multi-action) |
| `PUT` | `/api/consommations/<id>` | Modifier une consommation |
| `DELETE` | `/api/consommations/<id>` | Supprimer une consommation |

### Statistiques

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/stats/summary` | Résumé statistique (totaux, répartitions) |
| `GET` | `/api/stats/timeline` | Données timeline pour graphiques |
| `GET` | `/api/stats/activity` | Activité journalière (heure, jour, préparateur) |
| `GET` | `/api/stats/school-years` | Totaux de consommation par année scolaire (année en cours + précédentes) |
| `GET` | `/api/stats/school-year-summary` | Résumé (compteurs, répartitions) borné à l'année scolaire configurée |

### Gestion des entités (CRUD)

Pour chaque entité (`types_activite`, `machines`, `materiaux`, `classes`, `referents`, `preparateurs`) :

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `POST` | `/api/<entity>` | Créer |
| `PUT` | `/api/<entity>/<id>` | Modifier |
| `DELETE` | `/api/<entity>/<id>` | Désactiver (soft-delete) |
| `GET` | `/api/<entity>/<id>/usage-count` | Nombre de saisies liées |
| `POST` | `/api/<entity>/<id>/replace-and-delete` | Remplacer les dépendances puis désactiver |
| `POST` | `/api/<entity>/mass-delete` | Désactivation en masse |

### Sauvegarde & Restauration

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/backup/settings` | Paramètres de sauvegarde |
| `PUT` | `/api/backup/settings` | Modifier fréquence, chemin, max sauvegardes |
| `POST` | `/api/backup/create` | Créer une sauvegarde manuelle |
| `GET` | `/api/backup/list` | Lister toutes les sauvegardes |
| `GET` | `/api/backup/export/<filename>` | Télécharger une sauvegarde |
| `GET` | `/api/backup/export-current` | Exporter la base actuelle en `.fabtrack` |
| `POST` | `/api/backup/import` | Importer un fichier `.fabtrack` |
| `DELETE` | `/api/backup/delete/<filename>` | Supprimer une sauvegarde |
| `POST` | `/api/backup/validate-path` | Valider un chemin de sauvegarde |

### Autres

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/export/csv` | Télécharger CSV (complet ou filtré) |
| `GET` | `/api/template/<entity>` | Télécharger gabarit CSV |
| `POST` | `/api/import/<entity>` | Importer un CSV |
| `PUT` | `/api/machines/<id>/statut` | Changer statut machine |
| `POST` | `/api/upload-image` | Upload d'image pour une entité |
| `GET/POST` | `/api/custom-fields` | Gestion champs personnalisés |
| `PUT/DELETE` | `/api/custom-fields/<id>` | Modifier/supprimer champ personnalisé |
| `GET/POST` | `/api/custom-field-values/<type>/<id>` | Valeurs des champs personnalisés |
| `GET` | `/api/settings/school-year` | Période de l'année scolaire utilisée par les statistiques |
| `PUT` | `/api/settings/school-year` | Configurer l'année scolaire de date à date (dates vides = valeurs par défaut) |
| `POST` | `/api/demo/generate` | Générer données de démonstration |
| `POST` | `/api/reset` | Réinitialiser la base (confirmation requise) |

---

## 🔒 Sécurité

| Mesure | Détail |
|--------|--------|
| **XSS** | Fonction globale `escHtml()` appliquée à tous les contenus utilisateur injectés via JavaScript |
| **SQL Injection** | Requêtes paramétrées exclusivement (pas de concaténation de valeurs utilisateur) |
| **Whitelist** | `_resolve_nom()` accepte uniquement les tables autorisées |
| **Validation** | Pagination bornée (`per_page` max 10 000), parsing protégé des entiers, exceptions typées |
| **Soft-delete** | Suppression sécurisée avec vérification des dépendances et option de remplacement |
| **Réinitialisation** | Requiert de taper `REINITIALISER` en majuscules pour confirmer |
| **Sauvegarde import** | Validation SQLite + tables requises avant import, sauvegarde automatique pré-remplacement |
| **Chemin sauvegarde** | Test d'écriture automatique avant validation d'un chemin personnalisé |
| **Secret key** | Lecture depuis `FABTRACK_SECRET` (variable d'environnement) avec fallback |

---

## 🌙 Mode sombre

Basculez entre mode clair et sombre via le bouton 🌙/☀️ dans la navbar. Le choix est sauvegardé dans le `localStorage`. Les logos s'adaptent automatiquement.

---

## 🧪 Démonstration & Réinitialisation

Depuis la page **Export** :

- **Générer une démo** : crée ~150 consommations fictives réalistes réparties sur 6 mois, avec des **préparateurs fictifs** (Préparateur A, B, C…), des **classes fictives** (Classe 1A, 1B, 2A…, BTS, Licence Pro) et des **référents fictifs** de différentes catégories (professeurs, agents techniques, demandes extérieures, administration)
- **Réinitialiser** : supprime toutes les données (consommations, classes, préparateurs, référents) et recrée les tables avec uniquement les **machines et matériaux par défaut**. Les types d'activité sont également recréés.

---

## 📜 Changelog

<!-- changelog:start -->

### v2.13.0 — 2026-11-17

#### Depassements de seuil sur la timeline et export

- Semaines ISO : la timeline regroupe par lundi et affiche « Semaine du JJ/MM », y compris autour du 1er janvier
- Les periodes contenant une saisie au-dessus du seuil sont encadrees en rouge ; les seuils en vigueur et la liste des depassements (valeur, seuil, ecart, referent, classe) sont affiches et exportables en CSV
- Une periode sans donnee affiche un message explicite au lieu de cartes a zero ; un test DOM verifie le clic sur une periode

---

### v2.12.0 — 2026-11-17

#### Pas de regroupement adapte a la periode

- Depuis l'historique des seuils, le pas de la timeline suit la duree de la periode : jour jusqu'a 31 jours, semaine jusqu'a 120 jours, mois au-dela
- Les periodes ouvertes (premier seuil ou periode en cours) restent au mois ; le choix manuel du pas reste possible ensuite

---

### v2.11.1 — 2026-11-17

#### Filtre d'annee scolaire synchronise

- Apres un clic sur une periode de l'historique des seuils, le selecteur d'annee scolaire passe sur « Periode personnalisee » et le texte d'aide decrit les dates appliquees
- Le raccourci de periode actif est retire pour ne plus afficher une annee scolaire qui ne correspond pas aux dates

---

### v2.11.0 — 2026-11-16

#### Periodes de seuil cliquables

- Historique des seuils : un clic sur une periode applique ses dates aux statistiques et au tableau des plus grosses saisies
- On voit ainsi les saisies qui ont produit les alertes de cette configuration, y compris pendant les periodes desactivees ; la periode en cours s'arrete a aujourd'hui

---

### v2.10.0 — 2026-11-16

#### Historique des seuils dans Statistiques

- Statistiques : nouvel historique des seuils par categorie, avec les valeurs, les dates de changement et les periodes desactivees
- La periode en cours est mise en evidence et l'historique se rafraichit apres l'enregistrement des seuils
- L'API des seuils expose aussi l'historique complet, deja stocke par l'application

---

### v2.9.4 — 2026-11-16

#### Reactivation de seuil datee

- Desactiver un seuil puis le reactiver ne le traite plus comme un premier seuil : la reactivation est datee et ne s'applique qu'aux saisies posterieures
- Les saisies anterieures a la desactivation gardent l'ancien seuil, et celles de la periode desactivee ne sont plus signalees

---

### v2.9.3 — 2026-11-16

#### Seuil 2.9.0 conserve apres modification

- Un seuil herite de 2.9.0 puis modifie continue de couvrir tout l'historique : les saisies anterieures gardent l'ancien seuil au lieu de perdre toute alerte
- Le changement date s'applique a partir du jour de la modification, sans requalifier le passe

---

### v2.9.2 — 2026-11-16

#### Seuils existants rattaches a l'historique

- Un seuil enregistre en 2.9.0, reduit a une seule entree datee, couvre desormais tout l'historique : les anciennes saisies hors seuil redeviennent visibles
- Des qu'un seuil est modifie, la nouvelle valeur reste datee et ne requalifie pas les saisies anterieures

---

### v2.9.1 — 2026-11-16

#### Premier seuil applique a l'historique

- Un seuil defini pour la premiere fois s'applique a toutes les saisies deja enregistrees : les anciennes saisies hors seuil deviennent visibles
- Les modifications ulterieures restent datees : elles ne requalifient pas les saisies anterieures, qui gardent le seuil de leur epoque

---

### v2.9.0 — 2026-11-16

#### Seuils dates et corrections guidees

- Un avertissement non bloquant signale des la saisie une surface incoherente avec les dimensions ou un depassement de seuil, et la saisie peut etre confirmee
- Le tableau des plus grosses saisies propose « Corriger » pour remplacer une surface en cm2/mm2 par celle deduite des dimensions
- L'audit en ligne de commande lit les seuils par categorie et peut appliquer les corrections avec --corriger --appliquer, apres sauvegarde de la base
- Les seuils sont dates : modifier un seuil ne signale plus retroactivement les saisies anterieures
- Notification FabSuite et widget FabBoard « Depassements de seuil » pour les saisies des 7 derniers jours

---

### v2.8.0 — 2026-11-16

#### Seuils d'alerte par categorie

- Statistiques : un seuil d'alerte saisissable pour chaque categorie (g, m2 ou feuilles), avec mise en evidence des saisies qui le depassent
- Le tableau des plus grosses saisies suit la categorie choisie et affiche sa quantite dans la bonne unite, triee par valeur ou par date
- Une saisie signalee reste comptee une seule fois, meme si elle cumule depassement de seuil et incoherence d'unite

---

### v2.7.5 — 2026-11-16

#### Plus grosses surfaces triables

- Statistiques : nouveau tableau des plus grosses saisies de la periode, triable par surface (decroissant par defaut) ou par date
- Chaque saisie est confrontee a ses dimensions : une surface en cm2 ou mm2, une valeur au-dela de 2 m2 ou une surface sans dimensions est signalee
- Le diagnostic est partage avec audit_surface.py, donc les seuils sont identiques a l'audit en ligne de commande

---

### v2.7.4 — 2026-11-16

#### Surfaces de decoupe verifiables

- Corriger une longueur ou une largeur dans l'historique met desormais a jour la surface enregistree, au lieu de conserver l'ancienne valeur
- Nouveau script audit_surface.py (lecture seule) : totaux par annee scolaire et par mois, plus grosses saisies, et signalement des surfaces incoherentes avec les dimensions (erreur cm2 ou mm2)
- Tests permanents sur les bornes de l'annee scolaire et sur le multiplicateur d'occurrences

---

### v2.7.3 — 2026-11-15

#### Statut des imprimantes sans blocage

- Les statuts Raise3D sont interroges en parallele puis memorises 30 s : un widget ou une notification ne bloque plus sur une imprimante hors ligne
- Notifications et widget Raise3D bornes a 2 s par appel, ce qui rend de nouveau visibles dans FabHome les notifications Fabtrack
- Tests permanents sur le cache et le parallelisme des statuts d'imprimantes

---

### v2.7.2 — 2026-11-15

#### Cache et compression HTTP

- Le navigateur garde desormais les fichiers CSS, JavaScript et images une heure (Cache-Control public, max-age=3600), ce qui supprime une revalidation a chaque page ; les pages HTML et les reponses JSON sont compressees en gzip quand le navigateur l'accepte (Vary: Accept-Encoding).

---

### v2.7.1 — 2026-11-15

#### Attente de verrou SQLite

- Attente de verrou : chaque application attend jusqu'a 5 secondes qu'une ecriture concurrente libere la base SQLite, au lieu d'echouer immediatement sur un "database is locked".

---

### v2.7.0 — 2026-11-14

#### Garde-fou contre les doubles saisies

- Une saisie identique à une autre (même type, machine, matériau, quantités et projet, ainsi que même préparateur lorsqu'il est renseigné de part et d'autre) à moins de deux minutes d'intervalle est maintenant signalée.
- Un message demande confirmation avant d'enregistrer malgré tout, ce qui évite les doublons créés par un double clic sur Enregistrer.
- Le garde-fou fonctionne aussi sur la saisie multi-actions : la ligne concernée est indiquée et rien n'est enregistré tant que l'on n'a pas confirmé.

---

### v2.6.0 — 2026-11-14

#### Missions — Date de création automatique

- Les nouvelles missions enregistrent automatiquement leur date de création et l'affichent sur la carte kanban (« Créée le … ») ainsi que, en lecture seule, dans la fiche de la mission.
- Les missions créées avant cette version n'ont pas de date de création : rien n'est affiché pour elles (aucun remplissage rétroactif).
- La date de création est reprise dans les exports de l'historique (CSV, HTML, PDF) sous « Date creation » ; vide pour les missions antérieures.

#### Statistiques — Année scolaire

- La page Statistiques s'ouvre désormais sur l'**année scolaire en cours** au lieu du cumul historique.
- Nouveau sélecteur **Année scolaire** : année en cours, années précédentes et « Tout l'historique » (les anciennes années restent consultables).
- Nouveau tableau **Consommation par année scolaire** (interventions, filament 3D, surface découpée, feuilles) ; un clic sur une ligne affiche le détail de cette année.
- Période configurable de date à date dans Paramètres → Informations du Fablab → « Année scolaire des statistiques » (par défaut : 1er septembre → 31 août).
- Les widgets FabBoard « Compteurs Fabtrack » et « Répartition activités » utilisent le même cadrage (via `/api/stats/school-year-summary`) et affichent l'année concernée, au lieu du cumul historique. Avec « Activités récentes » et « Dernières consommations » (via `/api/fabtrack/consommations`), leur période est configurable widget par widget (année scolaire, mois, semaine, jour, cumul) et affichée à côté du titre.
- Export CSV des statistiques : récupération paginée complète (plus de troncature silencieuse à 10 000 lignes).

**Migration de base de données :**
- Ajout de la colonne `date_creation TEXT` sur la table `missions` (migration idempotente, sans remplissage rétroactif).

<!-- manuel -->

---

### v2.5.1 — 2026-11-02

#### Fenêtre de mission plus lisible

- Fenêtre de mission en deux colonnes : détails à gauche, timeline des étapes à droite.
- Dernière étape affichée directement sur les cartes du kanban.
- Étapes en zone de texte multiligne et fenêtre agrandie pour les longues descriptions.
- Correction d'une erreur JavaScript dans le kanban (constante de catégorie supprimée par erreur).

---

### v2.5.0 — 2026-10-19

#### Étapes de mission, recherche et exports enrichis

- Chaque mission peut maintenant contenir des étapes datées, avec timeline d'avancement.
- Recherche dans les missions terminées et dans les étapes, avec exports filtrés.
- Les exports CSV, HTML et PDF incluent les étapes des missions.

---

### v2.4.1 — 2026-05-18

#### Corrections saisie et historique

- Correction du calcul de surface pour les activités multi-occurrences : le champ occurrence était toujours lu depuis le panneau Impression 3D même lors d'une Découpe Laser/CNC.
- Historique > modal d'édition : les champs Format papier et Type de feuille sont maintenant des listes déroulantes (cohérentes avec la saisie principale).
- Historique > modal d'édition : les secondes de l'heure de saisie sont désormais préservées lors d'une modification, évitant le déplacement de la ligne dans le tri.

---

### v2.4.0 — 2026-05-16

#### Filtres avancés dans les statistiques et les exports

- Trois nouveaux filtres dans la page Statistiques : Référent, Classe et Mot-clé projet.
- Les filtres s'appliquent à la fois aux graphiques (actualisation) et aux exports.
- Export CSV : lignes de métadonnées en tête de fichier indiquant les filtres actifs.
- Export HTML : encart 'Filtres appliqués' intégré dans le fichier exporté.
- Export PDF (impression) : récapitulatif des filtres affiché uniquement à l'impression.
- Nom de fichier enrichi selon les filtres actifs (ex : FabTrack_Export_Ref-Martin_2026-05-16.csv).
- Preset 'Année scolaire' : période du 1er septembre à aujourd'hui en un clic.

---

### v2.3.4 — 2026-05-08

#### Missions — Catégories avec définitions, exports enrichis et statistiques

**Nouvelles fonctionnalités :**

- **Définitions de catégories** : chaque catégorie de mission peut désormais recevoir un texte de définition décrivant les types de missions qui la composent. Ce champ s'affiche :
  - dans la modale de gestion des catégories (création et modification)
  - dans la liste des catégories (sous le nom)
  - en infobulle (`title`) sur les badges de catégorie dans les cartes kanban
  - dans tous les formats d'export

- **Exports enrichis** (CSV, HTML, PDF) :
  - **CSV** : nouvelle colonne *Définition catégorie* ; bloc légende `# LEGENDES CATEGORIES` en tête de fichier avec nom + définition de chaque catégorie ; encodage `UTF-8 BOM` pour compatibilité Excel
  - **HTML** : légende colorée (pastille + nom + définition) affichée avant le tableau ; description des missions inline sous le titre ; couleurs de priorité distinctives
  - **PDF** : section `--- Categories ---` avec les définitions avant la liste des missions

- **Panneau statistiques** (bouton *Stats* dans la barre d'actions) :
  - 6 indicateurs : total, à faire, en cours, terminées, en retard, durée moyenne de completion (sur les 30 dernières missions terminées)
  - Répartition par catégorie avec barres de progression, compteurs par statut et définition affichée
  - Répartition par priorité (Normale / Haute / Urgente)

**Endpoint API ajouté :**

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/missions/api/stats` | Statistiques globales : `total`, `by_statut`, `by_prio`, `by_category` (avec description), `overdue`, `avg_completion_days` |

**Migration de base de données :**
- Ajout de la colonne `description TEXT DEFAULT ''` sur la table `mission_categories` (migration automatique idempotente au démarrage)

<!-- manuel -->

---

### v2.3.3 — 2026-05-01

#### Cohérence occurrences par type et matériau

- Le flag matériau count_occurrences ne s'applique plus hors des familles ciblées (g, m2, feuilles).
- Migration durcie : les valeurs NULL historiques de count_occurrences sont normalisées à 1.
- Edition matériau fiabilisée : si le champ est absent, la valeur par défaut reste 1 (plus de retour à NULL).
- Interface paramètres simplifiée : suppression du mode auto par nom, affichage explicite oui/non.
- Validation complète : 25 tests unitaires passés sur la suite Fabtrack.

---

### v2.3.1 — 2026-05-01

#### Occurrences 3D fiabilisées

- Correction du pilotage des occurrences pour les entrées PLA/ABS 3D.
- Mise à jour du popup de release pour aligner les notes de cette itération.

---

### v2.3.0 — 2026-04-30

#### Multi-referents et édition d'historique

- Support des multi-référents dans les flux de suivi.
- Edition de l'historique améliorée pour corriger les entrées plus facilement.
- Sécurisation du flux de saisie pour réduire les erreurs opérateur.

---

### v2.2.0 — 2026-04-10

#### Assistant de setup et bibliothèque d'images

- Ajout d'un assistant de configuration initiale.
- Ajout d'une bibliothèque d'images pour simplifier l'illustration des fiches.
- Améliorations UX successives sur le sélecteur d'images.

---

### v2.1.0 — 2026-04-09

#### Popup de nouveautés centralisée

- Popup Nouveautés chargée depuis release-notes.json.
- Lien footer pour consulter l'historique à tout moment.

---

<!-- changelog:end -->

## 📝 Licence

MIT — © 2025-2026
