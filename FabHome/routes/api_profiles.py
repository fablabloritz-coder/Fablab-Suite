"""FabHome — Blueprint : API profils + réglages."""

import logging

from flask import Blueprint, jsonify, request, session

import models
from routes import get_current_profile_id

bp = Blueprint('api_profiles', __name__)
logger = logging.getLogger(__name__)


def set_current_profile_id(profile_id):
    session['profile_id'] = profile_id


@bp.route('/api/profiles', methods=['GET'])
def api_get_profiles():
    return jsonify(profiles=models.get_profiles(),
                   current=get_current_profile_id())


@bp.route('/api/profiles', methods=['POST'])
def api_create_profile():
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify(error='Nom requis'), 400
        profile_id = models.create_profile(
            name[:50],
            (data.get('icon') or '👤')[:10],
            (data.get('color') or '#6c757d')[:20])
        return jsonify(id=profile_id), 201
    except Exception as e:
        logger.error(f"Erreur création profil: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500


@bp.route('/api/profiles/<int:profile_id>', methods=['PUT'])
def api_update_profile(profile_id):
    data = request.get_json() or {}
    models.update_profile(
        profile_id,
        name=data.get('name'),
        icon=data.get('icon'),
        color=data.get('color'))
    return jsonify(ok=True)


@bp.route('/api/profiles/<int:profile_id>', methods=['DELETE'])
def api_delete_profile(profile_id):
    if profile_id == 1:
        return jsonify(error='Cannot delete default profile'), 400
    models.delete_profile(profile_id)
    if get_current_profile_id() == profile_id:
        set_current_profile_id(1)
    return jsonify(ok=True)


@bp.route('/api/profiles/switch', methods=['POST'])
def api_switch_profile():
    data = request.get_json() or {}
    profile_id = data.get('profile_id')
    if not profile_id:
        return jsonify(error='profile_id requis'), 400
    profile = models.get_profile(profile_id)
    if not profile:
        return jsonify(error='Profil introuvable'), 404
    set_current_profile_id(profile_id)
    return jsonify(ok=True, profile_id=profile_id)


# ── API : Réglages ────────────────────────────────────────

@bp.route('/api/settings', methods=['PUT'])
def api_update_settings():
    try:
        data = request.get_json()
        if not data:
            return jsonify(error='Données manquantes'), 400
        profile_id = get_current_profile_id()
        allowed = {'title', 'theme', 'background_url',
                   'search_provider', 'grid_cols', 'grid_rows',
                   'caldav_url', 'caldav_username', 'caldav_password', 'camera_urls',
                   'refresh_interval',
                   'custom_accent', 'custom_bg', 'custom_card_bg', 'custom_text',
                   'fabboard_url', 'fabboard_default_widget'}
        for k, v in data.items():
            if k in allowed:
                models.update_setting(k, str(v)[:500], profile_id)
        return jsonify(ok=True)
    except Exception as e:
        logger.error(f"Erreur mise à jour réglages: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500
