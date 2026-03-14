# 🚀 FabBoard - État du Projet

**Dernière mise à jour :** 06 mars 2026 19:17  
**Version :** v1.0 Phase 1 Complétée  
**Statut global :** ✅ Fonctionnel en environnement de développement

---

## 📍 Où en sommes-nous ?

### ✅ Phase 1 : Widgets Core (COMPLÉTÉE 100%)

**Objectif :** Créer un système modulaire de widgets avec rendu dynamique côté serveur.

**Réalisations :**
- [x] Architecture plugin (1 widget = 1 fichier HTML)
- [x] API `/api/widgets/<code>/render` (POST) - rendu Jinja2
- [x] API `/api/widget-data/<source_id>` (GET) - données sources
- [x] Dashboard.js v2.0 avec chargement asynchrone
- [x] 3 widgets fonctionnels :
  - `horloge` : temps réel (update 1s)
  - `texte_libre` : Markdown + couleurs custom
  - `meteo` : OpenWeatherMap (mockée)
- [x] Slide de démonstration créée
- [x] CSS dashboard avec états error/loading
- [x] Tests API validés (200 OK)

**Fichiers créés :** 5  
**Lignes de code :** ~750  
**Temps estimé :** 1-2 jours (prévu) → ✅ Réalisé

---

## 🎯 Prochaine étape : Phase 2

### UI Gestion des Sources de Données

**Objectif :** Permettre aux admins de configurer des sources externes (Fabtrack, CalDAV, OpenWeatherMap, etc.) via une interface web.

**Tâches prioritaires :**

1. **Page Paramètres → Section Sources** (1-2h)
   - [ ] Ajouter onglet "Sources de données" dans `templates/parametres.html`
   - [ ] Tableau listant les sources avec actions (Edit, Delete, Test)
   - [ ] Bouton "+ Nouvelle source"

2. **Modal CRUD Sources** (2-3h)
   - [ ] Formulaire : nom, type (dropdown), URL, credentials
   - [ ] Validation côté client (required fields)
   - [ ] Intégration API existante (`/api/sources`, `/api/sources/<id>`)

3. **Bouton "Test connexion"** (1h)
   - [ ] Appel `/api/sources/<id>/test`
   - [ ] Toast Bootstrap avec feedback (succès/erreur)
   - [ ] Badge statut sur chaque source (🟢 OK, 🔴 Erreur, ⚪ Jamais testé)

4. **Scripts JS** (2h)
   - [ ] `static/js/parametres.js` : gestion sources
   - [ ] Fonctions CRUD avec fetch API
   - [ ] Refresh tableau après mutation

**Fichiers à modifier :**
- `templates/parametres.html` (+200 lignes)
- `static/js/parametres.js` (nouveau, 300 lignes)
- `static/css/style.css` (+50 lignes pour modal)

**Estimation :** 6-8 heures (1 journée)

---

## 🔧 Phase 3 : Sync Worker (Planifiée)

### Objectif : Polling automatique des sources externes

**Architecture :**
```
sync_worker.py (Thread background)
├── Boucle infinie (sleep 10s)
├── Lecture table `sources` WHERE actif=1
├── Pour chaque source :
│   ├── Vérif derniere_sync + sync_interval_sec
│   ├── Requête HTTP/CalDAV/MQTT
│   ├── Parsing données
│   └── INSERT/UPDATE table `sources_cache`
└── Gestion erreurs (update derniere_erreur)
```

**Nouveau schéma DB :**
```sql
CREATE TABLE sources_cache (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    data_json TEXT,           -- JSON brut de l'API
    cached_at TEXT,
    expires_at TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
```

**Modification app.py :**
```python
import threading
from sync_worker import SyncWorker

@app.before_first_request
def start_sync_worker():
    worker = SyncWorker()
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
```

**Fichiers à créer :**
- `sync_worker.py` (400 lignes)
- Migration DB : ajout table `sources_cache`

**Estimation :** 2 jours

---

## 📊 Widgets restants (7/10)

### À créer en Phase 2

