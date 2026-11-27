# MEMORY - FabInventory

> **Mise à jour 2026-11-26 (consolidation).** Le schéma ajoute
> `snapshots.scan_date_iso`, `snapshots.content_hash` et
> `masters.pc_name_key` ; les vues `snapshot_keys` et `latest_snapshots`
> définissent le dernier scan par date de scan (ISO), avec `id` comme
> tie-breaker. Les seeds admin/pack ne s'exécutent qu'au premier démarrage
> (`app_meta.initial_seed`), les clés étrangères sont actives, SQLite est en
> WAL, et `/healthz` teste la base. Toutes les écritures exigent une session
> admin (portail professeur et comparaison exceptés) ; la clé de session est
> aléatoire et persistée, et le compte admin n'a plus de mot de passe par
> défaut. Les suppressions de masters et de scans passent par une corbeille
> restaurable (`deleted_items`, `/admin/trash`). Suite de référence :
> `python tests/run_all.py` (21 modules, 117 tests). Les
> scans conservent aussi `warnings_json`, `script_version`, `scan_schema` et
> `generated_at` ; `/master/<id>/evolution`, `/sante` et `/reseau` exploitent
> ces données. Les absences de logiciels sont suivies dans
> `software_followups` (statut `pending` par défaut, résolution automatique
> au retour du logiciel). Le Lot 6 (script PowerShell unique) reste en attente
> d'un test Windows.

## 1) Purpose of this file

This file is the single quick context handover for any new AI/session working on FabInventory.
If something conflicts with old notes, trust this file + current code in repository.

## 2) Project identity

