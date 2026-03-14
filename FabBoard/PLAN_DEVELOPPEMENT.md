# 🚀 Plan de Développement FabBoard — Phase 1.5+

**Date** : 6 mars 2026  
**Objectif** : Passer de l'architecture existante à un système fonctionnel et extensible

---

## 📊 État des lieux

### ✅ Ce qui EXISTE déjà

| Composant | État | Description |
|-----------|------|-------------|
| **Base de données** | ✅ Complet | Tables sources, slides, widgets, layouts |
| **API Sources** | ✅ Complet | CRUD complet (GET, POST, PUT, DELETE, test) |
| **Widgets déclarés** | ✅ Complet | 10 widgets en DB |
| **Layouts** | ✅ Complet | 7 layouts (1×1, 2×1, 2×2, 3×2, etc.) |
| **Templates base** | ✅ Complet | dashboard.html, slides.html, parametres.html |
| **Route principales** | ✅ Complet | /, /slides, /parametres |

### ❌ Ce qui MANQUE

| Composant | État | Blocage |
|-----------|------|---------|
| **Templates widgets** | ❌ Absent | Pas de `templates/widgets/*.html` |
| **UI Gestion sources** | ⚠️ Partiel | Interface paramètres incomplète |
| **Système sync** | ❌ Absent | Pas de worker background pour polling |
| **JS Dashboard** | ⚠️ Partiel | Affichage slides dynamique à implémenter |
| **Widgets core** | ❌ Absent | Horloge, texte, météo pas encore créés |

---

## 🎯 Plan de développement (3 phases)

### Phase 1 : Widgets Core (Priorité HAUTE) — 1-2 jours

**Objectif** : Créer 3 widgets fonctionnels sans dépendance externe.

#### 1.1 Widget Horloge ⏰
```
templates/widgets/horloge.html
- Affiche heure + date locale
- Mise à jour automatique (setInterval 1s)
- Design moderne (gradient background)
```

#### 1.2 Widget Texte libre 📝
```
templates/widgets/texte_libre.html
- Zone texte configurable
- Support Markdown (optionnel)
- Couleur/taille personnalisables
```

#### 1.3 Widget Météo 🌤️
```
templates/widgets/meteo.html
- Appelle OpenWeatherMap API (gratuite)
- Affiche température + description
- Config via source HTTP générique
```

**Livrables** :
- ✅ 3 fichiers HTML dans `templates/widgets/`
- ✅ CSS intégré dans chaque template
- ✅ JavaScript pour refresh auto
- ✅ Test sur slide démo

---

### Phase 2 : UI Gestion Sources (Priorité HAUTE) — 1 jour

**Objectif** : Interface admin complète pour gérer sources.

#### 2.1 Page Paramètres — Sources
```
templates/parametres.html (section Sources)
- Liste sources existantes (tableau)
- [+ Nouvelle source] (modal)
- [Éditer] / [🗑️ Supprimer] / [Test]
- Affichage status (OK/ERROR)
- Dernière sync + erreur si applicable
```

#### 2.2 Modal Création/Édition
```
- Champs : nom, type (select), URL, interval
- Credentials (JSON textarea ou champs)
- [Tester connexion] → Appelle /api/sources/{id}/test
- [Sauvegarder]
```

#### 2.3 JavaScript Gestion
```
static/js/parametres.js
- Fetch API sources (GET /api/sources)
- Créer source (POST)
- Tester connexion (afficher résultat)
- Supprimer source (confirmation)
```

**Livrables** :
- ✅ Interface sources complète
- ✅ CRUD fonctionnel (frontend + backend)
- ✅ Test connexion visible
- ✅ Messages success/erreur

---

### Phase 3 : Système Sync (Priorité MOYENNE) — 2 jours

**Objectif** : Worker background pour polling automatique des sources.

