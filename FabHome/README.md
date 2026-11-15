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

Cette commande sécurise la MAJ : build, redemarrage, attente de healthcheck, logs automatiques en cas d'echec.

Si le script n'est pas executable :

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

Points a verifier :
- Le service doit etre `Up` et `healthy`.
- Le mapping doit etre `3001->3000/tcp`.
- Si besoin, forcer le port : `FABHOME_PORT=3001 docker compose up -d`.

Sous Windows, une erreur `dockerDesktopLinuxEngine pipe not found` signifie que Docker Desktop n'est pas demarre.

### Conflit de nom de conteneur

```bash
docker stop fabhome && docker rm fabhome
docker compose up -d --build
```

### Réinitialiser la configuration

Supprimer le fichier `data/fabhome.db` et redémarrer — la base sera recréée avec les valeurs par défaut.

---

## 📜 Changelog

<!-- changelog:start -->

### v1.7.4 — 2026-11-15

#### Cache et compression HTTP

- Le navigateur garde desormais les fichiers CSS, JavaScript et images une heure (Cache-Control public, max-age=3600), ce qui supprime une revalidation a chaque page ; les pages HTML et les reponses JSON sont compressees en gzip quand le navigateur l'accepte (Vary: Accept-Encoding).

---

### v1.7.3 — 2026-11-15

#### Attente de verrou SQLite

- Attente de verrou : chaque application attend jusqu'a 5 secondes qu'une ecriture concurrente libere la base SQLite, au lieu d'echouer immediatement sur un "database is locked".

---

### v1.7.2 — 2026-11-15

#### Tests automatises dans la CI

- Nouvelle suite de tests FabHome : accueil, API des profils (creation, renommage, suppression, refus du profil par defaut), sante et manifeste FabSuite.
- Les tests FabHome et FabInventory tournent desormais a chaque push dans le job matriciel de la CI.

---

### v1.7.1 — 2026-05-14

#### Lisibilité des cellules et des widgets

- Cellules carrées restaurées : la mesure se fait sur la hauteur réelle de la cellule, sans débordement de la grille.
- Libellés des raccourcis toujours visibles, y compris dans les petites cellules.
- Dimensionnement de la grille et des widgets entièrement en CSS pour un rendu stable à toutes les résolutions.
- Correction de l'affichage des informations tronquées dans les widgets de liste.

---

### v1.7.0 — 2026-05-13

#### Grille 16:9, cellules carrées et bibliothèque d'icônes

- Grille fixe 16×9 cellules carrées : les cellules s'adaptent automatiquement à la résolution de l'écran sans jamais déborder (calcul min largeur / hauteur viewport).
- Widget Raccourci enrichi : prévisualisation de l'icône en temps réel, extraction automatique du favicon depuis l'URL du lien, upload d'une image personnalisée.
- Bibliothèque d'icônes intégrée : retrouvez directement dans la modale toutes les images que vous avez uploadées pour les sélectionner en un clic.
- Onglet Icônes dans les Réglages : gérez votre bibliothèque d'icônes (ajout et suppression) sans quitter FabHome.

---

### v1.6.0 — 2026-04-28

#### Fiabilité popup et robustesse des mises à jour

- Popup Nouveautés fiabilisée : détection basée sur signature complète de release et non plus uniquement la version.
- Chargement des notes de release via URL statique Flask pour un comportement plus robuste.
- Migrations SQLite durcies : version de schéma tracée dans la base et contrôlée au démarrage.
- Suivi centralisé des migrations avec historique idempotent pour limiter les régressions de mise à jour.
- Validation de compatibilité schéma au boot du hub avant activation complète des services.

---

### v1.5.0 — 2026-04-11

#### Popup de nouveautés centralisée

- Ajout de la popup de nouveautés basée sur fichier JSON.
- Ajout d'un lien footer pour reconsulter les mises à jour à tout moment.
- Affichage conditionnel une fois par release via signature stockée localement.

---

### v1.4.2 — 2026-03-21

#### Confort de navigation et UX d'accueil

- Autofocus automatique sur la barre de recherche au chargement de la page.
- Amélioration de la fluidité du flux de recherche pour un usage quotidien plus rapide.

---

### v1.4.0 — 2026-03-15

#### Personnalisation des groupes et widgets

- Ajout de la couleur de fond par groupe pour différencier visuellement les zones.
- Ajout d'un widget dédié pour enrichir le tableau de bord FabHome.

---

<!-- changelog:end -->
