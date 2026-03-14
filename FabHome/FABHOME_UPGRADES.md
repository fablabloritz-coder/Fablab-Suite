# FabHome - Améliorations Multi-Profils & Nouveaux Widgets

## ✅ Modifications terminées

### 1. **Système multi-profils dans la base de données**
- Table `profiles` ajoutée avec colonnes : id, name, icon, color, created_at
- Tables `settings`, `widgets`, `pages` modifiées pour inclure `profile_id`
- Fonctions models.py créées :
  - `get_profiles()`, `get_profile()`, `create_profile()`, `update_profile()`, `delete_profile()`
  - Toutes les fonctions existantes adaptées pour prendre un paramètre `profile_id`

### 2. **API profils dans app.py**
- `GET /api/profiles` - Liste des profils + profil actuel
- `POST /api/profiles` - Créer un profil
- `PUT /api/profiles/<id>` - Modifier un profil
- `DELETE /api/profiles/<id>` - Supprimer un profil
- `POST /api/profiles/switch` - Changer de profil actif (stocké en session)
- Toutes les routes API modifiées pour utiliser le profil actif via `get_current_profile_id()`

### 3. **Correction du check de statut des applications**
- Fonction `_ping()` améliorée :
  - Tentative HEAD puis GET avec User-Agent réaliste
  - Timeout augmenté à 8s
  - Retourne maintenant 'up', 'down' ou 'unknown' (au lieu de juste 'up'/'down')
  - Meilleure gestion des erreurs SSL/timeout

### 4. **Amélioration de la récupération des favicons**
- Fonction `/api/favicon` améliorée avec stratégie multi-niveaux :
  1. Essai direct /favicon.ico
  2. Parse du HTML pour trouver les balises `<link rel="icon">`
  3. Fallback sur Google Favicon Service
- Meilleure compatibilité avec GitHub et autres sites modernes

---

## 🚧 Tâches restantes (Frontend & Widgets)

### 5. **Interface de sélection de profil**

**Fichiers à créer/modifier :**

#### `FabHome/templates/index.html` - Ajouter en haut du body (avant .homepage) :
```html
<!-- Sélecteur de profil (affiché en haut à droite ou en modal au démarrage) -->
<div class="profile-selector" id="profileSelector">
    <button class="profile-btn" id="currentProfileBtn" title="Profil actuel">
        <span class="profile-icon">{{ current_profile.icon if current_profile else '👤' }}</span>
        <span class="profile-name">{{ current_profile.name if current_profile else 'Principal' }}</span>
    </button>
    <div class="profile-dropdown" id="profileDropdown" style="display:none;">
        {% for profile in profiles %}
        <div class="profile-item {% if profile.id == current_profile.id %}active{% endif %}"
             data-profile-id="{{ profile.id }}"
             style="border-left: 3px solid {{ profile.color }};">
            <span class="profile-icon">{{ profile.icon }}</span>
            <span class="profile-name">{{ profile.name }}</span>
        </div>
        {% endfor %}
        <hr>
        <div class="profile-item profile-add" data-action="add-profile">
            <i class="bi bi-plus-circle"></i>
            <span>Nouveau profil</span>
        </div>
        <div class="profile-item profile-manage" data-action="manage-profiles">
            <i class="bi bi-gear"></i>
            <span>Gérer les profils</span>
        </div>
    </div>
</div>
```

#### `FabHome/static/js/app.js` - Ajouter le code de gestion des profils :
```javascript
// Gestion des profils
function initProfiles() {
    var profileBtn = qs('#currentProfileBtn');
    var profileDropdown = qs('#profileDropdown');
   
    if (profileBtn) {
        profileBtn.addEventListener('click', function() {
            profileDropdown.style.display = profileDropdown.style.display === 'none' ? 'block' : 'none';
        });
    }
    
    qsa('.profile-item[data-profile-id]', profileDropdown).forEach(function(item) {
        item.addEventListener('click', function() {
            var profileId = parseInt(this.dataset.profileId);
            api('POST', '/api/profiles/switch', {profile_id: profileId}).then(function() {
                window.location.reload();
            });
        });
    });
    
    var addProfile = qs('.profile-add[data-action="add-profile"]');
    if (addProfile) {
        addProfile.addEventListener('click', function() {
            // TODO: Ouvrir modal de création de profil
        });
    }
}

// Appeler dans init()
initProfiles();
```

