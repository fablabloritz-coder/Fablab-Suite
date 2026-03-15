# MEMORY - FabLab Suite (Monorepo)

## 1) Purpose of this file
This file is the single quick context handover for any new AI/session.
If something conflicts with old notes, trust this file + current code in repository.

## 2) Project identity
- Project name: FabLab Suite
- Main repo (single source of truth): https://github.com/fablabloritz-coder/Fablab-Suite.git
- Stack: Flask + SQLite + Bootstrap 5 + Vanilla JS
- Deployment model:
  - Local dev: root docker compose
  - Ubuntu server: helper script + SSH GUI, monorepo only

## 3) Applications in scope
- FabHome: central hub, dashboard, suite registry, notifications
- Fabtrack: machines, consumptions, stock, missions, Raise3D integration
- PretGo: equipment loans
- FabBoard: display dashboard (TV mode), reads Fabtrack data

Out of core suite:
- FabStock: legacy/side app (features moved to Fabtrack stock module)
- FrigoScan: personal project, not part of suite

## 4) Ports and URLs
Default host ports:
- FabHome: 3001 (container 3000)
- Fabtrack: 5555
- PretGo: 5000
- FabBoard: 5580

Important URL rules:
- Local root compose: FabBoard -> Fabtrack uses service DNS (`http://fabtrack:5555`)
- Ubuntu helper env defaults for inter-app are container-safe host gateway URLs:
  - FABTRACK_URL = http://host.docker.internal:5555
  - PRETGO_URL = http://host.docker.internal:5000
  - FABBOARD_URL = http://host.docker.internal:5580
- FabHome registered app URLs (browser links) should use server network host/IP, not localhost.
- Server auto-registration now supports `FABHOME_REGISTER_HOST` (and optional `FABHOME_REGISTER_SCHEME`).
- Legacy hostname URLs can fail from containers; helper now normalizes/migrates these.

## 5) Monorepo policy (critical)
- Install/update are monorepo-only.
- Legacy per-app update mode is removed.
- Required server shape:
  - INSTALL_DIR must be monorepo checkout (git root)
- If legacy layout is detected (`~/fabsuite` style), `repair-env` auto-prepares migration:
  - switches INSTALL_DIR to `~/fablab-suite`
  - keeps existing data paths through `*_DATA_PATH` vars

## 6) Canonical operational entry points
Primary automation files:
- `run_fabsuite_ssh.ps1` (Windows one-liner launcher, recommended entry)
- `fabsuite-ubuntu.sh` (install/update/audit/safety)
- `fabsuite_ssh_gui.py` (one-click SSH orchestration)
- `fabsuite-ubuntu.env.example` (server config template)
- `INSTALL_UBUNTU.md` (server runbook)
- `README.md` (project overview)

## 7) Server install/update workflow
CLI flow:
1. `./fabsuite-ubuntu.sh prepare-host`
2. `./fabsuite-ubuntu.sh repair-env`
3. `./fabsuite-ubuntu.sh check-data-safety`
4. `./fabsuite-ubuntu.sh install`

Daily update:
1. `./fabsuite-ubuntu.sh repair-env`
2. `./fabsuite-ubuntu.sh check-data-safety`
3. `./fabsuite-ubuntu.sh update`
4. `./fabsuite-ubuntu.sh audit`

GUI flow (recommended):
1. Start GUI from Windows one-liner launcher:
  - `powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression (Invoke-RestMethod 'https://raw.githubusercontent.com/fablabloritz-coder/Fablab-Suite/main/run_fabsuite_ssh.ps1')"`
2. In tab `Deploiement Serveur`: Connect SSH
3. 1) Envoyer les fichiers installateur
4. 2) Audit serveur
5. 4) Preparer l hote Ubuntu
6. 5) Reparer env monorepo
7. 6) Pre-check securite donnees
8. 7) Installer la suite (first time) OR 7b) Mettre a jour la suite
9. 8) Verifier l etat

Notes:
- GUI Install/Update already run repair-env + data safety precheck automatically.
- If data safety detects path-switch risk, operation must stop.

## 8) Data persistence and safety
Main bind mounts (server):
- FabHome:
  - FABHOME_DATA_PATH -> /app/data
  - FABHOME_ICONS_PATH -> /app/static/icons
- Fabtrack:
  - FABTRACK_DATA_PATH -> /app/data
  - FABTRACK_UPLOADS_PATH -> /app/static/uploads
