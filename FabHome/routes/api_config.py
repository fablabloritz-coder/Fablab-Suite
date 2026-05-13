"""FabHome — Blueprint : API import/export + uploads."""

import os
import uuid
import glob
import logging

from flask import Blueprint, jsonify, request, session

import models
from routes import get_current_profile_id

bp = Blueprint('api_config', __name__)
logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(os.environ.get('FABHOME_DATA', 'data'), 'uploads')
ICON_DIR = os.path.join(UPLOAD_DIR, 'icons')
BG_DIR = os.path.join(UPLOAD_DIR, 'bg')
ALLOWED_IMG = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico'}


@bp.route('/api/config/export')
def api_export_config():
    data = models.export_all()
    return jsonify(data)


@bp.route('/api/config/import', methods=['POST'])
def api_import_config():
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify(error='JSON invalide'), 400
    if 'settings' not in data and 'groups' not in data:
        return jsonify(error='Données de configuration requises'), 400
    try:
        models.import_all(data)
    except Exception as e:
        logger.error(f"Erreur import config: {e}")
        return jsonify(error=f'Import échoué (rollback effectué): {e}'), 500
    return jsonify(ok=True)


# Upload d'icône
@bp.route('/api/upload/icon', methods=['POST'])
def api_upload_icon():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='Fichier manquant'), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_IMG:
        return jsonify(error='Format non supporté'), 400
    name = uuid.uuid4().hex[:12] + ext
    os.makedirs(ICON_DIR, exist_ok=True)
    f.save(os.path.join(ICON_DIR, name))
    return jsonify(url='/uploads/icons/' + name), 201


# Liste des icônes uploadées
@bp.route('/api/icons', methods=['GET'])
def api_list_icons():
    os.makedirs(ICON_DIR, exist_ok=True)
    files = []
    for fname in sorted(os.listdir(ICON_DIR)):
        ext = os.path.splitext(fname)[1].lower()
        if ext in ALLOWED_IMG:
            files.append({'name': fname, 'url': '/uploads/icons/' + fname})
    return jsonify(icons=files)


# Suppression d'une icône uploadée
@bp.route('/api/icons/<name>', methods=['DELETE'])
def api_delete_icon(name):
    # Sécurité : pas de traversal de répertoire
    safe = os.path.basename(name)
    path = os.path.join(ICON_DIR, safe)
    if not os.path.isfile(path):
        return jsonify(error='Fichier introuvable'), 404
    try:
        os.remove(path)
    except OSError as e:
        logger.error(f"Erreur suppression icône {safe}: {e}")
        return jsonify(error='Suppression impossible'), 500
    return jsonify(ok=True)


# Upload de fond d'écran (remplace l'ancien)
@bp.route('/api/upload/background', methods=['POST'])
def api_upload_background():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(error='Fichier manquant'), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_IMG:
        return jsonify(error='Format non supporté'), 400
    # Supprimer l'ancien fond
    for old in glob.glob(os.path.join(BG_DIR, '*')):
        try:
            os.remove(old)
        except OSError:
            pass
    name = 'background' + ext
    f.save(os.path.join(BG_DIR, name))
    bg_url = '/uploads/bg/' + name
    models.update_setting('background_url', bg_url, get_current_profile_id())
    return jsonify(url=bg_url), 201
