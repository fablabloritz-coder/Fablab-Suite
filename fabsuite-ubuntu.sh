#!/usr/bin/env bash
set -euo pipefail

# Self-heal: if uploaded from Windows with CRLF, fix and re-exec.
if LC_ALL=C grep -q $'\r' "${BASH_SOURCE[0]}" 2>/dev/null; then
  sed -i 's/\r$//' "${BASH_SOURCE[0]}"
  exec bash "${BASH_SOURCE[0]}" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/fabsuite-ubuntu.env"
ACTION=""
LOG_APP=""
DEFAULT_MONOREPO_URL="https://github.com/fablabloritz-coder/Fablab-Suite.git"

usage() {
  cat <<'EOF'
FabLab Suite - Ubuntu helper (monorepo + compose individuel par app)

Usage:
  ./fabsuite-ubuntu.sh prepare-host
  ./fabsuite-ubuntu.sh audit
  ./fabsuite-ubuntu.sh cleanup-safe
  ./fabsuite-ubuntu.sh repair-env [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh check-data-safety [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh install [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh bootstrap [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh update [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh start [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh stop [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh restart [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh status [--env-file /path/to/fabsuite-ubuntu.env]
  ./fabsuite-ubuntu.sh logs [AppName] [--env-file /path/to/fabsuite-ubuntu.env]

Examples:
  ./fabsuite-ubuntu.sh prepare-host
  ./fabsuite-ubuntu.sh audit
  ./fabsuite-ubuntu.sh cleanup-safe
  ./fabsuite-ubuntu.sh repair-env
  ./fabsuite-ubuntu.sh check-data-safety
  ./fabsuite-ubuntu.sh install
  ./fabsuite-ubuntu.sh status
  ./fabsuite-ubuntu.sh logs Fabtrack
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    prepare-host|audit|cleanup-safe|repair-env|check-data-safety|install|bootstrap|update|start|stop|restart|status|logs|help)
      ACTION="$1"
      shift
      ;;
    --env-file)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --env-file requires a value"
        exit 1
      fi
      ENV_FILE="$2"
      shift 2
      ;;
    -h|--help)
      ACTION="help"
      shift
      ;;
    *)
      if [[ -z "$LOG_APP" ]]; then
        LOG_APP="$1"
      else
        echo "ERROR: unknown argument '$1'"
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  ACTION="help"
fi

if [[ "$ACTION" == "help" ]]; then
  usage
  exit 0
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: command not found: $cmd"
    exit 1
  fi
}

require_docker_compose() {
  require_cmd docker
  if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: docker compose plugin is not available"
    exit 1
  fi
}

run_privileged() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
    return
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: sudo not found and current user is not root"
    exit 1
  fi

  if [[ -n "${SUDO_PASSWORD:-}" ]]; then
    # For non-interactive SSH execution (GUI/automation), provide sudo password through env var.
    printf '%s\n' "$SUDO_PASSWORD" | sudo -S -p '' "$@"
  elif [[ "${NON_INTERACTIVE:-0}" == "1" ]]; then
    if ! sudo -n "$@"; then
      echo "ERROR: sudo privileges are required for this action."
      echo "Tip: fill 'Sudo password' in the GUI advanced section."
      return 1
    fi
  else
    sudo "$@"
  fi
}

run_audit() {
  require_docker_compose

  local audit_fabhome_port=3001
  local audit_fabtrack_port=5555
  local audit_pretgo_port=5000
  local audit_fabboard_port=5580
  local audit_fabtrack_url=""
  local audit_fabtrack_url_host=""

  # Charger la config si disponible (audit n'exige pas l'env, mais l'utilise si présent).
  if [[ -f "$ENV_FILE" ]]; then
    normalize_env_file "$ENV_FILE" || true
    if source "$ENV_FILE" >/dev/null 2>&1; then
      audit_fabhome_port="${FABHOME_PORT:-$audit_fabhome_port}"
      audit_fabtrack_port="${FABTRACK_PORT:-$audit_fabtrack_port}"
      audit_pretgo_port="${PRETGO_PORT:-$audit_pretgo_port}"
      audit_fabboard_port="${FABBOARD_PORT:-$audit_fabboard_port}"
      audit_fabtrack_url="${FABTRACK_URL:-}"
    else
      echo "[audit] Warning: impossible de charger $ENV_FILE, valeurs par défaut utilisées"
    fi
  fi

  local audit_host
  audit_host="$(hostname)"
  if [[ -z "$audit_fabtrack_url" ]]; then
    audit_fabtrack_url="http://${audit_host}:${audit_fabtrack_port}"
  fi
  # IMPORTANT: depuis l'hôte, host.docker.internal n'est pas garanti.
  # Pour les tests host->service, utiliser 127.0.0.1:port pour éviter les faux KO.
  audit_fabtrack_url_host="http://127.0.0.1:${audit_fabtrack_port}"

  _check_host_health() {
    local label="$1"
    local base_url="$2"
    if curl -fsS --max-time 4 "${base_url%/}/api/fabsuite/health" >/dev/null 2>&1 || \
       curl -fsS --max-time 4 "${base_url%/}/api/health" >/dev/null 2>&1; then
      echo "[OK] Host -> ${label} health"
    else
      echo "[KO] Host -> ${label} health"
    fi
  }

  echo "===== Docker containers ====="
  docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

  echo
  echo "===== Docker compose projects ====="
  docker compose ls || true

  echo
  echo "===== FabSuite ports ====="
  if command -v ss >/dev/null 2>&1; then
    # Use sudo for full process info (non-root can't see other users' PIDs).
    run_privileged ss -ltnp 2>/dev/null | grep -E ':3001|:5555|:5000|:5580' || \
    ss -ltn 2>/dev/null | grep -E ':3001|:5555|:5000|:5580' || true
  elif command -v netstat >/dev/null 2>&1; then
    run_privileged netstat -lntp 2>/dev/null | grep -E ':3001|:5555|:5000|:5580' || \
    netstat -lnt 2>/dev/null | grep -E ':3001|:5555|:5000|:5580' || true
  else
    echo "Neither ss nor netstat is available"
  fi

  echo
  echo "===== Inter-app connectivity (host) ====="
  if command -v curl >/dev/null 2>&1; then
    _check_host_health "FabHome" "http://127.0.0.1:${audit_fabhome_port}"
    _check_host_health "Fabtrack" "${audit_fabtrack_url_host%/}"
    _check_host_health "PretGo" "http://127.0.0.1:${audit_pretgo_port}"
    _check_host_health "FabBoard" "http://127.0.0.1:${audit_fabboard_port}"

    if curl -fsS --max-time 4 "${audit_fabtrack_url_host%/}/missions/api/list" >/dev/null 2>&1; then
      echo "[OK] Host -> Fabtrack missions (${audit_fabtrack_url_host%/}/missions/api/list)"
    else
      echo "[KO] Host -> Fabtrack missions (${audit_fabtrack_url_host%/}/missions/api/list)"
    fi
  else
    echo "[INFO] curl absent: tests HTTP host ignorés"
  fi

  echo
  echo "===== FabBoard sync diagnostics ====="
  if command -v curl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    local _worker_url _worker_tmp _worker_http _worker_time _worker_json
    _worker_url="http://127.0.0.1:${audit_fabboard_port}/api/worker/status"
    _worker_tmp="$(mktemp)"
    read -r _worker_http _worker_time <<< "$(curl -sS --max-time 12 -o "$_worker_tmp" -w '%{http_code} %{time_total}' "$_worker_url" 2>/dev/null || echo '000 0')"

    if [[ "$_worker_http" == "200" ]]; then
      _worker_json="$(cat "$_worker_tmp")"
      python3 - "$_worker_json" <<'PY'
import json
import sys

try:
    raw = sys.argv[1] if len(sys.argv) > 1 else ''
    data = json.loads(raw)
except Exception as exc:
    print(f"[KO] Réponse worker/status invalide: {exc}")
    sys.exit(0)

if not data.get('success'):
    print(f"[KO] worker/status en erreur: {data.get('error', 'inconnue')}")
    sys.exit(0)

print(f"[INFO] Worker running: {bool(data.get('worker_running'))}")

sources = data.get('sources') or []
fabtrack = None
for s in sources:
    if str(s.get('type', '')).lower() == 'fabtrack':
        fabtrack = s
        break

if not fabtrack:
    print('[WARN] Aucune source Fabtrack trouvée dans FabBoard')
    sys.exit(0)

print(
    "[INFO] Source Fabtrack: "
    f"actif={fabtrack.get('actif')} "
    f"interval={fabtrack.get('sync_interval_sec')}s "
    f"derniere_sync={fabtrack.get('derniere_sync') or '-'} "
    f"cache_valid={fabtrack.get('cache_valid')}"
)

err = (fabtrack.get('derniere_erreur') or '').strip()
if err:
    print(f"[KO] Dernière erreur source Fabtrack: {err}")
PY
    python3 - "${_worker_time:-0}" <<'PY'
import sys
try:
  t = float(sys.argv[1])
except Exception:
  t = 0.0
if t > 4.0:
  if t > 6.0:
    print(f"[WARN] /api/worker/status lent ({t:.2f}s). Possible blocage sync/worker à investiguer.")
  else:
    print(f"[INFO] /api/worker/status un peu lent ({t:.2f}s), mais sans alerte critique.")
PY
    else
      echo "[KO] Impossible d'interroger /api/worker/status sur FabBoard (HTTP ${_worker_http})"
      if [[ -s "$_worker_tmp" ]]; then
        echo "[INFO] Réponse: $(head -c 220 "$_worker_tmp" | tr '\n' ' ')"
      fi

      if docker inspect -f '{{.State.Running}}' fabboard 2>/dev/null | grep -q true; then
        if docker exec -i fabboard python - <<'PY'
import urllib.request
import sys

url = 'http://localhost:5580/api/worker/status'
try:
  with urllib.request.urlopen(url, timeout=10) as resp:
    code = getattr(resp, 'status', 200)
    print(f"[INFO] FabBoard interne -> worker/status OK (HTTP {code})")
except Exception as exc:
    print(f"[KO] FabBoard interne -> worker/status: {exc}")
    sys.exit(2)
PY
        then
          :
        else
          echo "[HINT] Si cet endpoint reste lent/KO: déployer la dernière version FabBoard (fix worker lifecycle + cache expiré)."
        fi

        if docker exec -i fabboard python - <<'PY'
from pathlib import Path
import sys

app_py = Path('/app/app.py')
try:
    txt = app_py.read_text(encoding='utf-8', errors='ignore')
except Exception:
    sys.exit(1)

# Ancienne version: arrêt du worker à chaque requête via teardown_appcontext.
if 'teardown_appcontext' in txt:
    print('[WARN] Build FabBoard potentiellement ancien: teardown_appcontext détecté dans /app/app.py')
    sys.exit(2)
sys.exit(0)
PY
        then
          :
        else
          echo "[HINT] Mettre à jour/rebuild FabBoard recommandé (code app.py non aligné avec le fix lifecycle worker)."
        fi
      fi
    fi

    rm -f "$_worker_tmp"
  else
    echo "[INFO] curl/python3 absent: diagnostic worker ignoré"
  fi

  echo
  echo "===== Inter-app connectivity (FabBoard container -> Fabtrack) ====="
  if docker inspect -f '{{.State.Running}}' fabboard 2>/dev/null | grep -q true; then
    if ! docker exec -i fabboard python - <<'PY'
import os
import sys
import urllib.request

base = (os.environ.get('FABTRACK_URL') or '').rstrip('/')
if not base:
    print('[WARN] FABTRACK_URL absent dans le conteneur fabboard')
    sys.exit(0)

checks = [
    ('/api/stats/summary', 'stats'),
    ('/missions/api/list', 'missions'),
]

ok = True
for path, label in checks:
    url = base + path
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            code = getattr(resp, 'status', 200)
        print(f'[OK] FabBoard -> Fabtrack {label}: {url} (HTTP {code})')
    except Exception as exc:
        ok = False
        print(f'[KO] FabBoard -> Fabtrack {label}: {url} ({exc})')

if not ok:
    sys.exit(2)
PY
    then
      echo "[audit] Vérifie Paramètres > Sources dans FabBoard (URL source Fabtrack + bouton Test)."
    fi
  else
    echo "[INFO] FabBoard non démarré: test intra-conteneur ignoré"
  fi
}

run_cleanup_safe() {
  require_docker_compose

  local containers=(fabhome fabtrack pretgo fabboard)
  # Use configured ports if env already loaded, otherwise fall back to defaults.
  local cleanup_ports=("${FABHOME_PORT:-3001}" "${FABTRACK_PORT:-5555}" "${PRETGO_PORT:-5000}" "${FABBOARD_PORT:-5580}")
  local ts backup_dir mounts_file
  ts="$(date +%F-%H%M%S)"
  backup_dir="$HOME/backup-fabsuite/$ts"
  mounts_file="$backup_dir/bind-paths.txt"

  mkdir -p "$backup_dir"

  echo "[cleanup-safe] Collecting bind mounts..."
  for c in "${containers[@]}"; do
    if docker inspect "$c" >/dev/null 2>&1; then
      # Try Go template first; fall back to JSON extraction if template parsing fails.
      docker inspect --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}}
{{end}}{{end}}' "$c" 2>/dev/null \
        || docker inspect "$c" 2>/dev/null | python3 -c "
import sys,json
for m in json.load(sys.stdin)[0].get('Mounts',[]):
  if m.get('Type')=='bind': print(m.get('Source',''))
" 2>/dev/null || true
    fi
  done | sort -u > "$mounts_file"

  echo "[cleanup-safe] Backing up bind folders to $backup_dir ..."
  while IFS= read -r p; do
    [[ -n "$p" ]] || continue
    local name
    name="$(echo "$p" | sed 's#/#_#g' | sed 's#^_##')"
    run_privileged tar -czf "$backup_dir/${name}.tgz" "$p" 2>/dev/null || true
  done < "$mounts_file"

  echo "[cleanup-safe] Removing FabSuite containers..."
  for c in "${containers[@]}"; do
    docker rm -f "$c" 2>/dev/null || true
  done

  # Also remove any running container that publishes on a FabSuite port
  # (catches old deploys with different container names / project prefixes).
  for port in "${cleanup_ports[@]}"; do
    local by_port
    by_port="$(docker ps -q --filter "publish=$port" 2>/dev/null || true)"
    if [[ -n "$by_port" ]]; then
      local cname
      cname="$(docker ps --format '{{.Names}}' --filter "publish=$port" 2>/dev/null | tr '\n' ' ' || true)"
      echo "[cleanup-safe] Stopping container(s) on port $port: $cname"
      docker rm -f $by_port 2>/dev/null || true
    fi
  done

  # Kill any remaining process on FabSuite ports (docker-proxy orphans, Flask, systemd, etc.)
  # Collects PIDs from ALL matching lines so both IPv4 and IPv6 listeners are killed.
  for port in "${cleanup_ports[@]}"; do
    if ss -ltn 2>/dev/null | grep -qE ":${port}\b"; then
      local all_lines pids
      all_lines="$(run_privileged ss -ltnp 2>/dev/null | grep -E ":${port}\b" || true)"
      local pids
      pids="$(echo "$all_lines" | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | sort -u || true)"
      if [[ -n "$pids" ]]; then
        echo "[cleanup-safe] Killing processes on port $port (PIDs: $pids)"
        for pid in $pids; do
          run_privileged kill "$pid" 2>/dev/null || true
        done
      elif command -v fuser >/dev/null 2>&1; then
        echo "[cleanup-safe] fuser -k ${port}/tcp"
        run_privileged fuser -k "${port}/tcp" 2>/dev/null || true
      fi
    fi
  done

  echo "[cleanup-safe] Pruning docker artifacts..."
  docker image prune -f || true
  docker builder prune -f || true
  docker network prune -f || true

  echo
  echo "Cleanup complete. Backup folder: $backup_dir"
}

run_prepare_host() {
  require_cmd apt-get
  require_cmd curl
  require_cmd gpg

  local codename
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"

  echo "[prepare-host] Installing system prerequisites..."
  run_privileged apt-get update
  run_privileged apt-get install -y ca-certificates curl gnupg git

  echo "[prepare-host] Configuring Docker apt repository..."
  run_privileged install -m 0755 -d /etc/apt/keyrings

  local docker_gpg_url tmp_gpg tmp_dearmored tmp_list arch
  docker_gpg_url="https://download.docker.com/linux/ubuntu/gpg"
  tmp_gpg="$(mktemp)"
  tmp_dearmored="$(mktemp)"
  tmp_list="$(mktemp)"
  arch="$(dpkg --print-architecture)"

  curl -fsSL "$docker_gpg_url" -o "$tmp_gpg"
  if [ ! -s "$tmp_gpg" ]; then
    echo "ERROR: Docker GPG key download failed (empty file)."
    echo "URL: $docker_gpg_url"
    rm -f "$tmp_gpg" "$tmp_dearmored" "$tmp_list"
    exit 1
  fi

  # Dearmor as current user (avoids sudo/pipe issues with gpg).
  # If the key is already binary or dearmor fails, copy as-is.
  if gpg --batch --dearmor --yes -o "$tmp_dearmored" "$tmp_gpg" 2>/dev/null; then
    run_privileged install -m 0644 "$tmp_dearmored" /etc/apt/keyrings/docker.gpg
  elif file "$tmp_gpg" 2>/dev/null | grep -qi 'pgp\|gpg\|public.key'; then
    echo "[prepare-host] GPG key appears binary, installing directly."
    run_privileged install -m 0644 "$tmp_gpg" /etc/apt/keyrings/docker.gpg
  else
    echo "ERROR: Docker GPG key download returned unexpected content."
    echo "URL: $docker_gpg_url"
    echo "First lines of response:"
    sed -n '1,5p' "$tmp_gpg" || true
    rm -f "$tmp_gpg" "$tmp_dearmored" "$tmp_list"
    exit 1
  fi

  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' "$arch" "$codename" > "$tmp_list"
  run_privileged install -m 0644 "$tmp_list" /etc/apt/sources.list.d/docker.list

  rm -f "$tmp_gpg" "$tmp_dearmored" "$tmp_list"

  echo "[prepare-host] Installing Docker Engine + Compose plugin..."
  run_privileged apt-get update
  run_privileged apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  local user_name="${SUDO_USER:-${USER:-}}"
  if [[ -n "$user_name" ]]; then
    run_privileged usermod -aG docker "$user_name" || true
  fi

  echo
  echo "Host preparation complete."
  echo "If this is the first install, re-login (or run 'newgrp docker') before using docker without sudo."
}

create_env_from_example() {
  local env_dir
  env_dir="$(dirname "$ENV_FILE")"
  mkdir -p "$env_dir"

  if [[ -f "$ENV_FILE" ]]; then
    return
  fi

  if [[ -f "${SCRIPT_DIR}/fabsuite-ubuntu.env.example" ]]; then
    cp "${SCRIPT_DIR}/fabsuite-ubuntu.env.example" "$ENV_FILE"
  else
    cat > "$ENV_FILE" <<'EOF'
GIT_REPO_URL=https://github.com/fablabloritz-coder/Fablab-Suite.git
GIT_BRANCH=main
INSTALL_DIR=$HOME/fablab-suite
APPS="FabHome Fabtrack PretGo FabBoard"
TZ=Europe/Paris
FLASK_SECRET_KEY=
FABHOME_PORT=3001
FABTRACK_PORT=5555
PRETGO_PORT=5000
FABBOARD_PORT=5580
FABHOME_DATA_PATH=./data
FABHOME_ICONS_PATH=./icons
FABTRACK_URL=
PRETGO_URL=
FABBOARD_URL=
EOF
  fi

  normalize_env_file "$ENV_FILE"

  echo "Created env file: $ENV_FILE"
}

normalize_env_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0

  # Tolerate Windows uploads: source fails on CRLF with "$'\\r': command not found".
  if LC_ALL=C grep -q $'\r' "$file"; then
    sed -i 's/\r$//' "$file"
    echo "Normalized CRLF -> LF: $file"
  fi

  # Backward compatibility: old placeholder '<owner>' breaks shell parsing on source.
  if grep -q '<owner>' "$file"; then
    sed -i 's#<owner>#fablabloritz-coder#g' "$file"
    echo "Normalized unsafe placeholder '<owner>' -> 'fablabloritz-coder': $file"
  fi

  # Normalize legacy placeholder owner to the default monorepo owner.
  if grep -q 'OWNER_OR_ORG' "$file"; then
    sed -i 's#OWNER_OR_ORG#fablabloritz-coder#g' "$file"
    echo "Normalized placeholder 'OWNER_OR_ORG' -> 'fablabloritz-coder': $file"
  fi
}