- PretGo:
  - PRETGO_DATA_PATH -> /app/data
  - PRETGO_UPLOADS_PATH -> /app/static/uploads/materiel
- FabBoard:
  - FABBOARD_DATA_PATH -> /app/data

Safety mechanism:
- `check-data-safety` compares current bind sources vs expected new paths
- If mismatch/risk: block install/update

## 9) Inter-app auto-configuration status
Current behavior:
- Helper auto-registers Fabtrack/PretGo/FabBoard in FabHome
- Registration health checks are done via localhost on server (`http://localhost:<port>/api/fabsuite/health`)
- Registered URLs for FabHome can be forced to browser/network-friendly host via `FABHOME_REGISTER_HOST`
- If `FABHOME_REGISTER_HOST` is missing, helper tries auto-detect (route src IP, then `hostname -I`)
- SSH GUI injects `FABHOME_REGISTER_HOST` from active SSH peer host/IP to avoid localhost links in FabHome
- Existing app entries in FabHome can be replaced when URL changed (by app_id)
- Registration logging is explicit (HTTP code + payload)
- If app health endpoint not ready: registration deferred with warning

FabBoard source bootstrap:
- Auto-creates/updates Fabtrack source URL
- If FABTRACK_URL env is explicitly provided, existing source URL is synchronized

## 10) FabHome customization status
Implemented:
- Custom background color per group
- Custom background color per grid widget
- Render keeps semi-transparent visual style (tint mixed with card background)

Technical note:
- Uses CSS variables with fallback and `color-mix` tinting

## 11) FabSuite API contract
Each app exposes:
- `GET /api/fabsuite/manifest`
- `GET /api/fabsuite/health`
- Optional widgets/notifications endpoints from manifest

FabHome suite endpoints:
- `/api/suite/apps` (list/register)
- `/api/suite/apps/<id>` (delete)
- `/api/suite/apps/refresh`
- `/api/suite/notifications`

## 12) Project conventions (must keep)
- No React/Vue; use vanilla JS + Bootstrap
- DB schema naming remains French (snake_case)
- API keys/routes naming remains English (snake_case/kebab-case)
- `fabsuite_core` is vendored/copy model (not pip package)
- Per-app visual theme allowed; no forced global theme

## 13) Known traps / troubleshooting
- Docker not running on Windows -> compose commands fail early.
- CRLF env/script uploads can break shell sourcing; helper normalizes CRLF -> LF.
- Placeholder repo owner in env can break sourcing/clone; helper normalizes placeholders.
- Hostname-based inter-app URLs can fail from containers; prefer host.docker.internal defaults.
- If FabBoard worker diagnostics are KO/slow, check deployed FabBoard version and source URL.

## 14) Quick commands
Recommended Windows entry (install/update via GUI):
- `powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression (Invoke-RestMethod 'https://raw.githubusercontent.com/fablabloritz-coder/Fablab-Suite/main/run_fabsuite_ssh.ps1')"`

Local full stack:
- `cp .env.example .env`
- `docker compose up -d --build`

Server maintenance:
- `./fabsuite-ubuntu.sh status`
- `./fabsuite-ubuntu.sh audit`
- `./fabsuite-ubuntu.sh logs`
- `./fabsuite-ubuntu.sh logs Fabtrack`

## 15) Current baseline snapshot
Recent commits (latest first):
- `eff4048` docs(readme): promote single recommended installer command
- `257b96e` feat(installer): one-line Windows launcher and robust server app registration
- `7827937` feat(gui): separer UX actions communes et SSH-only
- `6b27449` feat(gui): ajouter mode local et unifier workflows deploy_core
- `9734009` feat(control-center): migrer audit GUI vers deploy_core
- `e7e0fb4` feat(control-center): brancher status GUI sur deploy_core
- `041eae6` feat(control-center): init memory and unified workflow scaffold
- `27d448d` fix(installer): centraliser auto-config inter-app et transparence UI

## 16) If you are a new AI entering this repo
Start here:
1. Read this file
2. Read `README.md`
3. Read `INSTALL_UBUNTU.md`
4. Read `fabsuite-ubuntu.sh` and `fabsuite_ssh_gui.py`
5. Run `git log --oneline -n 10`

Then act with this assumption:
- One monorepo, one update path, one clean orchestrated workflow.