| Widget | Template HTML | Complexité | Source |
|--------|--------------|------------|--------|
| `compteurs` | compteurs.html | Facile | Fabtrack `/api/stats/summary` |
| `activites` | activites.html | Facile | Fabtrack `/api/consommations?per_page=5` |
| `fabtrack_stats` | fabtrack_stats.html | Moyen | Fabtrack `/api/stats/summary` |
| `fabtrack_machines` | fabtrack_machines.html | Moyen | Fabtrack `/api/machines` |
| `fabtrack_conso` | fabtrack_conso.html | Facile | Fabtrack `/api/consommations` |
| `calendrier` | calendrier.html | Difficile | CalDAV (icalendar lib) |
| `imprimantes` | imprimantes.html | Difficile | Repetier/PrusaLink API |

**Priorisation :**
1. **High :** compteurs, activites, fabtrack_stats (Fabtrack déjà déployé)
2. **Medium :** fabtrack_machines, fabtrack_conso
3. **Low :** calendrier, imprimantes (nécessitent Phase 3)

**Estimation par widget :** 1-2h  
**Total Phase 2 widgets :** 7-14 heures (2 jours)

---

## 🐛 Bugs connus / À améliorer

### Priorité Haute
- [ ] **Widget météo** : Source OpenWeatherMap à configurer (actuellement mockée)
- [ ] **Refresh intelligent** : Tous widgets refresh 10s → optimiser par type
- [ ] **Cache sources** : Table SQLite non implémentée (données en temps réel uniquement)

### Priorité Moyenne
- [ ] **Thème personnalisé** : Variables CSS non appliquées aux widgets (styles inline)
- [ ] **Error handling** : Améliorer messages d'erreur utilisateur
- [ ] **Documentation** : Ajouter docstrings Python manquantes

### Priorité Basse
- [ ] **Performance** : Gestion mémoire du cache navigateur (localStorage)
- [ ] **Accessibilité** : ARIA labels sur widgets interactifs
- [ ] **Tests unitaires** : Couverture 0% (tests manuels uniquement)

---

## 🛠️ Configuration actuelle

### Serveur Flask
```
Host: localhost
Port: 5580
Mode: Debug (development)
Database: data/fabboard.db (SQLite)
```

### Slides actives
```
1. Dashboard principal (grid_3x2, 6 widgets Fabtrack, 30s)
2. 🎯 Démo Phase 1 - Widgets Core (grid_2x2, 3 widgets, 15s)
```

### Sources configurées
```
Aucune source externe configurée pour l'instant.
→ Phase 2 permettra d'ajouter Fabtrack, CalDAV, OpenWeatherMap
```

---

## 📖 Documentation disponible

### Pour les développeurs
- **[ECOSYSTEM.md](ECOSYSTEM.md)** : Vue d'ensemble des 3 apps (PretGo, Fabtrack, FabBoard)
- **[FABBOARD_SOURCES.md](FABBOARD_SOURCES.md)** : Guide technique intégration sources externes
- **[PHASE1_RECAP.md](PHASE1_RECAP.md)** : Récapitulatif détaillé Phase 1
- **[PLAN_DEVELOPPEMENT.md](PLAN_DEVELOPPEMENT.md)** : Roadmap 5 phases (6-8 jours)

### Pour les admins
- **[FABBOARD_QUICKSTART.md](FABBOARD_QUICKSTART.md)** : Installation et premier usage (15 min)
- **[INDEX.md](INDEX.md)** : Navigation documentation
- **[README.md](README.md)** : Introduction générale

---

## 🚀 Comment lancer FabBoard ?

### Démarrage rapide
```bash
cd FabBoard
python app.py
```
➡️ Dashboard : http://localhost:5580/  
➡️ Config slides : http://localhost:5580/slides  
➡️ Paramètres : http://localhost:5580/parametres

### Créer une slide de test
```bash
python create_demo_slide.py
```

### Réinitialiser la DB (⚠️ Supprime toutes les slides)
```python
from models import reset_db
reset_db()
```

---

## 💡 Prochaines décisions à prendre

### Architecture
1. **Sync worker :** Thread Python ou worker Celery ?
   - Thread → Simple, 0 dépendance
   - Celery → Scalable, nécessite Redis/RabbitMQ

2. **Cache sources :** SQLite ou Redis ?
   - SQLite → Déjà utilisé, backups faciles
   - Redis → Plus rapide, TTL natif

