# 🚀 Phase 3 : Sync Worker Background + Cache SQLite

**Date** : 6 mars 2026  
**Statut** : ✅ V1 Complétée

---

## 📋 Résumé

Phase 3 implémente un **worker de synchronisation en arrière-plan** qui sondage automatiquement les sources externes configurées et cache les données dans SQLite. Cela élimine le besoin de requêtes HTTP en direct sur chaque accès au dashboard.

### Objectifs atteints

| Objectif | Statut | Description |
|----------|--------|-------------|
| Worker threading | ✅ | Classe `SyncWorker` avec boucle infinie en daemon thread |
| Table cache | ✅ | `sources_cache` avec schema (source_id, data_json, expires_at, fetched_at) |
| Polling cycle | ✅ | Boucle 10s, vérifie chaque source selon `sync_interval_sec` |
| API Cache | ✅ | GET/DELETE endpoints pour interroger et nettoyer le cache |
| Intégration | ✅ | Démarrage au premier accès HTTP via `@app.before_request` |
| Nettoyage | ✅ | Shutdown gracieux via `@app.teardown_appcontext` |

---

## 🏗️ Architecture

### 1. Worker Thread (`sync_worker.py`)

```
┌─────────────────────────────────────┐
│   SyncWorker (daemon thread)        │
│                                     │
│  _sync_loop() [infinite]            │
│   │                                 │
│   ├─ sleep(poll_interval=10s)       │
│   │                                 │
│   └─ for each active source:        │
│       ├─ _should_sync()?            │
│       │   └─ Check derniere_sync    │
│       │       + sync_interval_sec   │
│       │                             │
│       └─ _fetch_source_data()       │
│           ├─ _fetch_fabtrack()     │
│           ├─ _fetch_printer_api()  │
│           ├─ _fetch_caldav()       │
│           ├─ _fetch_openweathermap()
│           └─ _fetch_generic_http() │
│               │                     │
│               ├─ Parse response     │
│               ├─ Extract data       │
│               └─ Return (data, err) │
│                                     │
│           _cache_source_data()     │
│               └─ INSERT OR REPLACE  │
│                   sources_cache     │
│                                     │
│           UPDATE sources            │
│               derniere_sync,        │
│               derniere_erreur       │
│                                     │
└─────────────────────────────────────┘
```

### 2. Cache Schema

```sql
CREATE TABLE sources_cache (
    source_id INTEGER PRIMARY KEY,      -- FK sources(id)
    data_json TEXT NOT NULL,             -- Payload JSON complet
    expires_at TEXT NOT NULL,            -- Timestamp ISO 8601
    fetched_at TEXT DEFAULT (now())      -- Quand récupéré
);

CREATE INDEX idx_sources_cache_expires ON sources_cache(expires_at);
```

### 3. Lifecycle du Worker

```
Flask startup
    │
    └─ @app.before_request triggered
        │
        └─ ensure_db():
            ├─ init_db()
            │   └─ CREATE sources_cache table
            │
            └─ start_sync_worker(poll_interval=10)
                └─ SyncWorker instance
                    └─ Thread daemon
                        └─ Running: True
                        
Cache updates: Every 10 seconds
    │
    ├─ DB query: SELECT * FROM sources where actif=1
    │
    ├─ For each source:
    │   ├─ Check: now() >= derniere_sync + interval?
    │   │
    │   ├─ If yes:
    │   │   ├─ Fetch data (with timeout=4s)
    │   │   ├─ Parse/extract payload
    │   │   └─ Cache: INSERT sources_cache
    │   │
    │   └─ Catch exceptions → log to derniere_erreur
    │
    └─ Sleep 10s, repeat

Flask shutdown
    │
    └─ @app.teardown_appcontext triggered
        │
        └─ stop_sync_worker()
            └─ worker.stop()
                └─ running = False
                └─ Thread joins (timeout=5s)
```

---

## 🔗 API Endpoints (Phase 3)

### GET `/api/worker/status`

**Description** : Retourne l'état du worker et des caches

**Response** (200 OK):
```json
{
  "success": true,
  "worker_running": true,
  "worker_poll_interval": 10,
  "sources": [
    {
      "id": 3,
      "nom": "Nextcloud",
      "type": "nextcloud_caldav",
      "actif": 1,
      "sync_interval_sec": 15,
      "derniere_sync": "2026-03-06 20:25:20",
      "derniere_erreur": "",
      "cache_valid": true,
      "cache_expires_at": "2026-03-06 20:25:35",
      "cache_fetched_at": "2026-03-06 20:25:20"
    }
  ]
}
```

---