#### 3.1 Worker de Synchronisation
```python
# sync_worker.py
import threading
import time
from models import get_db
from app import fetch_source_data, cache_source_data

def sync_worker_loop():
    """Boucle infinie : poll chaque source active."""
    while True:
        db = get_db()
        sources = db.execute(
            'SELECT * FROM sources WHERE actif = 1'
        ).fetchall()
        
        for source in sources:
            # Vérifier si sync nécessaire
            if should_sync(source):
                try:
                    data, error = fetch_source_data(source)
                    if data:
                        cache_source_data(source['id'], data)
                        update_derniere_sync(source['id'])
                    else:
                        log_error(source['id'], error)
                except Exception as e:
                    log_error(source['id'], str(e))
        
        # Attendre 10 secondes avant prochain cycle
        time.sleep(10)

def start_sync_worker():
    """Démarre le worker en background thread."""
    thread = threading.Thread(target=sync_worker_loop, daemon=True)
    thread.start()
```

#### 3.2 Intégration App
```python
# Dans app.py, après init_db
@app.before_first_request
def start_background_tasks():
    from sync_worker import start_sync_worker
    start_sync_worker()
```

#### 3.3 Cache Sources
```python
# Table sources_cache
CREATE TABLE sources_cache (
  source_id INTEGER PRIMARY KEY,
  data_json TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  fetched_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (source_id) REFERENCES sources(id)
);

# Fonctions
def cache_source_data(source_id, data):
    """Stocke données dans cache."""
    
def get_cached_data(source_id):
    """Récupère données en cache."""
```

**Livrables** :
- ✅ Worker background fonctionnel
- ✅ Cache SQLite pour données
- ✅ Polling automatique (10s loop)
- ✅ Gestion erreurs + logs

---

### Phase 4 : Dashboard Dynamique (Priorité MOYENNE) — 1 jour

**Objectif** : Affichage slides avec rotation automatique.

#### 4.1 Dashboard.html
```html
<div id="slide-container">
  <!-- Slides chargées dynamiquement -->
</div>

<script>
let currentSlide = 0;
let slides = [];

async function loadSlides() {
  const resp = await fetch('/api/slides');
  slides = await resp.json();
  displaySlide(0);
}

function displaySlide(index) {
  const slide = slides[index];
  // Render slide + widgets
  renderSlide(slide);
  
  // Auto-advance après temps_affichage
  setTimeout(() => {
    currentSlide = (currentSlide + 1) % slides.length;
    displaySlide(currentSlide);
  }, slide.temps_affichage * 1000);
}

loadSlides();
</script>
```

#### 4.2 API Slides
```python
@app.route('/api/slides')
def api_get_slides_full():
    """Retourne slides complètes avec widgets + cache."""
    slides = get_all_slides(include_inactive=False)
    
    for slide in slides:
        # Enrichir avec données widgets
        for widget in slide['widgets']:
            widget['data'] = get_widget_data(widget['widget_id'])
    
    return jsonify({'slides': slides})
```

**Livrables** :
- ✅ Dashboard rotation automatique
- ✅ Affichage widgets dynamiques
- ✅ Smooth transitions
- ✅ Fullscreen mode

---

### Phase 5 : Widgets Fabtrack (Priorité BASSE) — 1-2 jours

**Objectif** : Intégrer données Fabtrack optionnellement.

#### 5.1 Ajouter Endpoints Fabtrack
```python
# Dans Fabtrack/app.py

@app.route('/api/stats/summary')
def api_stats_summary():
    """Statistiques globales."""
    return jsonify({
        'total_interventions': ...,
        'total_3d_grammes': ...,
        'total_decoupe_m2': ...,
        'total_papier_feuilles': ...
    })

@app.route('/api/machines/status')
def api_machines_status():
    """État de chaque machine."""
    machines = get_all_machines()
    return jsonify({'machines': machines})
```

#### 5.2 Widget Compteurs Fabtrack
```html
templates/widgets/fabtrack_compteurs.html
- Affiche 4 compteurs (interventions, 3D, découpe, papier)
- Fetch depuis cache source Fabtrack
- Refresh toutes les 10s
```