#### `FabHome/static/css/style.css` - Ajouter les styles :
```css
/* Sélecteur de profil */
.profile-selector {
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 1000;
}

.profile-btn {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    background: rgba(0,0,0,0.6);
    border: 2px solid rgba(255,255,255,0.2);
    border-radius: 25px;
    color: white;
    cursor: pointer;
    backdrop-filter: blur(10px);
    transition: all 0.3s;
}

.profile-btn:hover {
    background: rgba(0,0,0,0.8);
    border-color: rgba(255,255,255,0.4);
}

.profile-icon {
    font-size: 20px;
}

.profile-dropdown {
    position: absolute;
    top: calc(100% + 10px);
    right: 0;
    min-width: 220px;
    background: rgba(30,30,30,0.95);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    backdrop-filter: blur(10px);
    overflow: hidden;
}

.profile-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    cursor: pointer;
    transition: background 0.2s;
}

.profile-item:hover {
    background: rgba(255,255,255,0.1);
}

.profile-item.active {
    background: rgba(13,110,253,0.2);
}
```

---

### 6. **Zone header pour widgets système**

**Objectif :** Déplacer les widgets "search" et "health" dans un header fixe en haut de page.

#### Modifier `FabHome/templates/index.html` :
```html
<!-- NOUVEAU: Header fixe pour widgets système -->
<header class="system-header">
    <div class="system-header-left">
        <!-- Logo ou titre -->
        <h2 class="system-title">{{ settings.title or "FabHome" }}</h2>
    </div>
    
    <div class="system-header-center">
        {% if widgets.get('search') and widgets.search.enabled %}
        <form id="search-form" class="search-form-header">
            <div class="input-group">
                <span class="input-group-text"><i class="bi bi-search"></i></span>
                <input type="text" class="form-control" id="search-input"
                       placeholder="Rechercher sur le web…" autocomplete="off">
            </div>
        </form>
        {% endif %}
    </div>
    
    <div class="system-header-right">
        {% if widgets.get('health') and widgets.health.enabled %}
        <div class="widget-health-compact" id="health-widget-header">
            <div class="health-item-compact" title="CPU">
                <i class="bi bi-cpu"></i>
                <small id="health-cpu-pct-header">--%</small>
            </div>
            <div class="health-item-compact" title="RAM">
                <i class="bi bi-memory"></i>
                <small id="health-ram-pct-header">--%</small>
            </div>
            <div class="health-item-compact" title="Disque">
                <i class="bi bi-device-hdd"></i>
                <small id="health-disk-pct-header">--%</small>
            </div>
        </div>
        {% endif %}
    </div>
</header>

<!-- Ensuite la zone widgets-bar existante sans search ni health -->
<div class="widgets-bar">
    <!-- Garder greeting, clock, weather, calendar, camera -->
</div>
```

#### `FabHome/static/css/style.css` :
```css
.system-header {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: 60px;
    background: rgba(20,20,20,0.95);
    border-bottom: 1px solid rgba(255,255,255,0.1);
    backdrop-filter: blur(20px);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    z-index: 999;
}

.search-form-header {
    width: 500px;
}

.widget-health-compact {
    display: flex;
    gap: 16px;
}

.health-item-compact {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: rgba(255,255,255,0.8);
}

/* Ajuster la page pour compenser le header fixe */
.homepage {
    margin-top: 60px;
}
```

---

### 7. **Widget Calendrier Nextcloud (CalDAV)**

**Fichiers à créer :**

#### `FabHome/templates/widgets/calendar.html` (nouveau template partial) :
```html
{% if widgets.get('calendar') and widgets.calendar.enabled %}
<div class="widget-calendar" id="calendar-widget">
    <div class="widget-header">
        <i class="bi bi-calendar3"></i>
        <span>Calendrier</span>
    </div>
    <div class="calendar-events" id="calendar-events">
        <div class="loading">Chargement...</div>
    </div>
</div>
{% endif %}
```

