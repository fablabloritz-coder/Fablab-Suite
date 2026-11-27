# FabInventory

Gestionnaire d'inventaire de masters FOG — Application web pour centraliser, consulter et comparer les inventaires logiciels de vos PC masters.

Compatible [Fablab-Suite](https://github.com/fablabloritz-coder/Fablab-Suite) (Flask + SQLite + Bootstrap 5 + Docker).

## Fonctionnalités

- **Script de collecte téléchargeable** : depuis l'accueil, téléchargez un script PowerShell prêt à l'emploi (Windows PowerShell 5.1 et PowerShell 7+)
- **Export dual automatique** : le script génère un **.html** (import FabInventory) et un **.csv** (exploitation bureautique)
- **JSON enrichi schema 3** : système, disques, réseau, services automatiques et logiciels sont sérialisés par `ConvertTo-Json`, avec repli HTML pour les anciens rapports
- **Fenêtre de sauvegarde** : l'utilisateur choisit le dossier + le nom du fichier via une fenêtre Windows ; `-OutputPath`, `-OutputDirectory` et `-NoGui` permettent l'automatisation
- **Import HTML** : uploadez les fichiers générés par le script d'inventaire
- **Import sécurisé par identité** : avant toute écriture, la série, le fabricant et le domaine du fichier sont comparés au dernier scan du master. Une série connue et différente est refusée par défaut (message clair et renvoi vers la mise à jour ciblée) ; l'aperçu permet de confirmer explicitement un « poste réimaginé ».
- **Aperçu et rapport de fusion** : `/upload` peut analyser les fichiers sans rien écrire (masters créés, mis à jour, doublons, conflits, fichiers illisibles), puis affiche après import le rapport de fusion avec le détail de chaque fichier.
- **Dashboard** : vue d'ensemble de tous les masters avec statistiques
- **Détail par master** : liste complète des logiciels avec tri, recherche, filtrage
- **Cases à cocher** : marquez les logiciels importants (sauvegardé en base)
- **Notes** : ajoutez des notes par logiciel (licence, salle, remarques)
- **Couverture logicielle fiable** : les noms sont normalisés (casse, accents, préfixes d'éditeur, suffixes d'édition/version) et complétés par des alias gérés dans le catalogue. « Java » ne compte plus « JavaScript », « Office » ne compte plus « OpenOffice », « Visual Studio » ne compte plus « Visual Studio Code ».
- **Migrations de schéma versionnées** : chaque migration idempotente est listée dans l'ordre et enregistrée dans `schema_version`. Une base vide comme une base antérieure (v2.1.4) est reprise au démarrage suivant, sans perte.
- **Rapports parc** : versions par logiciel (avec repérage des versions anciennes), licences déclarées et logiciels importants, calculés sur le dernier scan de chaque master inventorié. Les variantes d'un même nom sont regroupées par clé normalisée, une version absente reste « sans version » et toutes les versions installées sur un poste sont conservées.
- **Exports CSV des rapports** : licences (avec l'information manquante) et logiciels importants (master, version, catégorie, note, licence, une ligne par version installée).
- **Journal d'audit** : chaque écriture (import, nouveau scan, suppression et restauration, flag logiciel, checklist, décision d'absence, paramètres) est journalisée avec son auteur, sa cible, son IP et un résumé. Page admin `/admin/journal` filtrable par utilisateur, action et période, export CSV et rétention configurable (365 jours par défaut, 0 = illimité).
- **Rôles admin et technicien** : `admin_users.role` distingue l'administrateur (tout) du technicien, limité à l'inventaire (imports, masters, scans, roadmap, pack minimal, flags, absences, rapports, corbeille et restauration). Pour un technicien, le reste de l'espace `/admin` — catalogue, paramètres, professeurs, demandes, journal — et la purge définitive de la corbeille répondent 403 ; chaque écriture reste attribuée à son auteur dans le journal d'audit.
- **Gestion des comptes** : page `/admin/comptes` réservée aux administrateurs — création d'un technicien, réinitialisation de son mot de passe, désactivation et réactivation. Les actions sont journalisées, sans jamais recopier de mot de passe.
- **Historique** : chaque scan est conservé, avec provenance du script et avertissements de collecte
- **Évolution d'un master** : comparez deux scans du même poste (logiciels, système, disques, réseau, services)
- **Santé du parc** : repérez les scans anciens, disques bas, collectes partielles et doublons réseau
- **Registre réseau** : retrouvez la dernière IP/MAC connue de chaque interface
- **Comparaison** : comparez deux masters côte à côte (commun / unique A / unique B)
- **Renommage master** : le bouton Modifier permet de changer le nom du master, son label et ses notes
- **Mise à jour avec confirmation** : upload d'un nouveau fichier pour un master avec comparatif avant/après puis validation explicite
- **Suivi des absences** : chaque logiciel absent est « à vérifier » par défaut ; choisissez « Retiré volontairement » ou « À installer ». La liste ponctuelle est propre au master et se ferme automatiquement quand le logiciel réapparaît.
- **API FabSuite** : endpoints `/api/fabsuite/manifest`, `/api/fabsuite/health`, `/api/fabsuite/status`

## Comptes et rôles

`admin_users.role` (migration 7) distingue deux rôles :

| Rôle | Accès |
|---|---|
| `admin` | Tout : inventaire, catalogue, paramètres, professeurs, demandes, journal d'audit. |
| `technicien` | Inventaire seulement : imports, masters, scans, roadmap, pack minimal, flags, absences, rapports, santé, corbeille et restauration. Le reste de `/admin` (catalogue, paramètres, professeurs, demandes, journal) et la purge définitive répondent 403. |

Un compte créé avant la migration garde `admin` ; un rôle vide ou inconnu retombe
sur `technicien` (moindre privilège), et une session ouverte avant la migration
reste administrateur pour ne pas verrouiller l'installation. La page de
connexion est commune aux deux rôles : le technicien arrive sur le dashboard
inventaire, l'administrateur sur `/admin/`.

Les comptes techniciens se gèrent dans **Admin → Comptes** (`/admin/comptes`) :

- **créer un technicien** — le rôle est imposé, aucune élévation de privilège
  n'est possible depuis cette page ; mot de passe de 6 caractères minimum ;
- **réinitialiser son mot de passe** ;
- **désactiver / réactiver** un compte : désactivé, il est refusé à la connexion
  comme un mot de passe erroné, et son historique d'audit est conservé.

Les comptes administrateurs ne sont pas modifiables depuis cette page : leur mot
de passe passe par **Paramètres**, ce qui évite qu'un administrateur se retire
lui-même l'accès. La désactivation bloque les connexions suivantes ; une session
déjà ouverte reste valide jusqu'à sa déconnexion.

## Première connexion administrateur

Au tout premier démarrage (base vide, aucune clé `initial_seed` en base),
l'application crée un unique compte, comme PretGo :

- **identifiant : `admin`**
- **mot de passe : `1234`**

La connexion avec `admin` / `1234` n'ouvre pas le dashboard : elle mène à
**Paramètres**, qui demande de choisir un nouveau mot de passe (6 caractères
minimum). Tant que ce changement n'est pas fait, les autres pages de
l'administration redirigent vers Paramètres. Le nouveau mot de passe remplace
`1234` immédiatement et la session continue avec lui.

`ADMIN_PASSWORD` permet de fournir directement un mot de passe initial
(installation scriptée, machine exposée) : définie et non vide au premier
démarrage, elle remplace `1234` et aucun changement n'est imposé. Le seed n'a
lieu qu'une fois — modifier la variable après coup ne change plus rien ; le mot
de passe se change ensuite dans **Admin → Paramètres**. Aucun fichier de mot de
passe n'est généré.

**Base existante** : à la première mise à jour vers cette version, une base qui
n'a pas encore le drapeau `admin_password_is_default` est basculée **une seule
fois** sur `admin` / `1234` (ou sur `ADMIN_PASSWORD` si elle est définie) et le
changement est imposé à la connexion suivante. Une base déjà passée par ce
mécanisme, y compris une base neuve, n'est plus touchée.

> **Sécurité** : tant que `1234` n'a pas été changé, toute personne joignant
> l'application peut ouvrir Paramètres. Sur une machine exposée, définissez
> `ADMIN_PASSWORD` dans le `.env` avant le premier lancement.

### En Docker

Les trois composes de la suite transmettent `ADMIN_PASSWORD` depuis le `.env`.
Laisser la variable vide donne le compte `admin` / `1234` avec changement
imposé ; la renseigner évite le mot de passe par défaut :

```bash
# .env — optionnel
ADMIN_PASSWORD=UnMotDePasseProvisoire
```

### Réinitialisation si le mot de passe est perdu

Il n'existe pas d'écran « mot de passe oublié » : la page Paramètres exige
l'ancien mot de passe et la page Comptes exclut volontairement les
administrateurs. Le compte se réinitialise donc directement en base, avec le
même hachage que l'application (Werkzeug). **Arrêtez l'application** et
sauvegardez la base avant l'opération.

Docker, depuis le dossier du `docker-compose.yml` utilisé — on remet le compte
`admin` sur `1234` et on réarme le changement obligatoire :

```bash
docker compose stop fabinventory
docker compose run --rm --no-deps fabinventory cp /data/fabinventory.db /data/fabinventory.db.bak
docker compose run --rm --no-deps fabinventory python -c 'import sqlite3; from werkzeug.security import generate_password_hash; db = sqlite3.connect("/data/fabinventory.db"); db.execute("UPDATE admin_users SET password_hash = ?, is_active = 1 WHERE username = ?", (generate_password_hash("1234"), "admin")); db.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", ("admin_password_is_default", "1")); db.commit(); print(db.total_changes, "ligne(s) mise(s) a jour")'
docker compose start fabinventory
```

Installation manuelle (hors Docker) — la même commande, avec le chemin réel de
la base (`DB_PATH`, `/data/fabinventory.db` par défaut) :

```bash
python -c 'import sqlite3; from werkzeug.security import generate_password_hash; db = sqlite3.connect("/data/fabinventory.db"); db.execute("UPDATE admin_users SET password_hash = ?, is_active = 1 WHERE username = ?", (generate_password_hash("1234"), "admin")); db.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", ("admin_password_is_default", "1")); db.commit(); print(db.total_changes, "ligne(s) mise(s) a jour")'
```

Après redémarrage, `admin` / `1234` rouvre Paramètres et impose un nouveau mot de
passe. Pour fixer directement un autre mot de passe, remplacez `1234` par la
valeur voulue et écrivez `0` dans `admin_password_is_default` : aucun changement
ne sera alors demandé. Si aucune ligne n'est mise à jour, la ligne `admin` a été
supprimée : réinsérez-la avec un `password_hash` généré de la même façon, ou
restaurez `fabinventory.db.bak` en arrêtant l'application et en remplaçant
`/data/fabinventory.db`.

## Installation rapide (Docker)

```bash
git clone <repo-url> FabInventory
cd FabInventory
docker compose up -d --build
```

Accès : `http://IP-DU-SERVEUR:5590`. Le compte `admin` et son mot de passe
initial sont décrits dans [Première connexion administrateur](#première-connexion-administrateur).

## Installation manuelle (dev)

```bash
pip install -r requirements.txt
mkdir -p /data
python app.py
```

## Utilisation

1. Ouvrez FabInventory dans un navigateur
2. Depuis l'accueil, cliquez sur **Télécharger le script master**
3. Exécutez le script PowerShell sur le poste master
4. Choisissez l'emplacement et le nom via la fenêtre d'enregistrement
5. Récupérez les deux fichiers générés : `.html` et `.csv`
6. Dans FabInventory, cliquez sur **Importer** puis sélectionnez le `.html`
7. Pour une mise à jour ciblée d'un master existant, ouvrez sa fiche puis cliquez sur **Mettre à jour**
8. Vérifiez le comparatif, décidez du sort des logiciels absents (à vérifier, retiré volontairement ou à installer), puis confirmez
9. Retrouvez la file ponctuelle d'un master via **Absences** ; elle se met à jour automatiquement quand un logiciel réapparaît

## Script d'inventaire (Lot 6)

Le script servi par `/download/master-script` est la source unique :
`static/downloads/inventaire_master.ps1`. Il collecte le registre machine et
utilisateur, les applications AppX, les dossiers Program Files, les disques,
le réseau, les services automatiques et les informations système.

La version courante (`3.1.0`) et l'empreinte SHA256 du script sont affichées
sur le dashboard. Un scan produit par une version antérieure (ou sans version)
porte le badge « scan avec un script ancien » sur la fiche master et sur
`/sante`.

```powershell
# Mode interactif : fenêtre de sauvegarde
.\inventaire_master.ps1

# Mode silencieux, pour un déploiement ou une tâche planifiée
.\inventaire_master.ps1 -NoGui -OutputDirectory C:\Inventaires

# Vérification du script sans interroger Windows
.\inventaire_master.ps1 -SelfTest -OutputPath C:\Temp\selftest.html
```

Le rapport HTML est autonome (aucun CDN) et contient un bloc
`<script id="inventoryData" type="application/json">` de la forme :

```json
{"schema":3,"pcName":"PC-01","date":"26/11/2026 21:00","system":{},"disks":[],"network":[],"services":[],"software":[{"n":"...","v":"...","e":"...","d":"...","s":0,"src":"..."}]}
```

Les erreurs de collecte (WMI indisponible, AppX refusé, dossier inaccessible)
sont consignées dans `warnings` et visibles dans le rapport ; elles
n'interrompent pas le reste de l'inventaire. Un journal est écrit dans le
dossier temporaire en cas d'échec fatal.

## Architecture

```
FabInventory/
├── app.py                  # Application Flask
├── templates/              # Templates HTML (Jinja2)
│   ├── base.html           # Layout commun
│   ├── 403.html            # Accès refusé (rôle technicien)
│   ├── index.html          # Dashboard
│   ├── upload.html         # Page d'import
│   ├── upload_preview.html # Aperçu du plan d'import avant écriture
│   ├── master.html         # Détail d'un master
│   ├── master_update.html  # Mise à jour master avec comparaison + décisions
│   ├── master_followups.html # File ponctuelle des absences d'un master
│   ├── snapshot.html       # Détail d'un scan
│   ├── master_evolution.html # Évolution entre deux scans d'un master
│   ├── health.html         # Santé du parc
│   ├── parc_reports.html   # Rapports parc (versions, licences, importants)
│   ├── admin/audit.html    # Journal d'audit (filtres, export, rétention)
│   ├── admin/accounts.html # Comptes techniciens (création, mot de passe, désactivation)
│   ├── network_registry.html # Registre IP/MAC
│   └── compare.html        # Comparaison
├── static/css/style.css    # Styles
├── static/downloads/
│   ├── inventaire_master.ps1     # Script telechargeable (HTML + CSV + JSON)
│   └── lancer_inventaire_master.bat
├── tests/                       # Suite de non-regression (run_all.py)
├── tools/parc_scale.py          # Parc synthetique (500 PC) et mesure des pages
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Tests et mesures

```bash
python3 tests/run_all.py        # 30 modules, 194 tests, un processus par module
python3 tools/parc_scale.py --db /tmp/parc-500.db --force --benchmark
```

La seconde commande génère un parc synthétique (500 masters, 10 000 snapshots,
400 000 lignes d'index logiciel) puis mesure `/`, `/sante`, `/search`,
`/api/software` et `/admin/masters` : un budget SQL fixe par page et un seuil de
temps de réponse. Un budget constant prouve qu'aucune requête n'est faite par
master ou par snapshot (N+1). Le jeu n'est jamais committé : il est généré à la
demande.

La CI `.github/workflows/fabinventory-tests.yml` rejoue les suites et cette
mesure sur chaque PR touchant l'application, avant fusion.

## Stack technique

- Backend : Flask + SQLite
- Frontend : Bootstrap 5 + JavaScript vanilla
- Déploiement : Docker / Docker Compose
- Compatible : Fablab-Suite (`/api/fabsuite/manifest`, `/api/fabsuite/health`, `/api/fabsuite/status`)

## Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PORT` | `5590` | Port de l'application |
| `DB_PATH` | `/data/fabinventory.db` | Chemin de la base SQLite |
| `UPLOAD_FOLDER` | `/data/uploads` | Dossier d'upload |
| `SECRET_KEY` | générée | Clé de session Flask. Une valeur absente, de moins de 32 caractères, trop peu variée ou connue (`change-me`, `secret`, `tests`…) est ignorée : une clé aléatoire est générée et conservée dans la base. Recommandé : `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `ADMIN_PASSWORD` | `1234` | Mot de passe initial du compte `admin`, lu au premier démarrage et lors de la bascule d'une base existante, et transmis par les `docker-compose.yml` depuis le `.env`. Absent ou vide, le compte est `admin` / `1234` et le changement de mot de passe est imposé à la connexion ; renseigné, il est utilisé tel quel sans changement imposé. |

---

## 📜 Changelog

<!-- changelog:start -->

### v2.1.4 — 2026-11-15

#### Manifeste conforme au contrat FabSuite

- Le manifeste FabSuite de FabInventory expose desormais le champ status attendu par le contrat commun aux cinq applications

---

### v2.1.3 — 2026-11-15

#### Cache, compression et serveur waitress

- Le navigateur garde desormais les fichiers CSS, JavaScript et images une heure (Cache-Control public, max-age=3600), ce qui supprime une revalidation a chaque page ; les pages HTML et les reponses JSON sont compressees en gzip quand le navigateur l'accepte (Vary: Accept-Encoding). FabInventory passe de gunicorn a waitress, le serveur de production deja utilise par les autres applications.

---

### v2.1.2 — 2026-11-15

#### Attente de verrou SQLite

- Attente de verrou : chaque application attend jusqu'a 5 secondes qu'une ecriture concurrente libere la base SQLite, au lieu d'echouer immediatement sur un "database is locked".

---

### v2.1.1 — 2026-11-15

#### Export CSV de la recherche corrige

- L'export CSV de la recherche globale fonctionne de nouveau : la requete ne selectionnait pas la categorie et l'export echouait en erreur.
- La colonne software_category est bien presente dans le fichier exporte.
- Les scripts de test historiques sont regroupes dans tests/ et executes par la CI.

---

### v2.1.0 — 2026-05-19

#### Admin unifié et couverture du catalogue

- Dashboard admin enrichi : 8 indicateurs (4 côté demandes, 4 côté inventaire) et alerte sur les scans de plus de 30 jours.
- Nouvelle page /admin/masters avec barre de progression de la couverture du catalogue.
- Badge « X/Y masters » sur chaque logiciel du catalogue pour voir où il est déjà installé.
- Champ « Déjà présent » dans la fenêtre de demande, avec la couverture inventaire.
- Portail professeur : badge « Déjà sur X poste(s) » et bouton désactivé si une demande est déjà en cours.
- Barre latérale admin : section Inventaire avec le lien Masters, et liens Outils restaurés.

---

### v2.0.0 — 2026-05-19

#### Portail demandes logiciels - refonte majeure

- Portail professeurs : accès via token UUID unique (sans mot de passe), catalogue logiciels, soumission de demandes.
- Espace admin sécurisé : login/password, dashboard agrégé, gestion des demandes (statuts : Demande -> Validé -> En cours -> Installé -> Refusé).
- Catalogue logiciels : CRUD complet (nom, catégorie, description, pack de base, actif/inactif).
- Versions par logiciel : numéro, type de licence, gratuit pour l'éducation, URL de téléchargement.
- Plugins/options par logiciel : sélectionnables par le professeur lors de la demande.
- Guides / Wiki : éditeur Markdown par logiciel (guide principal, comptes/licences, dépannage JSON).
- Gestion des professeurs : création, révocation, régénération de token, lien copiable.
- Export PDF des demandes pour préparer les installations masters.
- Anti-doublon : pas de deuxième demande en cours pour le même logiciel.
- Données de départ : compte admin par défaut et pack de base (VLC, 7-Zip, SumatraPDF).
- Migrations SQLite idempotentes : 8 nouvelles tables sans rupture de la base existante.
- Navigation : lien Admin ajouté dans la barre de navigation de l'inventaire.

---

<!-- changelog:end -->