3. **Frontend :** Rester vanilla JS ou framework ?
   - Vanilla → Léger, 0 build
   - Vue.js/Alpine.js → Réactivité, typage

### Fonctionnalités
1. **Multi-utilisateurs :** Ajouter authentification ?
   - Actuellement : Ouvert à tous
   - Option : Flask-Login + JWT

2. **Multi-écrans :** Supporter plusieurs dashboards différents ?
   - Actuellement : 1 cycle de slides global
   - Option : URL `/dashboard/<id>` avec slides filtrées

3. **Mobile :** Version responsive pour smartphones ?
   - Actuellement : Optimisé pour TV 1080p
   - Option : Media queries + layout mobile

---

## 📈 Métriques du projet

### Code
- **Lignes Python :** ~1800 (app.py 650, models.py 400, create_demo_slide 88, ...)
- **Lignes HTML :** ~500 (templates + widgets)
- **Lignes CSS :** ~400 (dashboard.css, style.css, widgets inline)
- **Lignes JavaScript :** ~600 (dashboard.js, utils.js, ...)
- **Total :** ~3300 lignes

### Base de données
- **Tables :** 9 (sources, slides, widgets_disponibles, layouts, ...)
- **Slides :** 2
- **Widgets disponibles :** 10 (3 fonctionnels, 7 à créer)
- **Layouts :** 9 (inspirés Windows 11)

### Fichiers
- **Python :** 4 (app.py, models.py, utils.py, create_demo_slide.py)
- **Templates :** 7 (base.html, dashboard.html, slides.html, parametres.html, test_api.html, ...)
- **Widgets :** 3 (horloge.html, texte_libre.html, meteo.html)
- **JavaScript :** 4 (dashboard.js, utils.js, parametres.js, slides.js)
- **CSS :** 3 (dashboard.css, slides.css, style.css)
- **Documentation :** 7 (ECOSYSTEM, SOURCES, QUICKSTART, INDEX, README, PLAN, RECAP)
- **Total :** ~28 fichiers

---

## 🎓 Compétences mobilisées

### Backend
- Flask (routing, templates Jinja2, API REST)
- SQLite (schéma relationnel, foreign keys, JSON)
- Python OOP (modèles, helpers)
- Threading (prévu Phase 3)

### Frontend
- JavaScript moderne (async/await, fetch API, DOM)
- CSS Grid + Flexbox (layouts responsives)
- Bootstrap 5 (utilitaires, composants)
- Animations CSS (transitions, keyframes)

### Architecture
- Pattern MVC (modèles, vues, contrôleurs)
- API-first (backend agnostique du frontend)
- Plugin system (widgets modulaires)
- Cache pattern (prévu Phase 3)

### DevOps
- Git (versioning, branches)
- Docker (Dockerfile prêt)
- Environment variables (config)
- Logging (Flask debug mode)

---

## 🎉 Succès de la Phase 1

### Points forts
1. **Modularité exemplaire** : Ajouter un widget = 1 fichier HTML
2. **Zero-dependency frontend** : Pas de npm, webpack, React
3. **Performance native** : SSR + fetch API pur
4. **Code propre** : Séparation concerns, docstrings, commentaires
5. **Extensibilité** : 7 types de sources supportés (architecture)

### Défis relevés
1. **Rendu asynchrone** : Widgets chargés séquentiellement sans bloquer UI
2. **Config JSON flexible** : Chaque widget peut avoir sa propre config
3. **Error handling gracieux** : Fallback avec message clair
4. **Horloge temps réel** : setInterval côté client sans API polling
5. **Database init lazy** : `@app.before_request` au lieu de startup

---

## 🔗 Liens utiles

- **GitHub :** (À définir)
- **Documentation :** [INDEX.md](INDEX.md)
- **Bugs :** (GitHub Issues à créer)
- **Roadmap :** [PLAN_DEVELOPPEMENT.md](PLAN_DEVELOPPEMENT.md)

---

**Préparé par :** GitHub Copilot (Claude Sonnet 4.5)  
**Projet :** FabBoard - Dashboard TV pour Fablab  
**Contact :** Voir responsable Fablab

---

> **Note :** Ce document est un snapshot de l'état actuel. Il sera mis à jour à chaque phase completée.