### GET `/api/cache/<source_id>`

**Description** : Récupère le payload complet en cache pour une source

**Parameters**:
- `source_id` (int): ID de la source

**Response** (200 OK):
```json
{
  "success": true,
  "data": {
    "summary": {
      "total_interventions": 42,
      "total_3d_grammes": 1250,
      ...
    },
    "consommations": [...],
    "machines": [...],
    "fetched_at": "2026-03-06 20:25:20"
  }
}
```

**Response** (404):
```json
{
  "error": "Pas de cache valide pour cette source"
}
```

---

### DELETE `/api/cache`

**Description** : Nettoie les caches expirés (par les timestamps `expires_at`)

**Response** (200 OK):
```json
{
  "success": true,
  "cleaned": 2,
  "message": "Supprimé 2 cache(s) expiré(s)"
}
```

---

### POST `/api/cache/<source_id>/refresh`

**Description** : Force la synchronisation d'une source au prochain cycle

**Parameters**:
- `source_id` (int): ID de la source à forcer

**Response** (200 OK):
```json
{
  "success": true,
  "message": "Sync forcée demandée pour la prochaine pollarisation"
}
```

**Response** (503 Service Unavailable):
```json
{
  "error": "Worker non actif",
  "hint": "Le worker de sync n'est pas actif. Les caches seront mis à jour lors du prochain cycle de polling."
}
```

---

## 📊 Data Flow

```
Configuration (Phase 2)
    │
    └─ POST /api/sources
       Create source with:
       - nom: "Fabtrack"
       - type: "fabtrack"
       - url: "http://localhost:5555"
       - sync_interval_sec: 60
       - actif: 1

Polling Cycle (Phase 3) [Every 10s]
    │
    ├─ Query: SELECT * FROM sources WHERE actif=1
    │
    ├─ For Fabtrack:
    │   ├─ Check: now >= last_sync + 60s?
    │   │
    │   └─ If yes:
    │       ├─ GET http://localhost:5555/api/stats/summary
    │       ├─ GET http://localhost:5555/api/consommations?per_page=10
    │       ├─ GET http://localhost:5555/api/reference
    │       │
    │       ├─ Parse responses → aggregate payload
    │       │
    │       └─ INSERT sources_cache (
    │           source_id=1,
    │           data_json='{"summary": {...}, ...}',
    │           expires_at=now+60s
    │       )

Widget Rendering (Phase 2 Enhancement)
    │
    ├─ GET /api/widgets/fabtrack_stats/render
    │   ├─ Fetch source_id from slide_widgets
    │   │
    │   └─ Call get_cached_source_data(source_id)
    │       └─ Query sources_cache for latest data
    │
    └─ Return HTML with cached data (no live API calls!)
```

---

## 🛠️ Configuration

### Sync Interval Tuning

Chaque source a un `sync_interval_sec` configurable (10-3600 secondes):

```
Fast sources (10-30s):
  - Stockage dynamique (imprimantes, machines)
  - Métriques temps réel

Medium sources (60-300s):
  - Fabtrack stats
  - Consommations
  - Calendrier

Slow sources (600-3600s):
  - RSS feeds
  - Meteo (changement lent)
```

### Environment Variables

```bash
FABBOARD_PORT=5580          # Port Flask (already supported)
FABTRACK_URL=...            # Auto-resolved from sources DB
FLASK_ENV=production        # Disable debug (important!)
FLASK_DEBUG=0               # Disable reloader
```

---

## 🐛 Debugging

### Vérifier l'état du worker

```bash
curl http://localhost:5580/api/worker/status | jq .
```

Attendez-vous à voir:
- ✅ `worker_running: true`
- ✅ `sources` avec `cache_valid: true` (après ~10 secondes)
- ✅ `derniere_sync` mis à jour régulièrement

### Vérifier les données en cache

```bash
curl http://localhost:5580/api/cache/3 | jq .data
```

Cela retourne le dernier payload synchronisé pour source_id=3

### Nettoyage manuel

```bash
curl -X DELETE http://localhost:5580/api/cache
```

### Logs (Terminal)

```
[SyncWorker] Sync worker démarré
[SyncWorker] Erreur sync source 3: Connection refused
[Cache] Erreur lecture cache source 3: ...
```

---

## 📈 Performance Impact

### Avant Phase 3 (Polling direct)

```
Widget render (HTTP GET):
├─ GET /api/stats/summary      [4ms + network]
├─ GET /api/consommations      [4ms + network]
└─ GET /api/reference          [4ms + network]
Total: ~12-30ms per refresh
Requests/min: 6 (1 refresh per 10s) × N sources
```

