# FabHome — Page d'accueil personnalisée

Page d'accueil web auto-hébergée avec **interface d'administration graphique**.
Remplace avantageusement Homepage (gethomepage.dev) sans avoir à toucher du YAML : tout se configure depuis le navigateur.

## Fonctionnalités

- **Groupes de liens** organisés en grille responsive
- **Surveillance de statut** (pastille verte/rouge) pour vos services
- **Barre de recherche** (Google, DuckDuckGo, Bing, Startpage)
- **Horloge & date** en temps réel
- **Message d'accueil** personnalisé (Bonjour / Bon après-midi / Bonsoir)
- **Widget météo** via Open-Meteo (sans clé API)
- **Thème sombre / clair** + image de fond personnalisable
- **Sélecteur d'icônes** intégré (Bootstrap Icons, émojis, ou URL d'image)
- **Administration 100 % web** — aucun fichier de configuration à éditer

## Déploiement Docker

### Prérequis

- Docker + Docker Compose installés
- Port 3001 disponible (ou configurable via `FABHOME_PORT`)

### Lancement

```bash
cd ~/fabhome          # ou le dossier de votre choix
cp .env.example .env  # une seule fois
docker compose up -d --build
```

L'application est accessible à `http://<IP-SERVEUR>:3001`.

### Mise à jour

```bash
cd ~/fabhome
git pull --ff-only origin main
./deploy_safe.sh
```

Cette commande sécurise la MAJ: build, redemarrage, attente de healthcheck, logs automatiques en cas d'echec.

Si le script n'est pas executable:

```bash
chmod +x deploy_safe.sh
./deploy_safe.sh
```

### Arrêt / Relance / Redémarrage

```bash
docker compose stop       # Arrêter
docker compose start      # Relancer
docker compose restart    # Redémarrer
```

### Rebuild complet (sans perte de données)

```bash
docker compose down
docker compose up -d --build
```

## Utilisation

1. Ouvrir `http://<IP-SERVEUR>:3001` (Docker) ou `http://<IP-SERVEUR>:3000` (lancement Python direct) dans le navigateur
2. Cliquer sur l'**engrenage** en bas à droite pour accéder à l'administration
3. **Onglet Groupes & Liens** : créer des groupes, ajouter des liens, les réordonner
4. **Onglet Widgets** : activer/désactiver horloge, recherche, météo, message d'accueil
5. **Onglet Apparence** : titre, thème sombre/clair, image de fond

## Icônes

Trois formats supportés :
- **Bootstrap Icons** : `bi-server`, `bi-globe`, `bi-house`… (sélecteur intégré)
- **Émojis** : `🖥️`, `📊`, `🏠`…
- **URL d'image** : `https://exemple.com/icone.png`

Vous pouvez aussi placer des icônes personnalisées dans le dossier `icons/` — elles seront accessibles à `/static/icons/nom-fichier.png`.

## Structure

```
FabHome/
├── app.py              # Application Flask + API REST
├── models.py           # Couche base de données SQLite
├── requirements.txt    # Dépendances Python
├── Dockerfile          # Image Docker
├── docker-compose.yml  # Orchestration
├── data/               # Base de données (volume Docker)
├── icons/              # Icônes personnalisées (volume Docker)
├── static/
│   ├── css/style.css
│   └── js/
│       ├── app.js      # Logique page d'accueil
│       └── admin.js    # Logique administration
└── templates/
    ├── base.html
    ├── index.html      # Page d'accueil
    └── admin.html      # Interface d'administration
```

## Dépannage

### L'application ne répond pas

```bash
docker compose ps              # Vérifier l'état du conteneur
docker compose logs --tail=50  # Lire les logs
curl http://localhost:3001/    # Tester localement
```

### Port 3001 inaccessible après MAJ

```bash
cd ~/fabhome
docker compose ps
docker compose logs --tail=150 fabhome
docker compose up -d --build fabhome
docker compose ps
```

Points a verifier:
- Le service doit etre `Up` et `healthy`.
- Le mapping doit etre `3001->3000/tcp`.
- Si besoin, forcer le port: `FABHOME_PORT=3001 docker compose up -d`.

Sous Windows, une erreur `dockerDesktopLinuxEngine pipe not found` signifie que Docker Desktop n'est pas demarre.

### Conflit de nom de conteneur

```bash
docker stop fabhome && docker rm fabhome
docker compose up -d --build
```

### Réinitialiser la configuration

Supprimer le fichier `data/fabhome.db` et redémarrer — la base sera recréée avec les valeurs par défaut.
