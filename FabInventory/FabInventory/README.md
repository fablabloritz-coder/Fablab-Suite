# FabInventory

Gestionnaire d'inventaire de masters FOG — Application web pour centraliser, consulter et comparer les inventaires logiciels de vos PC masters.

Compatible [Fablab-Suite](https://github.com/fablabloritz-coder/Fablab-Suite) (Flask + SQLite + Bootstrap 5 + Docker).

## Fonctionnalités

- **Import HTML** : uploadez les fichiers générés par le script d'inventaire USB (v3)
- **Dashboard** : vue d'ensemble de tous les masters avec statistiques
- **Détail par master** : liste complète des logiciels avec tri, recherche, filtrage
- **Cases à cocher** : marquez les logiciels importants (sauvegardé en base)
- **Notes** : ajoutez des notes par logiciel (licence, salle, remarques)
- **Historique** : chaque scan est conservé, consultez l'évolution dans le temps
- **Comparaison** : comparez deux masters côte à côte (commun / unique A / unique B)
- **Labels** : nommez vos masters (ex: "Salle Info 1 - SolidWorks")
- **API FabSuite** : endpoint `/api/fabsuite/status` pour l'intégration inter-apps

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

1. Lancez le script `LANCER_INVENTAIRE.bat` (v3) sur chaque PC master
2. Récupérez les fichiers HTML générés dans le dossier `INVENTAIRES/`
3. Ouvrez FabInventory dans un navigateur
4. Cliquez sur **Importer** et glissez vos fichiers HTML
5. Consultez, triez, marquez les logiciels importants, ajoutez des notes
6. Utilisez l'onglet **Comparer** pour voir les différences entre masters

## Architecture

```
FabInventory/
├── app.py                  # Application Flask
├── templates/              # Templates HTML (Jinja2)
│   ├── base.html           # Layout commun
│   ├── index.html          # Dashboard
│   ├── upload.html         # Page d'import
│   ├── master.html         # Détail d'un master
│   ├── snapshot.html       # Détail d'un scan
│   └── compare.html        # Comparaison
├── static/css/style.css    # Styles
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Stack technique

- Backend : Flask + SQLite
- Frontend : Bootstrap 5 + JavaScript vanilla
- Déploiement : Docker / Docker Compose
- Compatible : Fablab-Suite (endpoint `/api/fabsuite/status`)

## Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `PORT` | `5590` | Port de l'application |
| `DB_PATH` | `/data/fabinventory.db` | Chemin de la base SQLite |
| `UPLOAD_FOLDER` | `/data/uploads` | Dossier d'upload |
| `SECRET_KEY` | `fabinventory-secret` | Clé secrète Flask |
