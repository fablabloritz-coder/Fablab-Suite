"""
FabTrack v2 — Application Flask principale
Suivi de consommation pour Fablab (Loritz)
"""

from flask import Flask, render_template, request, jsonify
from models import get_db, init_db, DATA_DIR
from fabsuite_core.security import load_secret_key
from routes import register_blueprints
from routes.api_admin import check_auto_backup
import os, logging

# ── App Flask ──
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# ── Clé secrète (env > fichier > génération) ──
app.secret_key = load_secret_key(DATA_DIR)

# ── Enregistrement des blueprints ──
register_blueprints(app)


# ── Init DB au premier request ──
_db_initialized = False

@app.before_request
def ensure_db():
    global _db_initialized
    if _db_initialized and not app.config.get('_DB_NEEDS_REINIT'):
        return
    # Toujours appeler init_db() — CREATE TABLE IF NOT EXISTS est safe
    # et garantit que les nouvelles tables (ex: stock_*) sont créées
    # même sur une DB existante.
    init_db()
    check_auto_backup()
    _db_initialized = True
    app.config.pop('_DB_NEEDS_REINIT', None)


# ── Error handlers ──

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Ressource introuvable'}), 404
    return render_template('base.html', page='erreur'), 404

@app.errorhandler(500)
def internal_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Erreur interne du serveur'}), 500
    return render_template('base.html', page='erreur'), 500

@app.errorhandler(413)
def too_large(e):
    return jsonify({'success': False, 'error': 'Fichier trop volumineux (max 16 Mo)'}), 413


# ── Lancement ──

if __name__ == '__main__':
    init_db()
    print("\n" + "="*50)
    print("  🏭 FabTrack v2 — Suivi Consommation Fablab")
    print("  📡 FabLab Suite manifest v1.0.0")
    print("  📍 http://localhost:5555")
    print("  📍 Réseau: http://<IP>:5555")
    print("="*50 + "\n")
    debug_mode = os.environ.get('FLASK_DEBUG', '1').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=5555, debug=debug_mode)
