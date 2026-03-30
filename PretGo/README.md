# 📦 PretGo

Application web de gestion de prêt de matériel pour **établissements scolaires** — Python/Flask, SQLite, zéro installation requise. Interface moderne et tactile, base de données locale, aucun cloud requis.

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0-green?logo=flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local-lightgrey?logo=sqlite&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-purple?logo=bootstrap&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 🎯 À quoi ça sert ?

Vous gérez le matériel informatique d'un établissement scolaire (collège, lycée…) et vous avez besoin de :

- **Suivre qui a emprunté quoi** et depuis quand
- **Être alerté des retards** de restitution
- **Gérer un inventaire** de matériel avec numéros de série, étiquettes, images
- **Importer/exporter** facilement des listes de personnes et de matériel en CSV
- **Imprimer des étiquettes** avec code-barres pour le matériel
- **Consulter des statistiques** détaillées sur l’activité de prêt
- **Personnaliser l’interface** : thème couleur, mode sombre, champs dynamiques

Cette application tourne **en local** sur votre poste — aucun serveur externe, aucune donnée dans le cloud.

---

## ✨ Fonctionnalités

### Gestion des prêts
- Création rapide de prêt avec autocomplétion des personnes et du matériel
- Durée configurable : heures, jours, fin de journée (heure configurable), date précise (calendrier visuel Flatpickr), ou durée par défaut
- Retour en un clic avec mise à jour automatique de l'état du matériel
- Détail, modification et suppression de chaque prêt
- Historique complet des prêts restitués

### Gestion des personnes
- Fiche personne avec nom, prénom, catégorie (élève, enseignant, agent…) et classe
- Catégories personnalisables avec icônes, couleurs et badges visuels
- Import CSV en masse (avec gabarit dynamique reflétant les catégories configurées)
- Suppression logique (soft delete) — les personnes avec des prêts actifs sont protégées

### Inventaire de matériel
- Fiche matériel : type, marque, modèle, n° de série, n° d'inventaire, état, image
- Numérotation automatique par préfixe de catégorie (ex : `PC-00001`, `VID-00012`)
- Catégories de matériel personnalisables avec préfixes
- Import CSV en masse (avec gabarit dynamique reflétant les catégories configurées)
- Synchronisation automatique de l'état (disponible / prêté) avec les prêts

### Étiquettes & impression
- Génération d'étiquettes avec code-barres pour le matériel inventorié
- Mise en page optimisée pour impression sur planches d'étiquettes
- Support optionnel d'imprimante Zebra (ZPL via port série)

### Alertes & suivi
- Tableau de bord avec alertes de prêts en dépassement
- Badge de notification dans la barre de navigation
- Export CSV des alertes en cours
### 📧 Rappels email automatiques (NEW)
- **Scheduler automatique** : configurations des jours, heures et minutes pour les envois
- **Tentatives de rappel** : suivi du nombre de relances par prêt (max configurable, par défaut 3)
- **Anti-spam** : blocage automatique après limite de tentatives pour éviter la surcharge
- **Template configurable** : personnalisez le corps du rappel avec variables (`{nom}`, `{prenom}`, `{depassement}`, etc.)
- **Historique complet** : dashboard d'administration avec filtres (statut, date) et export CSV
- **FabHome Integration** : notifications centralisées dans le hub FabHome (alertes en temps réel)
- **Tracking robuste** : tentative N/M, statuts détaillés (Envoyé / Erreur / Bloqué), messages d'erreur SMTP stockés

👉 **Documentation complète** : voir [SYSTEM_EMAIL_GUIDE.md](SYSTEM_EMAIL_GUIDE.md)



