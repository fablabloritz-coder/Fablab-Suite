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
- **Rapports parc** : versions par logiciel (avec repérage des versions anciennes), licences déclarées et logiciels importants, calculés sur le dernier scan de chaque master inventorié.
- **Exports CSV des rapports** : licences (avec l'information manquante) et logiciels importants (master, version, catégorie, note, licence).
- **Historique** : chaque scan est conservé, avec provenance du script et avertissements de collecte
- **Évolution d'un master** : comparez deux scans du même poste (logiciels, système, disques, réseau, services)
- **Santé du parc** : repérez les scans anciens, disques bas, collectes partielles et doublons réseau
- **Registre réseau** : retrouvez la dernière IP/MAC connue de chaque interface
- **Comparaison** : comparez deux masters côte à côte (commun / unique A / unique B)
- **Renommage master** : le bouton Modifier permet de changer le nom du master, son label et ses notes
- **Mise à jour avec confirmation** : upload d'un nouveau fichier pour un master avec comparatif avant/après puis validation explicite
- **Suivi des absences** : chaque logiciel absent est « à vérifier » par défaut ; choisissez « Retiré volontairement » ou « À installer ». La liste ponctuelle est propre au master et se ferme automatiquement quand le logiciel réapparaît.
- **API FabSuite** : endpoints `/api/fabsuite/manifest`, `/api/fabsuite/health`, `/api/fabsuite/status`

## Installation rapide (Docker)

```bash
git clone <repo-url> FabInventory
cd FabInventory
docker compose up -d --build
```

Accès : `http://IP-DU-SERVEUR:5590`

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
python3 tests/run_all.py        # 27 modules, 159 tests, un processus par module
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
| `ADMIN_PASSWORD` | généré | Mot de passe du compte `admin` créé au premier démarrage. S'il est absent, un mot de passe aléatoire est écrit une seule fois dans `admin-initial-password.txt` (0600) à côté de la base. |

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