- Project name: FabInventory
- Parent project: [Fablab-Suite](https://github.com/fablabloritz-coder/Fablab-Suite)
- Stack: Flask + SQLite + Bootstrap 5 + Vanilla JS (same as Fablab-Suite)
- Deployment model: Docker Compose (standalone or integrated in Fablab-Suite)
- Language: French UI, English code/API naming (same convention as Fablab-Suite)

## 3) What this app does

FabInventory is a web application for managing FOG master image inventories in a school IT environment (~500 PCs).

Core workflow:
1. A PowerShell script (v3) runs on each Windows master PC before FOG capture
2. The script generates an HTML file containing a `<script id="inventoryData" type="application/json">` block with all software, system info, disks, network data
3. Users upload these HTML files to FabInventory via drag-and-drop
4. FabInventory parses the embedded JSON, stores data in SQLite, and provides a web UI to browse, search, flag, annotate, compare

## 4) Data model

### Tables

```
masters
├── id (PK)
├── pc_name (TEXT, unique identifier from scan)
├── label (TEXT, user-friendly name e.g. "Salle Info 1 - SolidWorks")
├── notes (TEXT, free-form notes)
└── created_at (TIMESTAMP)

snapshots
├── id (PK)
├── master_id (FK -> masters.id, CASCADE)
├── scan_date (TEXT, from HTML report)
├── os_info, cpu_info, ram_go, fabricant, num_serie, domaine
├── software_json (TEXT, JSON array of software objects)
├── disks_json, network_json, services_json (TEXT, JSON arrays)
├── warnings_json (TEXT, avertissements de collecte)
├── script_version, scan_schema, generated_at (métadonnées du scan)
├── total_software (INTEGER)
└── created_at (TIMESTAMP)

software_flags
├── id (PK)
├── master_id (FK -> masters.id, CASCADE)
├── software_name (TEXT)
├── is_important (INTEGER 0/1)
├── note (TEXT)
└── UNIQUE(master_id, software_name)

software_followups
├── master_id (FK -> masters.id, CASCADE)
├── software_key, software_name
├── old_version, old_editor, old_source
├── source_snapshot_id, last_seen_snapshot_id/date
├── last_missing_snapshot_id/date
├── status : pending | intentional | to_install | resolved
├── note, decided_at
└── resolved_snapshot_id, resolved_at

deleted_items (corbeille)
├── id (PK)
├── kind : master | snapshot
├── label, payload_json (lignes archivees avec leurs ids d'origine)
├── deleted_at
└── restored_at (NULL tant que la restauration est possible)
```

### Software JSON object structure (inside snapshots.software_json)

```json
{"n": "Firefox", "v": "128.0", "e": "Mozilla", "d": "15/03/2026", "s": 245.3, "src": "Registre"}
```

Fields: `n`=name, `v`=version, `e`=editor/publisher, `d`=install date, `s`=size in MB, `src`=source (Registre|Utilisateur|AppX|Dossier)

## 5) Port and URL

- Default port: **5590** (container and host)
- URL: `http://IP:5590`
- Chose 5590 to avoid conflict with other Fablab-Suite apps (3001, 5000, 5555, 5580)

## 6) File structure

```
FabInventory/
├── app.py                      # Flask app (routes, parser, DB init, API)
├── templates/
│   ├── base.html               # Layout: navbar, Bootstrap 5, flash messages
│   ├── index.html              # Dashboard: master cards with stats
│   ├── upload.html             # Drag-and-drop HTML file upload
│   ├── master.html             # Master detail: software table, flags, notes, history
│   ├── snapshot.html           # Single scan detail (read-only)
│   └── compare.html            # Side-by-side master comparison
├── static/css/style.css        # Custom styles
├── Dockerfile                  # Python 3.11 slim + gunicorn
├── docker-compose.yml          # Standalone deployment
├── requirements.txt            # flask, gunicorn
├── README.md                   # User-facing docs
├── MEMORY.md                   # This file
└── AGENTS.md                   # Workflow rules
```

## 7) Key routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Dashboard - list all masters |
| `/upload` | GET/POST | Upload HTML inventory files |
| `/master/<id>` | GET | Master detail with software table |
| `/master/<id>/edit` | POST | Update label/notes |
| `/master/<id>/delete` | POST | Archive then delete master + all snapshots |
| `/snapshot/<id>` | GET | View a specific scan |
| `/snapshot/<id>/delete` | POST | Archive then delete a scan |
| `/admin/trash` | GET | Corbeille : masters et scans restaurables |
| `/admin/trash/<id>/restore` | POST | Restaure un master ou un scan archive |
| `/master/<id>/absences` | GET/POST | Follow-up list for missing software and decisions |
| `/master/<id>/evolution` | GET | Compare two scans of the same master |
| `/sante` | GET | Fleet health: stale scans, low disks, warnings, duplicates |
| `/reseau` | GET | Registry of last known IP/MAC per interface |
| `/compare` | GET | Comparison page (select 2 masters) |
| `/api/compare` | POST | JSON comparison endpoint |
| `/api/flag` | POST | Set/update software flag (important + note) |
| `/api/fabsuite/status` | GET | FabSuite inter-app status endpoint |

## 8) HTML parser logic

The parser (`parse_inventory_html()`) looks for:
```html
<script id="inventoryData" type="application/json">{"pcName":"...","date":"...","software":[...]}</script>
```

This JSON block is generated by the PowerShell script v3. If the block is missing, the file is rejected with a warning.

System info (OS, CPU, RAM, etc.) is extracted separately via regex from the HTML body because it's not included in the JSON block.

### Important: backward compatibility

If the PowerShell script changes, ensure the `inventoryData` JSON block structure is preserved. The parser depends on:
- `pcName` (string): used as master identifier
- `date` (string): scan timestamp
- `software` (array of objects with keys `n`, `v`, `e`, `d`, `s`, `src`)

## 9) Frontend behavior

### Master detail page (master.html)

- Software table rendered client-side from JSON (`SW` variable injected by Jinja)
- Flags stored server-side in SQLite (not localStorage) via `/api/flag` fetch calls
- Sorting: click column headers, toggles asc/desc
- Filtering: search box + "importants uniquement" toggle
- Notes: inline text inputs, saved on change via `/api/flag`

### Compare page (compare.html)

- Two dropdown selectors (latest snapshot of each master)
- Calls `/api/compare` POST with snapshot IDs
- Results: common (blue), only in A (green), only in B (red)
- Client-side search filter on results

### Upload page (upload.html)

- Drag-and-drop zone + click to browse
- Accepts multiple `.html` files
- Server-side parsing and import on form submit

## 10) FabSuite integration

FabInventory follows the FabSuite inter-app contract:

- Exposes `GET /api/fabsuite/status` returning:
  ```json
  {"app": "FabInventory", "status": "ok", "masters": 13, "snapshots": 27}
  ```
- Can be registered in FabHome via its app registry
- Does NOT currently expose `/api/fabsuite/manifest` or `/api/fabsuite/health` (to be added if integrated into main Fablab-Suite)

### To integrate into Fablab-Suite monorepo:

1. Copy `FabInventory/` folder into monorepo root
2. Add service to root `docker-compose.yml`:
   ```yaml
   fabinventory:
     build: ./FabInventory
     container_name: fabinventory
     ports:
       - "5590:5590"
     volumes:
       - fabinventory-data:/data
     restart: unless-stopped
   ```
3. Add to `fabsuite-ubuntu.sh` service list if needed
4. Register in FabHome via API or helper script

## 11) Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5590` | App port |
| `DB_PATH` | `/data/fabinventory.db` | SQLite database path |
| `UPLOAD_FOLDER` | `/data/uploads` | Upload directory |
| `SECRET_KEY` | générée | Clé de session Flask ; si absente ou historiquement connue, une clé aléatoire est générée et persistée dans `app_meta`. Le compose exige `FLASK_SECRET_KEY`. |
| `ADMIN_PASSWORD` | généré | Mot de passe initial du compte `admin` ; sinon mot de passe aléatoire écrit une fois dans `admin-initial-password.txt` (0600) à côté de la base. |
| `DEBUG` | `0` | Set to `1` for Flask debug mode |

## 12) Data persistence

- SQLite database: `/data/fabinventory.db` (inside Docker volume `fabinventory-data`) ; WAL et `synchronous=NORMAL` activés par `init_db()`
- Upload folder: `/data/uploads` (same volume)
- All data survives container restarts/rebuilds via the named volume

## 13) PowerShell script companion (v3.1)

La source unique est `static/downloads/inventaire_master.ps1`, servie par
`/download/master-script` et incluse dans le pack ZIP. `usb_v3/` est
l'ancien emplacement de distribution, pas la source servie.

- Compatible Windows PowerShell 5.1 et PowerShell 7+ ; le lanceur `.bat` force `-STA` pour la fenêtre de sauvegarde
- Collecte : registre machine et utilisateur, AppX, dossiers Program Files, disques, réseau, services automatiques, système
- JSON `schema: 3` sérialisé par `ConvertTo-Json`, avec `warnings` et `services` ; les `<` sont échappés en `\u003c` pour ne jamais fermer le bloc `<script>`
- Repli de l'application sur l'ancien format HTML (labels OS/CPU/RAM et tableaux Disques/Réseau) quand le JSON enrichi est absent
- Sorties : HTML autonome + CSV ; options `-OutputPath`, `-OutputDirectory`, `-NoGui`, `-SelfTest`, `-SkipAppx`, `-SkipFolderScan`, `-SkipServices`
- Les erreurs WMI/AppX/dossier sont non bloquantes et visibles dans le rapport ; un échec fatal écrit un journal dans `%TEMP%`
- Le test Windows manuel reste obligatoire avant diffusion (le CI Linux ne peut pas exécuter WMI/AppX)

## 14) Project conventions (must keep)

- Same stack as Fablab-Suite: Flask + SQLite + Bootstrap 5 + vanilla JS
- No React/Vue/npm build step
- DB schema naming: French (snake_case)
- API routes naming: English (snake_case/kebab-case)
- UI language: French
- Code comments: French or English (flexible)
- All templates extend `base.html`
- Bootstrap Icons for iconography (loaded via CDN)
- Responsive design (works on mobile/tablet for quick lookup)

## 15) Known limitations / future improvements

- Toutes les écritures exigent une session admin (`before_request`) ; seuls le portail professeur (token dédié) et `/api/compare` (lecture seule) restent accessibles sans session.
- Les suppressions de masters et de scans sont archivees dans `deleted_items` avant effacement ; la restauration depuis `/admin/trash` réinsère les lignes avec leurs ids d'origine (AUTOINCREMENT ne les réattribue pas). Aucune purge automatique : la corbeille conserve les archives restaurées comme journal.
- No automatic PowerShell script version detection.
- Le JSON schema 3 fournit le système, les disques, le réseau et les services ; le parsing regex/HTML ne sert plus que de repli pour les anciens rapports.
- Le script PowerShell doit encore être validé sur un poste Windows réel (WMI, AppX, fenêtre de sauvegarde) ; l'autotest Linux vérifie le parseur et le JSON mais pas la collecte Windows.
- No export functionality (PDF/Excel of flagged software).
- L'évolution d'un même master est disponible dans `/master/<id>/evolution` (sélection de deux scans, sans nouvelle collecte).
- Les absences de logiciels sont créées avec le statut `pending` (À vérifier) et suivies dans `/master/<id>/absences` ; `intentional` et `to_install` sont des décisions, et un retour du logiciel résout automatiquement le suivi.
- Une absence peut être un faux positif si le scan est partiel : les avertissements sont affichés sur l'écran de confirmation et le statut par défaut reste `pending`.
- `/sante` et `/reseau` agrègent uniquement le dernier scan de chaque poste ; ce ne sont pas des vues temps réel.
- Seuils d'alerte disque fixes : moins de 10 Go libres ou moins de 15 % d'espace libre.
- Les services ne sont collectés que s'ils sont `Running` et en démarrage automatique ; un service absent peut être arrêté ou désinstallé.
- `/api/fabsuite/manifest`, `/api/fabsuite/health` et `/healthz` sont présents ; `healthz` teste la base.

## 16) If you are a new AI entering this project

Start here:
1. Read this file (MEMORY.md)
2. Read `README.md`
3. Read `app.py` (single-file Flask app, ~3800 lines)
4. Read `templates/master.html` (most complex template, has JS logic)
5. Understand the data flow: PowerShell script -> HTML file -> upload -> parse JSON -> SQLite -> web UI

Then act with this assumption:
- Keep it simple, keep it Flask+SQLite, keep it compatible with Fablab-Suite conventions.
- The user (Steven) manages IT for a technical high school with ~500 PCs and is not a developer. Code must be straightforward and well-commented.