set_env_var() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="${value//&/\\&}"

  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

is_placeholder_repo_url() {
  local url="$1"
  [[ "$url" == *"<owner>"* || "$url" == *"OWNER_OR_ORG"* ]]
}

has_legacy_suite_layout() {
  local base="$1"
  [[ -f "$base/FabHome/docker-compose.yml" && -f "$base/Fabtrack/docker-compose.yml" && -f "$base/PretGo/docker-compose.yml" && -f "$base/FabBoard/docker-compose.yml" ]]
}

require_valid_repo_url() {
  local url="$1"
  if is_placeholder_repo_url "$url"; then
    echo "ERROR: GIT_REPO_URL is not configured (placeholder detected)."
    echo "Edit $ENV_FILE and set e.g.:"
    echo "  GIT_REPO_URL=${DEFAULT_MONOREPO_URL}"
    return 1
  fi
  return 0
}

normalize_github_repo_url() {
  local raw="$1"
  if [[ "$raw" =~ ^https://github\.com/([^/]+)/([^/]+)(\.git)?$ ]]; then
    local owner="${BASH_REMATCH[1]}"
    local repo="${BASH_REMATCH[2]}"
    repo="${repo%.git}"
    echo "https://github.com/${owner}/${repo}.git"
    return 0
  fi
  if [[ "$raw" =~ ^git@github\.com:([^/]+)/([^/]+)(\.git)?$ ]]; then
    local owner="${BASH_REMATCH[1]}"
    local repo="${BASH_REMATCH[2]}"
    repo="${repo%.git}"
    echo "https://github.com/${owner}/${repo}.git"
    return 0
  fi
  echo "$raw"
}

github_owner_from_remote_url() {
  local raw="$1"
  if [[ "$raw" =~ ^https://github\.com/([^/]+)/[^/]+(\.git)?$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "$raw" =~ ^git@github\.com:([^/]+)/[^/]+(\.git)?$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

detect_repo_url_from_existing_checkouts() {
  local dir remote normalized owner app base

  # 1) Try monorepo checkouts first.
  for dir in "$INSTALL_DIR" "$HOME/fablab-suite" "$HOME/fabsuite"; do
    if [[ -d "$dir/.git" ]]; then
      remote="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
      if [[ -n "$remote" ]]; then
        normalized="$(normalize_github_repo_url "$remote")"
        echo "$normalized"
        return 0
      fi
    fi
  done

  # 2) Try legacy per-app repositories and infer monorepo owner.
  for base in "$INSTALL_DIR" "$HOME/fabsuite" "$HOME/fablab-suite"; do
    for app in FabHome Fabtrack PretGo FabBoard; do
      dir="$base/$app"
      if [[ -d "$dir/.git" ]]; then
        remote="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
        owner="$(github_owner_from_remote_url "$remote" 2>/dev/null || true)"
        if [[ -n "$owner" ]]; then
          echo "https://github.com/${owner}/Fablab-Suite.git"
          return 0
        fi
      fi
    done
  done

  return 1
}

resolve_bind_source_path() {
  local app="$1"
  local raw_path="$2"
  local app_dir="${INSTALL_DIR}/${app}"
  local abs_path=""

  if [[ "$raw_path" = /* ]]; then
    abs_path="$raw_path"
  else
    abs_path="${app_dir}/${raw_path}"
  fi

  if command -v readlink >/dev/null 2>&1; then
    readlink -m "$abs_path"
  else
    echo "$abs_path"
  fi
}

container_bind_source_for_dest() {
  local container="$1"
  local dest="$2"

  if ! docker inspect "$container" >/dev/null 2>&1; then
    echo ""
    return 0
  fi

  docker inspect "$container" 2>/dev/null | python3 -c $'import json\nimport sys\n\ndest = sys.argv[1]\ntry:\n    payload = json.load(sys.stdin)\n    mounts = payload[0].get("Mounts", []) if payload else []\nexcept Exception:\n    print("")\n    raise SystemExit(0)\n\nfor mount in mounts:\n    if mount.get("Type") == "bind" and (mount.get("Destination") or "") == dest:\n        print(mount.get("Source") or "")\n        break\nelse:\n    print("")\n' "$dest"
}

path_has_data() {
  local p="$1"
  if [[ -d "$p" ]]; then
    [[ -n "$(find "$p" -mindepth 1 -print -quit 2>/dev/null)" ]]
    return
  fi
  [[ -f "$p" ]]
}

path_state_label() {
  local p="$1"
  if path_has_data "$p"; then
    echo "has-data"
  elif [[ -e "$p" ]]; then
    echo "exists-empty"
  else
    echo "missing"
  fi
}

generate_secret_key() {
  local secret=""
  if command -v openssl >/dev/null 2>&1; then
    secret="$(openssl rand -hex 32)"
  elif command -v python3 >/dev/null 2>&1; then
    secret="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  else
    secret="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  echo "$secret"
}

load_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: env file not found: $ENV_FILE"
    echo "Tip: run install first, or create it from fabsuite-ubuntu.env.example"
    exit 1
  fi

  normalize_env_file "$ENV_FILE"

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  GIT_REPO_URL="${GIT_REPO_URL:-${DEFAULT_MONOREPO_URL}}"
  GIT_BRANCH="${GIT_BRANCH:-main}"
  INSTALL_DIR="${INSTALL_DIR:-$HOME/fablab-suite}"
  APPS="${APPS:-FabHome Fabtrack PretGo FabBoard}"
  FABHOME_DATA_PATH="${FABHOME_DATA_PATH:-./data}"
  FABHOME_ICONS_PATH="${FABHOME_ICONS_PATH:-./icons}"

  if is_placeholder_repo_url "$GIT_REPO_URL"; then
    local _detected_url
    _detected_url="$(detect_repo_url_from_existing_checkouts || true)"
    if [[ -n "$_detected_url" ]]; then
      GIT_REPO_URL="$_detected_url"
      set_env_var "GIT_REPO_URL" "$GIT_REPO_URL"
      echo "Auto-detected GIT_REPO_URL from existing checkout: $GIT_REPO_URL"
    else
      GIT_REPO_URL="$DEFAULT_MONOREPO_URL"
      set_env_var "GIT_REPO_URL" "$GIT_REPO_URL"
      echo "Normalized placeholder GIT_REPO_URL -> $GIT_REPO_URL"
    fi
  fi

  TZ="${TZ:-Europe/Paris}"
  FLASK_SECRET_KEY="${FLASK_SECRET_KEY:-}"

  FABHOME_PORT="${FABHOME_PORT:-3001}"
  FABTRACK_PORT="${FABTRACK_PORT:-5555}"
  PRETGO_PORT="${PRETGO_PORT:-5000}"
  FABBOARD_PORT="${FABBOARD_PORT:-5580}"

  # Compute container-safe inter-app URLs.
  # All app compose files define host.docker.internal:host-gateway.
  local _srv_host _legacy_fabtrack_url _legacy_pretgo_url _legacy_fabboard_url
  _srv_host="$(hostname)"
  _legacy_fabtrack_url="http://${_srv_host}:${FABTRACK_PORT:-5555}"
  _legacy_pretgo_url="http://${_srv_host}:${PRETGO_PORT:-5000}"
  _legacy_fabboard_url="http://${_srv_host}:${FABBOARD_PORT:-5580}"

  # Auto-migrate legacy defaults (hostname-based) to host-gateway URLs.
  if [[ "${FABTRACK_URL:-}" == "$_legacy_fabtrack_url" ]]; then
    FABTRACK_URL=""
  fi
  if [[ "${PRETGO_URL:-}" == "$_legacy_pretgo_url" ]]; then
    PRETGO_URL=""
  fi
  if [[ "${FABBOARD_URL:-}" == "$_legacy_fabboard_url" ]]; then
    FABBOARD_URL=""
  fi

  FABTRACK_URL="${FABTRACK_URL:-http://host.docker.internal:${FABTRACK_PORT:-5555}}"
  PRETGO_URL="${PRETGO_URL:-http://host.docker.internal:${PRETGO_PORT:-5000}}"
  FABBOARD_URL="${FABBOARD_URL:-http://host.docker.internal:${FABBOARD_PORT:-5580}}"

  # localhost URLs are not reachable from containers; normalize automatically.
  if [[ "$FABTRACK_URL" =~ ^https?://(localhost|127\.0\.0\.1)(:|/|$) ]]; then
    FABTRACK_URL="http://host.docker.internal:${FABTRACK_PORT:-5555}"
  fi
  if [[ "$PRETGO_URL" =~ ^https?://(localhost|127\.0\.0\.1)(:|/|$) ]]; then
    PRETGO_URL="http://host.docker.internal:${PRETGO_PORT:-5000}"
  fi
  if [[ "$FABBOARD_URL" =~ ^https?://(localhost|127\.0\.0\.1)(:|/|$) ]]; then
    FABBOARD_URL="http://host.docker.internal:${FABBOARD_PORT:-5580}"
  fi

  if [[ -z "$FLASK_SECRET_KEY" ]]; then
    FLASK_SECRET_KEY="$(generate_secret_key)"
    set_env_var "FLASK_SECRET_KEY" "$FLASK_SECRET_KEY"
    echo "Generated FLASK_SECRET_KEY in $ENV_FILE"
  fi

  # Keep key defaults explicit in env file for readability.
  set_env_var "GIT_REPO_URL" "$GIT_REPO_URL"
  set_env_var "TZ" "$TZ"
  set_env_var "FABHOME_DATA_PATH" "$FABHOME_DATA_PATH"
  set_env_var "FABHOME_ICONS_PATH" "$FABHOME_ICONS_PATH"
  set_env_var "FABTRACK_URL" "$FABTRACK_URL"
  set_env_var "PRETGO_URL" "$PRETGO_URL"
  set_env_var "FABBOARD_URL" "$FABBOARD_URL"

  if is_placeholder_repo_url "$GIT_REPO_URL"; then
    echo "WARNING: GIT_REPO_URL uses a placeholder owner value."
    echo "Edit $ENV_FILE and set your real GitHub repo URL before install/update."
  fi
}

run_repair_env() {
  create_env_from_example
  load_env

  echo "[repair-env] ENV file: $ENV_FILE"
  echo "[repair-env] INSTALL_DIR: $INSTALL_DIR"
  echo "[repair-env] APPS: $APPS"

  if is_placeholder_repo_url "$GIT_REPO_URL"; then
    echo "[repair-env] ERROR: GIT_REPO_URL est encore un placeholder."
    echo "[repair-env] Renseigne GIT_REPO_URL dans $ENV_FILE ou via la GUI (champ URL repo monorepo)."
    exit 1
  else
    echo "[repair-env] OK: GIT_REPO_URL=$GIT_REPO_URL"
  fi

  if [[ -d "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
    if [[ -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      if has_legacy_suite_layout "$INSTALL_DIR"; then
        local legacy_dir monorepo_dir
        legacy_dir="$INSTALL_DIR"
        monorepo_dir="$HOME/fablab-suite"

        if [[ "$legacy_dir" == "$monorepo_dir" ]]; then
          echo "[repair-env] ERROR: dossier legacy détecté mais INSTALL_DIR pointe déjà vers $monorepo_dir"
          echo "[repair-env] Déplace/renomme l'ancien dossier puis relance repair-env."
          exit 1
        fi

        INSTALL_DIR="$monorepo_dir"
        mkdir -p "$INSTALL_DIR"
        set_env_var "INSTALL_DIR" "$INSTALL_DIR"

        FABHOME_DATA_PATH="${legacy_dir}/FabHome/data"
        FABHOME_ICONS_PATH="${legacy_dir}/FabHome/icons"
        set_env_var "FABHOME_DATA_PATH" "$FABHOME_DATA_PATH"
        set_env_var "FABHOME_ICONS_PATH" "$FABHOME_ICONS_PATH"

        set_env_var "FABTRACK_DATA_PATH" "${legacy_dir}/Fabtrack/docker-data/data"
        set_env_var "FABTRACK_UPLOADS_PATH" "${legacy_dir}/Fabtrack/docker-data/uploads"
        set_env_var "PRETGO_DATA_PATH" "${legacy_dir}/PretGo/docker-data/data"
        set_env_var "PRETGO_UPLOADS_PATH" "${legacy_dir}/PretGo/docker-data/uploads/materiel"
        set_env_var "FABBOARD_DATA_PATH" "${legacy_dir}/FabBoard/docker-data/data"

        echo "[repair-env] MIGRATION: layout legacy détecté dans ${legacy_dir}"
        echo "[repair-env] MIGRATION: INSTALL_DIR basculé vers monorepo ${INSTALL_DIR}"
        echo "[repair-env] MIGRATION: chemins de données conservés vers les dossiers legacy"
        echo "[repair-env] MIGRATION: relance install pour cloner le monorepo et rebuild proprement"
      else
        echo "[repair-env] ERROR: INSTALL_DIR existe mais n'est pas un monorepo git: $INSTALL_DIR"
        echo "[repair-env] Le mode legacy multi-repos n'est plus supporté pour install/update."
        echo "[repair-env] Action recommandée:"
        echo "[repair-env]   1) définir INSTALL_DIR vers un dossier vide (ex: \$HOME/fablab-suite)"
        echo "[repair-env]   2) relancer install (clone monorepo + rebuild complet)"
        echo "[repair-env]   3) conserver les données via les variables *_DATA_PATH si besoin"
        exit 1
      fi
    fi
  fi

  local app
  local -a app_list
  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    if [[ -f "${INSTALL_DIR}/${app}/docker-compose.yml" ]]; then
      echo "[repair-env] [OK] ${app} trouvé dans ${INSTALL_DIR}/${app}"
    else
      echo "[repair-env] [INFO] ${app} absent localement (normal avant un premier install)."
    fi
  done
}

run_check_data_safety() {
  require_docker_compose
  require_cmd python3

  echo "===== Data safety precheck ====="

  local -a specs
  specs=(
    "FabHome|fabhome|core-data|/app/data|FABHOME_DATA_PATH|./data"
    "FabHome|fabhome|icons|/app/static/icons|FABHOME_ICONS_PATH|./icons"
    "Fabtrack|fabtrack|core-data|/app/data|FABTRACK_DATA_PATH|./docker-data/data"
    "PretGo|pretgo|core-data|/app/data|PRETGO_DATA_PATH|./docker-data/data"
    "FabBoard|fabboard|core-data|/app/data|FABBOARD_DATA_PATH|./docker-data/data"
  )

  local risk_count=0
  local spec app container label dest var_name default_rel raw_path expected current current_norm cur_state next_state

  for spec in "${specs[@]}"; do
    IFS='|' read -r app container label dest var_name default_rel <<< "$spec"

    raw_path="${!var_name:-$default_rel}"

    expected="$(resolve_bind_source_path "$app" "$raw_path")"
    current="$(container_bind_source_for_dest "$container" "$dest")"

    if [[ -n "$current" ]]; then
      if command -v readlink >/dev/null 2>&1; then
        current_norm="$(readlink -m "$current")"
      else
        current_norm="$current"
      fi

      if [[ "$current_norm" == "$expected" ]]; then
        echo "[OK] ${app} ${label}: path stable (${expected})"
      else
        cur_state="$(path_state_label "$current_norm")"
        next_state="$(path_state_label "$expected")"
        echo "[RISK] ${app} ${label}: path actuel='${current_norm}' -> prochain='${expected}' (actuel:${cur_state}, prochain:${next_state})"
        risk_count=$((risk_count + 1))
      fi
    else
      if path_has_data "$expected"; then
        echo "[OK] ${app} ${label}: conteneur absent, chemin attendu contient déjà des données (${expected})"
      elif [[ -e "$expected" ]]; then
        echo "[INFO] ${app} ${label}: conteneur absent, chemin attendu existe mais est vide (${expected})"
      else
        echo "[INFO] ${app} ${label}: conteneur absent, chemin attendu non trouvé (${expected})"
      fi
    fi
  done

  echo ""
  if (( risk_count > 0 )); then
    echo "[ALERT] RISK_COUNT=${risk_count}"
    echo "[ALERT] Risque détecté: une ou plusieurs apps vont utiliser un chemin data différent du chemin actuel."
    echo "[ALERT] Vérifie INSTALL_DIR et les variables *_DATA_PATH avant install/update."
    return 2
  fi

  echo "[SAFE] AUCUN_ECRASEMENT_DETECTE"
  return 0
}

repo_exists() {
  local app="$1"
  [[ -d "${INSTALL_DIR}/${app}" && -f "${INSTALL_DIR}/${app}/docker-compose.yml" ]]
}

service_name_for_app() {
  local app="$1"
  case "$app" in
    FabHome) echo "fabhome" ;;
    Fabtrack) echo "fabtrack" ;;
    PretGo) echo "pretgo" ;;
    FabBoard) echo "fabboard" ;;
    *)
      echo ""
      ;;
  esac
}

port_for_app() {
  local app="$1"
  case "$app" in
    FabHome) echo "$FABHOME_PORT" ;;
    Fabtrack) echo "$FABTRACK_PORT" ;;
    PretGo) echo "$PRETGO_PORT" ;;
    FabBoard) echo "$FABBOARD_PORT" ;;
    *)
      echo ""
      ;;
  esac
}

with_repo() {
  local app="$1"
  shift
  (
    cd "${INSTALL_DIR}/${app}"
    docker compose --env-file "$ENV_FILE" "$@"
  )
}

bootstrap_repo() {
  local url="$GIT_REPO_URL"
  local target="${INSTALL_DIR}"

  if [[ -d "$target/.git" ]]; then
    echo "[suite] exists -> git fetch/reset"
    git -C "$target" fetch origin "$GIT_BRANCH"
    git -C "$target" reset --hard "origin/$GIT_BRANCH"
    return 0
  fi

  if [[ -d "$target" ]]; then
    # Allow empty target dir created by install workflow.
    if [[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      require_valid_repo_url "$url" || exit 1
      rmdir "$target"
      echo "[suite] cloning from $url"
      git clone --branch "$GIT_BRANCH" "$url" "$target"
      return 0
    fi

    echo "ERROR: $target exists but is not a FabLab Suite repository"
    echo "Fix: set INSTALL_DIR to an empty folder, or clone the monorepo manually in INSTALL_DIR."
    exit 1
  fi

  require_valid_repo_url "$url" || exit 1
  echo "[suite] cloning from $url"
  git clone --branch "$GIT_BRANCH" "$url" "$target"
}

check_port_free() {
  local app="$1"
  local port
  port="$(port_for_app "$app")"
  [[ -z "$port" ]] && return 0

  # Check if the port is already in use by something other than this app's container.
  local service
  service="$(service_name_for_app "$app")"
  local cid
  cid="$(docker ps -q -f "name=^/${service}$" 2>/dev/null || true)"

  if [[ -n "$cid" ]]; then
    # Our own container is running — that's fine, docker compose will handle it.
    return 0
  fi

  # Is the port occupied?
  if ss -ltn 2>/dev/null | grep -qE ":${port}\b"; then
    echo "ERROR: [$app] port $port is already in use."
    # Check if a Docker container (any name) is holding this port.
    local docker_cname
    docker_cname="$(docker ps --format '{{.Names}}' --filter "publish=$port" 2>/dev/null | head -1 || true)"
    if [[ -n "$docker_cname" ]]; then
      echo "  -> Docker container '$docker_cname' is using port $port"
      echo "  Fix: run cleanup-safe (removes all FabSuite ports), or: docker rm -f $docker_cname"
    else
      local pid_info
      pid_info="$(run_privileged ss -ltnp 2>/dev/null | grep -E ":${port}\b" | head -1 || true)"
      [[ -n "$pid_info" ]] && echo "  -> $pid_info"
      echo "  Fix: run cleanup-safe to kill all FabSuite processes, or stop port $port manually"
    fi
    return 1
  fi
  return 0
}

start_app() {
  local app="$1"
  if ! check_port_free "$app"; then
    return 1
  fi
  echo "[$app] docker compose up -d --build"
  with_repo "$app" up -d --build
}

stop_app() {
  local app="$1"
  echo "[$app] docker compose down"
  with_repo "$app" down
}

update_app() {
  local app="$1"
  start_app "$app"
}

print_status() {
  printf '%-10s %-10s %-10s %-12s %s\n' "App" "Service" "State" "Health" "URL"
  printf '%-10s %-10s %-10s %-12s %s\n' "----------" "----------" "----------" "------------" "---------------------------"

  local app service cid state health port url
  read -r -a app_list <<< "$APPS"

  for app in "${app_list[@]}"; do
    service="$(service_name_for_app "$app")"
    port="$(port_for_app "$app")"
    url="http://localhost:${port}"

    if [[ -z "$service" ]]; then
      printf '%-10s %-10s %-10s %-12s %s\n' "$app" "?" "unknown" "-" "-"
      continue
    fi

    cid="$(docker ps -aq -f "name=^/${service}$")"
    if [[ -z "$cid" ]]; then
      printf '%-10s %-10s %-10s %-12s %s\n' "$app" "$service" "not-found" "-" "$url"
      continue
    fi

    state="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo "unknown")"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$cid" 2>/dev/null || echo "n/a")"

    printf '%-10s %-10s %-10s %-12s %s\n' "$app" "$service" "$state" "$health" "$url"
  done
}

autoregister_suite_apps() {
  # Auto-registers Fabtrack, PretGo and FabBoard in FabHome via its REST API.
  # Safe to call multiple times (skips already-registered URLs).
  local fabhome_url="http://localhost:${FABHOME_PORT:-3001}"
  local register_host register_scheme

  # Optional browser/public host override for links stored in FabHome.
  # Useful when the helper runs remotely over SSH and localhost links would be wrong.
  register_host="${FABHOME_REGISTER_HOST:-}"
  register_scheme="${FABHOME_REGISTER_SCHEME:-http}"
  register_host="${register_host#http://}"
  register_host="${register_host#https://}"
  register_host="${register_host%%/*}"

  if [[ -z "$register_host" ]]; then
    register_host="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')"
    if [[ -z "$register_host" ]]; then
      register_host="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    if [[ -n "$register_host" ]]; then
      echo "Auto-registration host detected: $register_host"
    fi
  else
    echo "Auto-registration host override: $register_host"
  fi

  echo "Auto-registration: waiting for FabHome at $fabhome_url ..."
  local i
  for i in $(seq 1 30); do
    if curl -sf -o /dev/null "${fabhome_url}/api/suite/apps" 2>/dev/null; then
      break
    fi
    sleep 2
  done

  if ! curl -sf -o /dev/null "${fabhome_url}/api/suite/apps" 2>/dev/null; then
    echo "WARNING: FabHome not reachable — skipping auto-registration"
    return 0
  fi

  local existing
  existing="$(curl -sf "${fabhome_url}/api/suite/apps" 2>/dev/null || echo '[]')"

  echo "Registering suite apps in FabHome..."
  local can_parse_existing
  can_parse_existing=0
  if command -v python3 >/dev/null 2>&1; then
    can_parse_existing=1
  else
    echo "  [INFO] python3 absent: migration auto des URLs existantes (par app_id) ignorée"
  fi

  local entry app rest expected_app_id url local_port local_url result j app_existing_id app_existing_url register_http register_tmp
  for entry in \
    "Fabtrack|fabtrack|${FABTRACK_URL:-http://host.docker.internal:${FABTRACK_PORT:-5555}}|${FABTRACK_PORT:-5555}" \
    "PretGo|pretgo|${PRETGO_URL:-http://host.docker.internal:${PRETGO_PORT:-5000}}|${PRETGO_PORT:-5000}" \
    "FabBoard|fabboard|${FABBOARD_URL:-http://host.docker.internal:${FABBOARD_PORT:-5580}}|${FABBOARD_PORT:-5580}"; do
    app="${entry%%|*}"
    rest="${entry#*|}"
    expected_app_id="${rest%%|*}"
    rest="${rest#*|}"
    url="${rest%%|*}"
    local_port="${rest##*|}"
    url="${url%/}"

    if [[ -n "$register_host" ]]; then
      # Force a browser/network-friendly URL in FabHome registrations.
      url="${register_scheme}://${register_host}:${local_port}"
    fi

    local_url="http://localhost:${local_port}/api/fabsuite/health"

    if echo "$existing" | grep -qF "$url"; then
      echo "  [$app] already registered"
      continue
    fi

    app_existing_id=""
    app_existing_url=""
    if [[ "$can_parse_existing" -eq 1 ]]; then
      app_existing_id="$(python3 - "$existing" "$expected_app_id" <<'PY'
import json
import sys

try:
    rows = json.loads(sys.argv[1] if len(sys.argv) > 1 else '[]')
except Exception:
    rows = []
target = (sys.argv[2] if len(sys.argv) > 2 else '').strip().lower()
for row in rows:
    rid = row.get('id')
    app_id = str(row.get('app_id', '')).strip().lower()
    if target and app_id == target and rid is not None:
        print(str(rid))
        break
PY
  )"

      app_existing_url="$(python3 - "$existing" "$expected_app_id" <<'PY'
import json
import sys

try:
    rows = json.loads(sys.argv[1] if len(sys.argv) > 1 else '[]')
except Exception:
    rows = []
target = (sys.argv[2] if len(sys.argv) > 2 else '').strip().lower()
for row in rows:
    app_id = str(row.get('app_id', '')).strip().lower()
    if target and app_id == target:
        print(str(row.get('url', '')).rstrip('/'))
        break
PY
   )"
    fi

    if [[ -n "$app_existing_id" && "$app_existing_url" != "$url" ]]; then
      if curl -sf -X DELETE "${fabhome_url}/api/suite/apps/${app_existing_id}" >/dev/null 2>&1; then
        echo "  [$app] updating URL ${app_existing_url} -> ${url}"
        existing="$(curl -sf "${fabhome_url}/api/suite/apps" 2>/dev/null || echo '[]')"
      else
        echo "  WARNING: [$app] impossible de supprimer l'ancienne entrée id=${app_existing_id}"
      fi
    fi

    # Wait up to 30s for the app itself to be ready before registering.
    for j in $(seq 1 15); do
      if curl -sf -o /dev/null "$local_url" 2>/dev/null; then break; fi
      sleep 2
    done

    if ! curl -sf -o /dev/null "$local_url" 2>/dev/null; then
      echo "  WARNING: [$app] endpoint santé indisponible (${local_url}) — enregistrement reporté"
      continue
    fi

    register_tmp="$(mktemp)"
    register_http="$(curl -sS -o "$register_tmp" -w '%{http_code}' -X POST "${fabhome_url}/api/suite/apps" \
      -H 'Content-Type: application/json' \
      -d "{\"url\": \"$url\"}" 2>/dev/null || echo '000')"
    result="$(cat "$register_tmp" 2>/dev/null || true)"
    rm -f "$register_tmp"

    if [[ "$register_http" =~ ^20[0-9]$ ]] && echo "$result" | grep -q '"ok":true'; then
      echo "  [$app] registered OK -> $url"
    else
      if [[ -z "$result" ]]; then
        result='{"error":"curl failed"}'
      fi
      echo "  WARNING: [$app] registration failed (HTTP ${register_http}): $result"
    fi
  done
}

run_install() {
  require_cmd git
  require_docker_compose
  mkdir -p "$INSTALL_DIR"

  local failed=""
  bootstrap_repo || { echo "ERROR: suite bootstrap failed"; exit 1; }
  if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
    echo "ERROR: INSTALL_DIR n'est pas un monorepo git: $INSTALL_DIR"
    exit 1
  fi

  local suite_remote suite_head
  suite_remote="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "unknown")"
  suite_head="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  echo "[suite] mode: monorepo"
  echo "[suite] source: $suite_remote"
  echo "[suite] commit: $suite_head"

  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    start_app "$app" || { echo "WARNING: [$app] start failed (port conflict?)"; failed="$failed $app"; }
  done

  echo
  echo "Container status:"
  print_status

  if [[ -n "$failed" ]]; then
    echo
    echo "WARNING: some apps had errors:$failed"
    echo "Check port conflicts or logs above. Other apps were deployed successfully."
  fi

  echo
  autoregister_suite_apps
}

run_bootstrap() {
  require_cmd git
  mkdir -p "$INSTALL_DIR"
  bootstrap_repo
}

run_start() {
  require_docker_compose
  local failed=""
  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    if repo_exists "$app"; then
      start_app "$app" || { echo "WARNING: [$app] start failed"; failed="$failed $app"; }
    else
      echo "[$app] skipped (missing repo at ${INSTALL_DIR}/${app})"
    fi
  done
  if [[ -n "$failed" ]]; then
    echo "WARNING: some apps had errors:$failed"
  fi
}

run_stop() {
  require_docker_compose
  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    if repo_exists "$app"; then
      stop_app "$app" || echo "WARNING: [$app] stop failed"
    else
      echo "[$app] skipped (missing repo at ${INSTALL_DIR}/${app})"
    fi
  done
}

run_restart() {
  require_docker_compose
  local failed=""
  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    if repo_exists "$app"; then
      echo "[$app] docker compose restart"
      with_repo "$app" restart || { echo "WARNING: [$app] restart failed"; failed="$failed $app"; }
    else
      echo "[$app] skipped (missing repo at ${INSTALL_DIR}/${app})"
    fi
  done
  if [[ -n "$failed" ]]; then
    echo "WARNING: some apps had errors:$failed"
  fi
}

run_update() {
  require_cmd git
  require_docker_compose
  mkdir -p "$INSTALL_DIR"

  bootstrap_repo || { echo "ERROR: suite update (git fetch/reset) failed"; exit 1; }
  if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
    echo "ERROR: INSTALL_DIR n'est pas un monorepo git: $INSTALL_DIR"
    echo "Le mode legacy multi-repos n'est plus supporté pour update."
    exit 1
  fi

  local suite_remote suite_head
  suite_remote="$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "unknown")"
  suite_head="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  echo "[suite] mode: monorepo"
  echo "[suite] source: $suite_remote"
  echo "[suite] commit: $suite_head"

  local failed=""
  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    update_app "$app" || { echo "WARNING: [$app] update failed"; failed="$failed $app"; }
  done
  if [[ -n "$failed" ]]; then
    echo "WARNING: some apps had errors:$failed"
  fi
}

run_logs() {
  require_docker_compose

  if [[ -n "$LOG_APP" ]]; then
    if repo_exists "$LOG_APP"; then
      with_repo "$LOG_APP" logs --tail 150
    else
      echo "ERROR: app not found or not cloned: $LOG_APP"
      exit 1
    fi
    return
  fi

  read -r -a app_list <<< "$APPS"
  for app in "${app_list[@]}"; do
    if repo_exists "$app"; then
      echo
      echo "===== ${app} ====="
      with_repo "$app" logs --tail 80
    fi
  done
}

# prepare-host, audit, cleanup-safe and repair-env do not require pre-loading env.
if [[ "$ACTION" != "prepare-host" && "$ACTION" != "audit" && "$ACTION" != "cleanup-safe" && "$ACTION" != "repair-env" ]]; then
  # Initialize env file only for commands that actually run actions.
  create_env_from_example
  load_env
fi

case "$ACTION" in
  prepare-host)
    run_prepare_host
    ;;
  audit)
    run_audit
    ;;
  cleanup-safe)
    run_cleanup_safe
    ;;
  repair-env)
    run_repair_env
    ;;
  check-data-safety)
    run_check_data_safety
    ;;
  install)
    run_install
    ;;
  bootstrap)
    run_bootstrap
    ;;
  update)
    run_update
    ;;
  start)
    run_start
    ;;
  stop)
    run_stop
    ;;
  restart)
    run_restart
    ;;
  status)
    require_docker_compose
    print_status
    ;;
  logs)
    run_logs
    ;;
  *)
    usage
    exit 1
    ;;
esac