### Après Phase 3 (Cache)

```
Widget render (JSON deserialization):
├─ Query sources_cache         [~1ms]
└─ Parse JSON                  [~1ms]
Total: ~2ms per refresh
Requests/min: Same 6 (polling) but DB-cached!
```

**Reduction**: ~90% latency, stable load on backend

---

## 🔄 Thread Safety

### SQLite WAL Mode

```python
PRAGMA journal_mode=WAL      # Write-Ahead Logging
PRAGMA foreign_keys=ON       # Enforce relationships
```

✅ Permet lectures/écritures concurrentes  
✅ Transactions ACID  
✅ No locking conflicts

### DB Connections

Chaque thread obtient sa propre connection via `get_db()`:

```python
# sync_worker.py
db = get_db()              # New connection for worker thread
# ... operations ...
db.close()                 # Release connection

# Flask request
db = get_db()              # New connection for HTTP request
# ... operations ...
db.close()                 # Connection pooling via Flask context
```

### Global _worker Object

```python
# Thread 1 (Flask request)
worker = get_sync_worker()  # Returns daemon thread reference
# Read-only access: worker.running, worker.poll_interval

# Thread 2 (Daemon)
# Only modifies: self.running = False (on stop)
# Write lock: implicit via Python GIL
```

---

## 📝 Logs Exemple

### Démarrage réussi

```
[FabBoard] Démarrage sur http://localhost:5580
[SyncWorker] Sync worker démarré
[FabBoard] GET / 200 OK
[FabBoard] DB initialized, Fabtrack source created
```

### Premier polling cycle (après ~10s)

```
[SyncWorker] Syncing source 1 (Fabtrack)
    ├─ Fetching: GET http://localhost:5555/api/stats/summary
    ├─ Fetching: GET http://localhost:5555/api/consommations?per_page=10&page=1
    ├─ Fetching: GET http://localhost:5555/api/reference
    ├─ Parsing: 42 interventions, 1.2kg 3D, 2.5m² cutting
    └─ Cached: sources_cache[source_id=1] expires in 60s
```

### Deuxième polling cycle (toujours valide, skip)

```
[SyncWorker] Source 1: Cache still valid (19s remaining), skipping
[SyncWorker] Sleeping 10s...
```

### Troisième polling cycle (doit refresh)

```
[SyncWorker] Source 1: Cache expired (60s exceeded), syncing...
    └─ Cached: sources_cache[source_id=1] expires in 60s
```

### Erreur de connexion

```
[SyncWorker] Erreur sync source 1: HTTPError: 503 Service Unavailable
     └─ Stored in: sources.derniere_erreur
     └─ Next retry in 60s
     └─ Cache remains: valid for 19s remaining
```

---

## 🎯 Next Steps (Phase 4+)

### Améliorations futures

1. **Dashboard auto-refresh optimisé** (Phase 4)
   - `data-sync-interval` per widget
   - Fewer HTTP calls to `/api/dashboard/data`
   - Direct cache reads

2. **Metrics & Monitoring** (Phase 4)
   - Worker stats: uptime, cycles, errors/success
   - Cache hit ratio
   - Response times

3. **Batch operations** (Phase 5)
   - Sync multiple sources in parallel
   - ThreadPoolExecutor for Fabtrack + Calendrier + Printers

4. **Persistent queue** (Phase 5)
   - If source temporarily unavailable
   - Retry logic with exponential backoff
   - Priority queue for critical sources

5. **UI Dashboard** (Phase 6)
   - `/moniteur/worker` page
   - Real-time source status
   - Manual sync buttons
   - Cache invalidation UI

---

## ✅ Checklist de clôture Phase 3

- [x] `sync_worker.py` créé avec classe `SyncWorker`
- [x] `sources_cache` table ajoutée à `init_db()`
- [x] Worker démarrage dans `@app.before_request`
- [x] Worker arrêt dans `@app.teardown_appcontext`
- [x] `get_cached_source_data()` helper implémenté
- [x] `/api/worker/status` GET endpoint
- [x] `/api/cache/<source_id>` GET endpoint
- [x] `/api/cache` DELETE endpoint (cleanup)
- [x] `/api/cache/<source_id>/refresh` POST endpoint
- [x] Support Fabtrack, OpenWeatherMap, HTTP générique
- [x] Support CalDAV, Repetier, PrusaLink (stubs)
- [x] Thread safety avec WAL mode
- [x] Graceful shutdown handling
- [x] Documentation complète

---

**Phase 3 COMPLÈTE** ✅  
**Prochaine étape**: Phase 4 - Dashboard auto-refresh avec widgets temps réel
