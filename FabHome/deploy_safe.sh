#!/usr/bin/env bash
set -euo pipefail

# Safe deployment helper for FabHome on Linux servers.
# Usage:
#   ./deploy_safe.sh
#   MAX_WAIT=180 ./deploy_safe.sh

SERVICE="fabhome"
MAX_WAIT="${MAX_WAIT:-120}"

cd "$(dirname "$0")"

echo "[1/5] Checking Docker daemon..."
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker command not found"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running"
  exit 1
fi

echo "[2/5] Pulling latest code is your responsibility before this step"
echo "[3/5] Building image..."
docker compose build --pull "$SERVICE"

echo "[4/5] Starting service..."
docker compose up -d "$SERVICE"

CID="$(docker compose ps -q "$SERVICE")"
if [ -z "$CID" ]; then
  echo "ERROR: could not resolve container id for service '$SERVICE'"
  exit 1
fi

echo "[5/5] Waiting for healthy status (timeout: ${MAX_WAIT}s)..."
DEADLINE=$((SECONDS + MAX_WAIT))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null || true)"

  if [ "$STATUS" = "healthy" ] || [ "$STATUS" = "running" ]; then
    echo "OK: FabHome is up (status=$STATUS)"
    echo "URL: http://<SERVER-IP>:${FABHOME_PORT:-3001}"
    exit 0
  fi

  if [ "$STATUS" = "unhealthy" ] || [ "$STATUS" = "exited" ] || [ "$STATUS" = "dead" ]; then
    echo "ERROR: container status is '$STATUS'"
    docker compose logs --tail 150 "$SERVICE"
    exit 1
  fi

  sleep 3
done

echo "ERROR: timed out waiting for service health"
docker compose ps
docker compose logs --tail 150 "$SERVICE"
exit 1
