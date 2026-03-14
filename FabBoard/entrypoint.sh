#!/bin/sh
set -e

# Assurer les permissions sur les volumes Docker montés
for dir in /app/data /app/static/uploads; do
  if [ -d "$dir" ]; then
    chown -R app:app "$dir" 2>/dev/null || true
  fi
done

# Démarrer en tant qu'utilisateur app
exec gosu app "$@"