#### `FabHome/app.py` - Ajouter une route API CalDAV :
```python
@app.route('/api/calendar/events')
def api_calendar_events():
    profile_id = get_current_profile_id()
    widgets = {w['type']: w for w in models.get_widgets(profile_id)}
    cal_widget = widgets.get('calendar')
    
    if not cal_widget or not cal_widget['enabled']:
        return jsonify(error='Widget calendrier désactivé'), 404
    
    config = cal_widget['config']
    nextcloud_url = config.get('nextcloud_url', '').strip()
    username = config.get('username', '').strip()
    password = config.get('password', '').strip()
    
    if not all([nextcloud_url, username, password]):
        return jsonify(error='Configuration incomplète'), 400
    
    try:
        import caldav
        from datetime import datetime, timedelta
        
        client = caldav.DAVClient(
            url=nextcloud_url,
            username=username,
            password=password
        )
        principal = client.principal()
        calendars = principal.calendars()
        
        events = []
        start = datetime.now()
        end = start + timedelta(days=7)
        
        for calendar in calendars:
            for event in calendar.date_search(start, end):
                comp = event.icalendar_component
                events.append({
                    'summary': str(comp.get('summary', '')),
                    'start': comp.get('dtstart').dt.isoformat() if comp.get('dtstart') else None,
                    'end': comp.get('dtend').dt.isoformat() if comp.get('dtend') else None,
                    'location': str(comp.get('location', '')),
                })
        
        return jsonify(events=events)
    except Exception as e:
        logger.error(f"Erreur CalDAV: {e}")
        return jsonify(error=str(e)), 500
```

**Note :** Ajouter `caldav` dans `requirements.txt`.

---

### 8. **Widget Caméra/Flux Vidéo**

#### `FabHome/templates/widgets/camera.html` :
```html
{% if widgets.get('camera') and widgets.camera.enabled %}
<div class="widget-camera" id="camera-widget">
    <div class="widget-header">
        <i class="bi bi-camera-video"></i>
        <span>Caméras</span>
    </div>
    <div class="camera-grid" id="camera-grid">
        {% for stream in widgets.camera.config.get('streams', []) %}
        <div class="camera-stream">
            <div class="camera-title">{{ stream.name }}</div>
            <img src="{{ stream.url }}" alt="{{ stream.name }}" class="camera-img">
            <!-- Pour RTSP/MJPEG : utiliser une balise <img> avec snapshot ou intégrer un player WebRTC -->
        </div>
        {% endfor %}
    </div>
</div>
{% endif %}
```

#### `FabHome/static/css/style.css` :
```css
.widget-camera {
    padding: 16px;
    background: rgba(0,0,0,0.3);
    border-radius: 12px;
    margin-bottom: 16px;
}

.camera-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-top: 12px;
}

.camera-stream {
    position: relative;
    aspect-ratio: 16/9;
    background: #000;
    border-radius: 8px;
    overflow: hidden;
}

.camera-title {
    position: absolute;
    top: 8px;
    left: 8px;
    background: rgba(0,0,0,0.7);
    color: white;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    z-index: 1;
}

.camera-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}
```

**Note :** Pour RTSP, il faut un serveur de streaming intermédiaire (comme `rtsp-simple-server` ou `mediamtx`) qui convertit en HTTP/WebRTC.

---

### 9. **Personnalisation avancée des widgets**

**Options à ajouter dans les configs des widgets :**

#### Exemple pour le widget Weather :
```json
{
  "latitude": 48.69,
  "longitude": 6.18,
  "city": "Nancy",
  "fontSize": "large",
  "showDetails": true,
  "iconSize": "medium"
}
```

#### Modifier  les templates pour appliquer les styles dynamiques :
```html
<div class="widget-weather {{ 'size-' + widgets.weather.config.get('fontSize', 'medium') }}"
     style="--icon-size: {{ widgets.weather.config.get('iconSize', '24') }}px;">
    ...
</div>
```

#### Ajouter des classes CSS pour les tailles :
```css
.widget-weather.size-small { font-size: 14px; }
.widget-weather.size-medium { font-size: 16px; }
.widget-weather.size-large { font-size: 20px; }
```

---

## 📋 Checklist finale

- [ ] Créer l'interface de sélection de profil (HTML + JS + CSS)
- [ ] Créer la zone header pour widgets système
- [ ] Implémenter le widget calendrier Nextcloud (+ ajouter caldav dans requirements.txt)
- [ ] Implémenter le widget caméra/flux vidéo
- [ ] Ajouter les options de personnalisation dans les modals de config des widgets
- [ ] Tester le système multi-profils complet
- [ ] Tester les nouveaux widgets
- [ ] Vérifier que les favicons s'affichent correctement (GitHub, etc.)
- [ ] Vérifier que les statuts d'applications sont corrects

---

## 🚀 Pour lancer et tester

```bash
cd FabHome
python app.py
# Ouvrir http://localhost:3000
```

Les profils sont maintenant gérés via session Flask. Chaque utilisateur peut avoir son propre profil avec ses propres pages, widgets, réglages.