### Administration
- **Première connexion guidée** : mot de passe par défaut `1234`, personnalisation obligatoire au premier lancement
- Code de récupération **unique par installation**, généré lors de la personnalisation du mot de passe
- Accès protégé par mot de passe (hachage sécurisé scrypt/pbkdf2)
- Réglages : durée par défaut, heure de fin de journée, nom de l'établissement, taille des étiquettes
- **Personnalisation du bip sonore** lors des scans webcam : choix du volume et du type de bip (sine, carré, triangle, dent de scie), test instantané, réglages persistants sur toutes les pages
- Réinitialisation de la base de données
- Génération de données de démonstration dynamiques (s'adapte aux catégories configurées)
- **Configuration email SMTP** : test connexion, activation/désactivation, template customisable
- **Gestion du scheduler email** : configure horaire, jours, limite de tentatives avec interface visuelle
### Ergonomie du scan
- Bouton de scan toujours visible dans la barre de navigation
- Icône contextuelle selon le mode (webcam ou douchette)
- Bip sonore configurable à chaque scan webcam (volume/type)
- Test du bip en direct dans les réglages admin

### Import / Export
- Export CSV : prêts (tous / en cours), personnes, inventaire, alertes
- Import CSV : personnes, inventaire (avec détection des doublons)
- Gabarits CSV dynamiques : les fichiers téléchargeables reflètent automatiquement les catégories configurées dans l'application

---

## 🚀 Déploiement

### 🐳 Docker (recommandé)

Docker est la méthode officielle — comportement identique en local et en production sur serveur Ubuntu. Aucune installation Python requise.

**Prérequis :** [Docker Desktop](https://docs.docker.com/get-docker/) (Windows/macOS) ou Docker Engine (Linux).

```bash
git clone https://github.com/fablabloritz-coder/PretGo.git
cd PretGo
cp .env.example .env        # ajustez PRETGO_PORT si besoin
docker compose up -d --build
```

L'application est accessible à **http://localhost:5000** (ou le port configuré dans `.env`).
Connectez-vous avec le mot de passe par défaut **`1234`** puis personnalisez-le.

---

### 🪟 Alternative Windows (sans Docker)

> Pour les environnements sans Docker. Aucune installation Python requise — Python portable téléchargé automatiquement.

1. **Double-cliquez sur `installer.bat`** — télécharge Python portable + Flask (~15 Mo)
2. **Double-cliquez sur `lancer.bat`** — lance le serveur et ouvre le navigateur

### 👨‍💻 Alternative manuelle (développeurs)

```bash
git clone https://github.com/fablabloritz-coder/PretGo.git
cd PretGo
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
python app.py
```

L'application est accessible sur **http://localhost:5000**.

---

### Procédures Docker détaillées

PretGo est prêt à être exécuté en conteneur avec persistance de la base SQLite et des images uploadées.

#### 1) Lancer en mode serveur (stable)

```bash
docker compose up -d --build
```

- Application: `http://localhost:5000`
- Données persistées sur le disque hôte (bind mounts):
    - `./docker-data/data` (SQLite, sauvegardes, clé secrète, code de récupération, documents)
    - `./docker-data/uploads/materiel` (images matériel)

Arrêt:

```bash
docker compose down
```

#### 2) Lancer en mode développement (code monté en direct)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Ce mode monte le code local dans le conteneur et lance `python app.py`.

#### 3) Variable d'environnement conseillée (serveur)

Définissez une clé secrète forte avant de lancer en production :

```bash
cp .env.example .env
```

Vous pouvez adapter les chemins hôte via `.env` :

```bash
PRETGO_DATA_PATH=./docker-data/data
PRETGO_UPLOADS_PATH=./docker-data/uploads/materiel
```

```bash
# Linux/macOS
export FLASK_SECRET_KEY="votre_cle_longue_et_aleatoire"

# PowerShell
$env:FLASK_SECRET_KEY="votre_cle_longue_et_aleatoire"
```

#### 4) Procedures d'exploitation Docker (Ubuntu serveur)

Commandes a executer depuis le dossier `PretGo/`.

Mise a jour applicative (code + image + conteneur):

```bash
cd /chemin/vers/PretGo
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

Arret du service:

```bash
cd /chemin/vers/PretGo
docker compose stop
```

Relance du service (sans rebuild):

```bash
cd /chemin/vers/PretGo
docker compose start
```

Redemarrage complet (sans rebuild):

```bash
cd /chemin/vers/PretGo
docker compose restart
```

Relance avec rebuild force:

```bash
cd /chemin/vers/PretGo
docker compose down
docker compose up -d --build
```

Diagnostic rapide:

```bash
cd /chemin/vers/PretGo
docker compose ps
docker logs --tail=120 pretgo
```

En cas de conflit `container name ... already in use`:

```bash
docker stop pretgo 2>/dev/null || true
docker rm pretgo
cd /chemin/vers/PretGo
docker compose up -d --build
```

Depannage: application inaccessible depuis le navigateur

1. Verifier que le conteneur tourne:

```bash
cd /chemin/vers/PretGo
docker compose ps
docker ps -a --filter "name=^/pretgo$"
```

2. Lire les logs de demarrage:

```bash
docker logs --tail=200 pretgo
```

3. Tester localement sur le serveur:

```bash
curl -I http://127.0.0.1:5000
```

4. Si local OK mais acces distant KO, ouvrir le firewall:

```bash
sudo ufw allow 5000/tcp
sudo ufw status
```

5. Si l'etat est incoherent, remise a plat sans perte de donnees:

```bash
docker stop pretgo 2>/dev/null || true
docker rm pretgo 2>/dev/null || true
cd /chemin/vers/PretGo
docker compose up -d --build
```

#### 5) Sauvegarde / restauration

- La persistance est assurée par des dossiers hôte (donc indépendants du conteneur).
- Données critiques à conserver :
    - `docker-data/data/gestion_prets.db`
    - `docker-data/data/secret_key.txt`
    - `docker-data/data/code_recuperation.txt`
    - `docker-data/data/documents/`
    - `docker-data/data/sauvegardes/` (sauvegardes auto/manuelles côté app)
    - `docker-data/uploads/materiel/`
- En plus, utilisez les fonctions de sauvegarde intégrées de PretGo pour exporter des archives `.pretgo` hors de la machine.

#### 6) Déploiement multi-applications (Fablab Suite)

PretGo fait partie de la FabLab Suite :

| Application | Description | Port |
|---|---|---|
| **[FabHome](https://github.com/fablabloritz-coder/FabHome)** | Hub central — portail et dashboard | 3001 |
| **PretGo** | Gestion de prêts de matériel | 5000 |
| **[Fabtrack](https://github.com/fablabloritz-coder/Fabtrack)** | Suivi des consommations machines | 5555 |
| **[FabBoard](https://github.com/fablabloritz-coder/FabBoard)** | Dashboard TV temps réel | 5580 |

Pour déployer toute la suite en une commande, un `docker-compose.yml` unifié est disponible à la racine du dépôt parent [fabsuite-spec](https://github.com/fablabloritz-coder/fabsuite-spec).

---

## 📁 Structure du projet

```
├── app.py                  # Point d'entrée Flask (~185 lignes)
├── database.py             # Gestion SQLite (init, migrations, helpers) (~380 lignes)
├── utils.py                # Utilitaires, filtres Jinja2, backup (~740 lignes)
├── routes/                 # 7 Blueprints Flask
│   ├── admin.py            # Administration, réglages, backup (~1200 lignes)
│   ├── prets.py            # Gestion des prêts (~400 lignes)
│   ├── personnes.py        # Gestion des personnes (~580 lignes)
│   ├── inventaire.py       # Inventaire, imports CSV (~600 lignes)
│   ├── core.py             # Pages principales (~190 lignes)
│   ├── api.py              # API JSON (scan, autocomplete) (~280 lignes)
│   └── export.py           # Exports CSV (~200 lignes)
├── test_routes.py          # Tests automatisés (81 tests, ~500 lignes)
├── requirements.txt        # Dépendances Python (Flask, Waitress)
├── installer.bat           # Télécharge Python portable + installe Flask
├── lancer.bat              # Lance l'app avec le Python embarqué
├── python/                 # Python portable (créé par installer.bat)
├── data/
│   └── code_recuperation.txt   # Code de récupération admin (généré à la personnalisation)
├── static/
│   ├── css/
│   │   └── style.css       # Styles personnalisés
│   ├── js/
│   │   └── app.js          # JavaScript front-end
│   ├── uploads/             # Images de matériel uploadées
│   └── exemple_personnes.csv
└── templates/               # Templates Jinja2 (35 fichiers)
    ├── base.html            # Layout principal (navbar, Bootstrap 5, mode sombre)
    ├── index.html           # Tableau de bord / prêts en cours (paginé)
    ├── nouveau_pret.html    # Formulaire de nouveau prêt
    ├── retour.html          # Interface de retour
    ├── inventaire.html      # Liste du matériel
    ├── personnes.html       # Liste des personnes
    └── ...
```

---

## ⚙️ Configuration

Au premier lancement, l'application crée automatiquement :
- La base de données `data/gestion_prets.db`
- Les tables et migrations nécessaires

### Accès administrateur

1. **Première connexion** : connectez-vous avec le mot de passe par défaut **`1234`**
2. L'application vous demande immédiatement de **personnaliser votre mot de passe**
3. Un **code de récupération unique** est alors généré et sauvegardé dans `data/code_recuperation.txt`
4. **Conservez ce code** — il est le seul moyen de réinitialiser votre mot de passe en cas d'oubli

> ⚠️ Le code de récupération est régénéré à chaque changement de mot de passe. Pensez à noter le nouveau code.


### Réglages disponibles

| Paramètre | Description | Valeur par défaut |
|-----------|-------------|-------------------|
| Durée d'alerte | Durée avant qu'un prêt soit considéré en retard | 7 jours |
| Unité de durée | Jours ou heures | Jours |
| Heure de fin de journée | Limite pour le mode "fin de journée" | 17:45 |
| Nom de l'établissement | Affiché sur les étiquettes | — |
| Taille code-barres | Hauteur des codes-barres sur les étiquettes | 50 |
| Volume du bip | Volume du bip sonore lors du scan webcam | 15% |
| Type de bip | Forme d'onde du bip (sine, carré, triangle, dent de scie) | Sine |
| Couleur primaire | Couleur du thème de l'interface | #4361ee |
| Mode sombre | Activer/désactiver le thème sombre | Désactivé |

---

## 🖥️ Technologies

| Composant | Technologie |
|-----------|------------|
| Backend | **Python 3** / **Flask 3.0** / **Waitress** (WSGI production) |
| Base de données | **SQLite** (locale, WAL mode, sans serveur) |
| Frontend | **Bootstrap 5.3** / **Bootstrap Icons** |
| Codes-barres | **JsBarcode** (client-side) |
| Graphiques | **Chart.js** (statistiques interactives) |
| Calendrier | **Flatpickr** (sélection de date visuelle, thème sombre, locale FR) |
| Sécurité | **scrypt/pbkdf2** (hachage des mots de passe) |

**Aucune dépendance lourde** — seul Flask est requis côté Python. L'interface charge Bootstrap et les icônes via CDN.

**Déploiement autonome** — `installer.bat` télécharge un Python portable (~10 Mo) dans le dossier du projet. Aucune installation système requise, aucun droit administrateur nécessaire. Idéal pour les environnements scolaires restreints.

---

## 🔒 Sécurité

- **Première connexion sécurisée** : mot de passe par défaut `1234` avec personnalisation obligatoire. Le code de récupération n'est généré qu'après cette étape, garantissant un code **unique par installation**.
- **Mots de passe** hachés avec scrypt/pbkdf2 (via werkzeug). Migration automatique depuis l'ancien format SHA-256.
- **En-têtes de sécurité** : Content-Security-Policy (CSP) stricte, X-Content-Type-Options, X-Frame-Options.
- **Protection CSRF** : tokens de sécurité sur tous les formulaires, exemptions limitées aux API JSON.
- **Regénération de session** après authentification pour prévenir la fixation de session.
- **Serveur WSGI Waitress** en production (au lieu du serveur de développement Flask).
- **Audit logging** : les actions sensibles (login, logout, reset password, reset DB, restauration, génération démo) sont tracées dans les logs.
- **Protection anti-bruteforce** : rate limiter sur les tentatives de connexion avec nettoyage mémoire automatique.
- **Clé secrète Flask** configurable via variable d'environnement `FLASK_SECRET_KEY` (une valeur par défaut est fournie pour simplifier le démarrage).
- **Protection contre l'open redirect** sur le paramètre `next` après authentification.
- **Autocomplétion sécurisée** : les données utilisateur sont insérées dans le DOM via `textContent` (pas `innerHTML`) pour prévenir les injections XSS.
- **Validation des couleurs du thème** par regex pour éviter les injections CSS.

---

## 📸 Aperçu

<img width="1912" height="1076" alt="image" src="https://github.com/user-attachments/assets/beee0068-c3bc-4680-9ca2-a835db76bb93" />


### Tableau de bord
Liste des prêts en cours avec indicateur de durée et alertes de dépassement.

### Nouveau prêt
Formulaire avec autocomplétion des personnes, sélection du matériel inventorié, choix de la durée, modal d'engagement.

### Inventaire
Tableau filtrable avec image, type, marque/modèle, n° de série, état et actions rapides.

### Étiquettes
Génération d'étiquettes avec code-barres, optimisées pour l'impression.

---

## 🤝 Contribution

Les contributions sont les bienvenues ! N'hésitez pas à :

1. **Fork** le projet
2. Créer une **branche** pour votre fonctionnalité (`git checkout -b feature/ma-fonctionnalite`)
3. **Commit** vos changements (`git commit -m "Ajout de ma fonctionnalité"`)
4. **Push** sur votre branche (`git push origin feature/ma-fonctionnalite`)
5. Ouvrir une **Pull Request**

### Améliorations récentes

- 🏗️ **Architecture Blueprints** : `app.py` refactorisé en 7 modules (core, prets, personnes, inventaire, admin, api, export)
- 🛡️ **Sécurité renforcée** : CSP header, session regeneration, audit logging, rate limiter, Waitress WSGI
- ⚡ **Performance** : cache de settings, requêtes GROUP BY au lieu de N+1, pagination du tableau de bord
- 📁 **Explorateur de dossiers** : sélection visuelle du répertoire de sauvegarde
- 📊 **Backup automatique** : sauvegardes planifiées avec rotation configurable
- 📅 **Date de retour précise** : nouveau type de durée avec calendrier visuel (Flatpickr, thème sombre, locale FR)
- ⏰ **Heure de retour configurable** : l'heure limite de retour utilise désormais l'heure de fin de journée configurée dans les réglages (au lieu de 23h59)
- 🎨 **Thème couleur personnalisable** et **mode sombre** complet
- 📊 **Tableau de bord statistiques** avec graphiques interactifs (Chart.js)
- 🔊 **Bip sonore configurable** pour le scan webcam (volume, type d'onde)
- 🏷️ **Catégories personnalisables** pour personnes et matériel (icônes, couleurs, badges, préfixes)
- 📥 **Gabarits CSV dynamiques** adaptés aux catégories configurées

### Idées d'améliorations futures

- 📅 Système de réservation / planification
- 📧 Notifications par email (rappels, retards)
- 👥 Mode multi-utilisateurs avec rôles (admin, gestionnaire, consultant)
- 🔧 Gestion du cycle de vie du matériel (maintenance, garantie, rebut)
- 🌐 Mode hors-ligne (PWA)

---

## 📄 Licence

Ce projet est distribué sous licence **MIT**. Voir le fichier [LICENSE](LICENSE) pour plus de détails.

---

## 🙏 Remerciements

- [Flask](https://flask.palletsprojects.com/) — micro-framework web Python
- [Bootstrap](https://getbootstrap.com/) — framework CSS responsive
- [Bootstrap Icons](https://icons.getbootstrap.com/) — icônes SVG
- [JsBarcode](https://github.com/lindell/JsBarcode) — génération de codes-barres côté client
- [Chart.js](https://www.chartjs.org/) — graphiques interactifs
