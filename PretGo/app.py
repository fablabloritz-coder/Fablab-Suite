"""
==============================================
  PRETGO — Gestion de Prêt de Matériel
  Application Flask avec base SQLite locale
==============================================
Pour lancer : python app.py
Puis ouvrir http://localhost:5000
"""

from flask import Flask, request, session, g, abort, render_template
from database import init_db, DATA_DIR
from utils import get_app_db, register_filters, register_context_processors, check_and_run_backup
from fabsuite_core.security import load_secret_key
from routes import register_blueprints
from scheduler import email_scheduler
import os
import secrets

# ============================================================
#  CRÉATION DE L'APPLICATION
# ============================================================

app = Flask(__name__)

# ── Clé secrète : env > fichier persisté > génération automatique ──
app.secret_key = load_secret_key(DATA_DIR, env_var='FLASK_SECRET_KEY')

# Initialiser la base de données au démarrage
with app.app_context():
    try:
        init_db()
    except Exception as e:
        import traceback as _tb
        print("\n[ERREUR INIT_DB]", e)
        _tb.print_exc()
        raise


# ============================================================
#  SÉCURITÉ : CSRF, HEADERS, TEARDOWN DB
# ============================================================

CSRF_EXEMPT_ENDPOINTS = {
    'api.api_personnes', 'api.api_inventaire', 'api.api_scan',
    'api.api_liste_images', 'api.api_statistiques', 'api.api_parcourir_dossiers',
    'fabsuite.fabsuite_manifest', 'fabsuite.fabsuite_health',
    'fabsuite.fabsuite_widget', 'fabsuite.fabsuite_notifications',
}


def _generate_csrf_token():
    """Génère ou récupère le token CSRF de la session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


@app.before_request
def _csrf_protect():
    """Valide le token CSRF sur toutes les requêtes POST (sauf API exemptées)."""
    if request.method == 'POST':
        if app.config.get('TESTING'):
            return
        if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
            return
        token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
        if not token or token != session.get('_csrf_token'):
            abort(403)


@app.before_request
def _auto_backup_check():
    """Déclenche le check de backup automatique (throttlé à 5 min)."""
    if app.config.get('TESTING') or request.path.startswith('/static'):
        return
    check_and_run_backup(app)


@app.context_processor
def inject_csrf():
    """Rend le token CSRF disponible dans tous les templates."""
    return {'csrf_token': _generate_csrf_token}


@app.after_request
def _set_security_headers(response):
    """Ajoute les headers de sécurité à chaque réponse."""
    # CORS pour /api/fabsuite/* géré par fabsuite_core
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # CSP : autoriser inline styles (thème dynamique) et les CDN utilisés
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'"
    )
    return response


@app.teardown_appcontext
def _close_db(exception):
    """Ferme automatiquement la connexion DB si stockée dans g."""
    db = g.pop('_db', None)
    if db is not None:
        db.close()


# ============================================================
#  ENREGISTREMENT DES FILTRES, CONTEXT PROCESSORS & BLUEPRINTS
# ============================================================

register_filters(app)
register_context_processors(app)
register_blueprints(app)

# ── Démarrer le scheduler des rappels email automatiques ──
email_scheduler.start(app)


# ============================================================
#  PAGES D'ERREUR PERSONNALISÉES
# ============================================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    import traceback
    traceback.print_exc()
    # Écrire le traceback dans un fichier pour diagnostic distant
    try:
        import os
        err_path = os.path.join(DATA_DIR, 'last_error.log')
        with open(err_path, 'w', encoding='utf-8') as f:
            f.write(f'[{__import__("datetime").datetime.now()}] {e}\n')
            f.write(traceback.format_exc())
    except Exception:
        pass
    return render_template('500.html'), 500


# ============================================================
#  LANCEMENT DE L'APPLICATION
# ============================================================

if __name__ == '__main__':
    import signal
    import subprocess
    import sys

    PORT = 5000

    def kill_existing_instance(port):
        """Tue automatiquement toute instance Python qui occupe déjà le port."""
        try:
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'TCP'],
                capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=5
            )
            current_pid = os.getpid()
            for line in result.stdout.splitlines():
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid != current_pid and pid != 0:
                        try:
                            os.kill(pid, signal.SIGTERM)
                            print(f"   [Info] Ancienne instance (PID {pid}) arrêtée.")
                            import time
                            time.sleep(0.5)
                        except (ProcessLookupError, PermissionError):
                            pass
        except Exception:
            pass

    kill_existing_instance(PORT)

    print()
    print("=" * 55)
    print("   PRETGO — Gestion de Prêt de Matériel")
    print("   ----------------------------")
    print("   Ouvrez votre navigateur à l'adresse :")
    print(f"   http://localhost:{PORT}")
    print("=" * 55)
    print()
    # Utiliser waitress (serveur de production) si disponible, sinon Flask dev server
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=PORT, threads=4)
    except ImportError:
        app.run(host='0.0.0.0', port=PORT, debug=False)
