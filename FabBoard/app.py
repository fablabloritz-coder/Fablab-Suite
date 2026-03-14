"""
FabBoard v2.0 — Application Flask principale
Tableau de bord TV pour Fablab
Architecture blueprints + fabsuite_core
"""

from flask import Flask, render_template, request, jsonify
from models import get_db, init_db, migrate_db
from fabsuite_core.security import load_secret_key
from sync_worker import start_sync_worker, stop_sync_worker
import os
import logging
import requests as http_requests
from urllib.parse import urlparse
import atexit

# ============================================================
# APPLICATION
# ============================================================

app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
PORT = int(os.environ.get('FABBOARD_PORT', 5580))

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 3600

# Clé secrète via fabsuite_core
app.secret_key = load_secret_key(data_dir=DATA_DIR)

# Enregistrer les blueprints
from routes import register_blueprints
register_blueprints(app)

# Démarrer le sync worker au démarrage de l'app
try:
    print('[App] Démarrage du sync worker au startup...')
    start_sync_worker(poll_interval=10)
    print('[App] Sync worker démarré!')
except Exception as e:
    print(f'[App] Erreur démarrage worker: {e}')


def _shutdown_worker_on_exit():
    """Arrêt propre du worker lors de l'extinction du process."""
    try:
        stop_sync_worker()
    except Exception:
        pass


atexit.register(_shutdown_worker_on_exit)


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Ressource introuvable'}), 404
    return render_template('base.html'), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error('Erreur interne: %s', e)
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Erreur interne du serveur'}), 500
    return render_template('base.html'), 500


@app.errorhandler(413)
def too_large(e):
    return jsonify({'success': False, 'error': 'Fichier trop volumineux (max 16 Mo)'}), 413


# ============================================================
# INIT DB + BOOTSTRAP
# ============================================================

_db_initialized = False


@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        migrate_db()
        _auto_bootstrap_sources()
        _db_initialized = True


def _normalize_base_url(url):
    if not url:
        return ''
    return str(url).strip().rstrip('/')


def _is_running_in_docker():
    return os.path.exists('/.dockerenv')


def _is_localhost_url(url):
    try:
        host = (urlparse(str(url)).hostname or '').lower()
    except Exception:
        return False
    return host in ('localhost', '127.0.0.1', '::1')


def _default_fabtrack_url():
    from_env = _normalize_base_url(os.environ.get('FABTRACK_URL', ''))
    if from_env:
        return from_env
    if _is_running_in_docker():
        return 'http://host.docker.internal:5555'
    return 'http://localhost:5555'


def _auto_bootstrap_sources():
    """Auto-détecte et crée automatiquement les sources connues au premier lancement."""
    db = get_db()
    try:
        fabtrack_url = _default_fabtrack_url()
        existing_fabtrack_rows = db.execute(
            "SELECT id, url FROM sources WHERE type = 'fabtrack' ORDER BY id"
        ).fetchall()

        if not existing_fabtrack_rows:
            actif = 0

            try:
                resp = http_requests.get(f"{fabtrack_url}/api/stats/summary", timeout=3)
                if resp.status_code == 200:
                    actif = 1
                    print(f'[Bootstrap] Fabtrack détecté à {fabtrack_url} ✓')
                else:
                    print(f'[Bootstrap] Fabtrack trouvé mais erreur HTTP {resp.status_code}')
            except http_requests.RequestException:
                print(f'[Bootstrap] Fabtrack non disponible à {fabtrack_url} — source créée inactive')

            db.execute(
                '''INSERT INTO sources (nom, type, url, credentials_json, sync_interval_sec, actif)
                   VALUES (?, ?, ?, '{}', 30, ?)''',
                ('Fabtrack', 'fabtrack', fabtrack_url, actif)
            )
            db.commit()
        elif fabtrack_url and not _is_localhost_url(fabtrack_url):
            updated = 0
            for row in existing_fabtrack_rows:
                current_url = _normalize_base_url(row['url'])
                if _is_localhost_url(current_url) and current_url != fabtrack_url:
                    db.execute(
                        'UPDATE sources SET url = ?, derniere_erreur = ? WHERE id = ?',
                        (fabtrack_url, '', row['id'])
                    )
                    updated += 1

            if updated:
                db.commit()
                print(f"[Bootstrap] {updated} source(s) Fabtrack migrée(s) vers {fabtrack_url}")

    except Exception as e:
        print(f'[Bootstrap] Erreur auto-détection: {e}')
    finally:
        db.close()


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    print(f'[FabBoard] Démarrage sur http://localhost:{PORT}')
    from fabsuite_core import SUITE_SPEC_VERSION
    print(f'[FabBoard] FabLab Suite manifest v{SUITE_SPEC_VERSION}')
    debug_mode = os.environ.get('FLASK_DEBUG', '1').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=PORT, debug=debug_mode)
