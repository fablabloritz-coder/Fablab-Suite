"""FabHome — Page d'accueil personnalisée avec grille configurable."""

import os
import logging

from flask import Flask, request, jsonify, redirect, url_for

from fabsuite_core.security import load_secret_key
from routes import register_blueprints
import models

# ============================================================
#  CRÉATION DE L'APPLICATION
# ============================================================

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024

# ── Clé secrète : env > fichier persisté > génération automatique ──
DATA_DIR = os.environ.get('FABHOME_DATA', 'data')
app.config['SECRET_KEY'] = load_secret_key(DATA_DIR, env_var='FABHOME_SECRET')

# Créer les dossiers nécessaires
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
ICON_DIR = os.path.join(UPLOAD_DIR, 'icons')
BG_DIR = os.path.join(UPLOAD_DIR, 'bg')
os.makedirs(ICON_DIR, exist_ok=True)
os.makedirs(BG_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Initialiser la base de données au démarrage
models.init_db()

# ============================================================
#  ENREGISTREMENT DES BLUEPRINTS
# ============================================================

register_blueprints(app)

# ============================================================
#  CORS pour /api/suite/* (en plus de /api/fabsuite/* géré par fabsuite_core)
# ============================================================

@app.after_request
def suite_cors(response):
    if request.path.startswith('/api/suite/'):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# ============================================================
#  PAGES D'ERREUR PERSONNALISÉES
# ============================================================

@app.errorhandler(404)
def err_404(e):
    if request.path.startswith('/api/'):
        return jsonify(error='Ressource non trouvée'), 404
    return redirect(url_for('pages.index'))


@app.errorhandler(500)
def err_500(e):
    logger.exception("Erreur interne")
    return jsonify(error='Erreur interne du serveur'), 500


@app.errorhandler(413)
def err_413(e):
    return jsonify(error='Fichier trop volumineux (max 2 Mo)'), 413

# ============================================================
#  DÉMARRAGE
# ============================================================

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    port = int(os.environ.get('FABHOME_APP_PORT', os.environ.get('PORT', '3000')))
    app.run(host='0.0.0.0', port=port, debug=debug)
