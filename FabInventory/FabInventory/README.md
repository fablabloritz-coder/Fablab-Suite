# FabInventory

Gestionnaire d'inventaire de masters FOG — Application web pour centraliser, consulter et comparer les inventaires logiciels de vos PC masters.

Compatible [Fablab-Suite](https://github.com/fablabloritz-coder/Fablab-Suite) (Flask + SQLite + Bootstrap 5 + Docker).

## Fonctionnalités

- **Script de collecte téléchargeable** : depuis l'accueil, téléchargez un script PowerShell prêt à l'emploi
- **Export dual automatique** : le script génère un **.html** (import FabInventory) et un **.csv** (exploitation bureautique)
- **Fenêtre de sauvegarde** : l'utilisateur choisit le dossier + le nom du fichier via une fenêtre Windows
- **Import HTML** : uploadez les fichiers générés par le script d'inventaire
- **Dashboard** : vue d'ensemble de tous les masters avec statistiques
- **Détail par master** : liste complète des logiciels avec tri, recherche, filtrage
- **Cases à cocher** : marquez les logiciels importants (sauvegardé en base)
- **Notes** : ajoutez des notes par logiciel (licence, salle, remarques)
- **Historique** : chaque scan est conservé, consultez l'évolution dans le temps
- **Comparaison** : comparez deux masters côte à côte (commun / unique A / unique B)
- **Renommage master** : le bouton Modifier permet de changer le nom du master, son label et ses notes
- **Mise à jour avec confirmation** : upload d'un nouveau fichier pour un master avec comparatif avant/après puis validation explicite
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
8. Vérifiez le comparatif (ajouts/suppressions/changements) puis confirmez

## Architecture

```
FabInventory/
├── app.py                  # Application Flask
├── templates/              # Templates HTML (Jinja2)
│   ├── base.html           # Layout commun
│   ├── index.html          # Dashboard
│   ├── upload.html         # Page d'import
│   ├── master.html         # Détail d'un master
│   ├── master_update.html  # Mise à jour master avec comparaison + confirmation
│   ├── snapshot.html       # Détail d'un scan
│   └── compare.html        # Comparaison
├── static/css/style.css    # Styles
├── static/downloads/
│   └── inventaire_master.ps1  # Script telechargeable (HTML + CSV)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

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
| `SECRET_KEY` | `fabinventory-secret` | Clé secrète Flask |

---

## 📜 Changelog

<!-- changelog:start -->

### v2.1.0 — 2026-05-19

#### Admin unifie et couverture du catalogue

- Dashboard admin enrichi : 8 indicateurs (4 cotes demandes, 4 cotes inventaire) et alerte sur les scans de plus de 30 jours.
- Nouvelle page /admin/masters avec barre de progression de la couverture du catalogue.
- Badge « X/Y masters » sur chaque logiciel du catalogue pour voir ou il est deja installe.
- Champ « Deja present » dans la fenetre de demande, avec la couverture inventaire.
- Portail professeur : badge « Deja sur X poste(s) » et bouton desactive si une demande est deja en cours.
- Barre laterale admin : section Inventaire avec le lien Masters, et liens Outils restaures.

---

### v2.0.0 — 2026-05-19

#### Portail demandes logiciels - refonte majeure

- Portail professeurs : acces via token UUID unique (sans mot de passe), catalogue logiciels, soumission de demandes.
- Espace admin securise : login/password, dashboard agrege, gestion des demandes (statuts : Demande -> Valide -> En cours -> Installe -> Refuse).
- Catalogue logiciels : CRUD complet (nom, categorie, description, pack de base, actif/inactif).
- Versions par logiciel : numero, type de licence, gratuit pour l'education, URL de telechargement.
- Plugins/options par logiciel : selectionnables par le professeur lors de la demande.
- Guides / Wiki : editeur Markdown par logiciel (guide principal, comptes/licences, depannage JSON).
- Gestion des professeurs : creation, revocation, regeneration de token, lien copiable.
- Export PDF des demandes pour preparer les installations masters.
- Anti-doublon : pas de deuxieme demande en cours pour le meme logiciel.
- Donnees de depart : compte admin par defaut et pack de base (VLC, 7-Zip, SumatraPDF).
- Migrations SQLite idempotentes : 8 nouvelles tables sans rupture de la base existante.
- Navigation : lien Admin ajoute dans la barre de navigation de l'inventaire.

---

<!-- changelog:end -->