**Livrables** :
- ✅ Endpoints API Fabtrack
- ✅ Widget compteurs fonctionnel
- ✅ Widget machines (optionnel)
- ✅ Test avec Fabtrack en marche

---

## 📅 Planning suggéré

| Phase | Durée | Début | Fin | Priorité |
|-------|-------|-------|-----|----------|
| **Phase 1** : Widgets Core | 1-2 jours | J+0 | J+2 | 🔴 HAUTE |
| **Phase 2** : UI Sources | 1 jour | J+2 | J+3 | 🔴 HAUTE |
| **Phase 3** : Système Sync | 2 jours | J+3 | J+5 | 🟡 MOYENNE |
| **Phase 4** : Dashboard | 1 jour | J+5 | J+6 | 🟡 MOYENNE |
| **Phase 5** : Fabtrack | 1-2 jours | J+6 | J+8 | 🟢 BASSE |

**Total estimé** : 6-8 jours de développement

---

## 🎯 Jalons (Milestones)

### Milestone 1 : MVP Fonctionnel (Phase 1-2)
- 3 widgets core fonctionnels
- Interface gestion sources
- **Livrables** : Démo avec 1 slide + horloge

### Milestone 2 : Autonome (Phase 3-4)
- Sync automatique
- Dashboard rotation
- **Livrables** : TV affichage continu

### Milestone 3 : Intégré (Phase 5)
- Fabtrack connecté
- Widgets données réelles
- **Livrables** : Production-ready

---

## 🛠️ Prochaines actions immédiates

### Action 1 : Créer répertoire widgets
```bash
mkdir FabBoard/templates/widgets
```

### Action 2 : Widget Horloge (30 min)
```bash
# Créer templates/widgets/horloge.html
# Tester sur slide démo
```

### Action 3 : Widget Texte (30 min)
```bash
# Créer templates/widgets/texte_libre.html
# Config JSON : {"titre": "...", "contenu": "..."}
```

### Action 4 : Test complet
```bash
python start.py
# http://localhost:5580/slides
# Ajouter widgets à une slide
# Voir sur dashboard
```

---

## 📋 Checklist de démarrage

- [ ] Créer `templates/widgets/` directory
- [ ] Widget horloge fonctionnel
- [ ] Widget texte libre fonctionnel
- [ ] Widget météo fonctionnel
- [ ] Interface sources dans `/parametres`
- [ ] Test création source HTTP
- [ ] Système sync (worker)
- [ ] Dashboard rotation automatique
- [ ] Fabtrack endpoints API
- [ ] Widget compteurs Fabtrack
- [ ] Documentation mise à jour

---

## 💡 Recommandations

### Architecture
- ✅ Widgets = fichiers HTML autonomes
- ✅ Chaque widget = HTML + CSS + JS intégré
- ✅ Config widget via `config_json` (flexible)
- ✅ Cache sources dans SQLite (pas en mémoire)

### Sécurité
- ✅ Chiffrer credentials (Phase 3)
- ✅ Valider URLs (SSRF protection)
- ✅ Timeout strict (5s max)
- ✅ Rate limiting webhooks (futur)

### Performance
- ✅ Polling 10s minimum (pas à chaque seconde)
- ✅ Cache TTL configurable par source
- ✅ Lazy loading widgets (affichage slide par slide)

### UX
- ✅ Messages success/error clairs
- ✅ Status visuel sources (⚪ OK, 🔴 ERROR)
- ✅ Test connexion avant save
- ✅ Confirmation suppression

---

## 🚀 Validation finale

**Critères de succès Phase 1** :
- [ ] 3 widgets affichés sur 1 slide
- [ ] Interface sources permet CRUD
- [ ] Dashboard tourne en continu
- [ ] Documentation à jour

**Ready pour production** :
- [ ] Tests fonctionnels complets
- [ ] Performance validée (TV 24/7)
- [ ] Sécurité credentials
- [ ] Backup DB automatique

---

**Créé par** : GitHub Copilot  
**Status** : Plan validé, prêt à démarrer 🚀  
**Prochaine étape** : Commencer Phase 1 (Widgets Core)
