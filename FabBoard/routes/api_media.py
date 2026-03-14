"""
FabBoard — API Media (blueprint)
Upload images/vidéos, listing, suppression
"""

from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
import os
import secrets

bp = Blueprint('api_media', __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'webm', 'ogg'}


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


@bp.route('/api/upload', methods=['POST'])
def upload():
    """Upload une image pour les widgets ou les fonds de slide."""
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier envoyé'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nom de fichier vide'}), 400

    if not _allowed_file(file.filename):
        return jsonify({'error': 'Type de fichier non autorisé. Extensions acceptées : ' + ', '.join(ALLOWED_EXTENSIONS)}), 400

    ext = secure_filename(file.filename).rsplit('.', 1)[1].lower()
    unique_name = f"{secrets.token_hex(8)}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, unique_name)
    file.save(filepath)

    url = f"/static/uploads/{unique_name}"
    return jsonify({'success': True, 'url': url, 'filename': unique_name})


@bp.route('/api/upload-video', methods=['POST'])
def upload_video():
    """Upload une vidéo pour le widget vidéo."""
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier envoyé'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nom de fichier vide'}), 400

    if not _allowed_video_file(file.filename):
        return jsonify({'error': 'Type de fichier non autorisé. Extensions acceptées : ' + ', '.join(ALLOWED_VIDEO_EXTENSIONS)}), 400

    ext = secure_filename(file.filename).rsplit('.', 1)[1].lower()
    unique_name = f"{secrets.token_hex(8)}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, unique_name)
    file.save(filepath)

    url = f"/static/uploads/{unique_name}"
    return jsonify({'success': True, 'url': url, 'filename': unique_name})


@bp.route('/api/medias')
def list_medias():
    """Liste tous les fichiers uploadés (images et vidéos)."""
    medias = []
    if os.path.isdir(UPLOAD_DIR):
        for fname in sorted(os.listdir(UPLOAD_DIR)):
            fpath = os.path.join(UPLOAD_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext in ALLOWED_EXTENSIONS:
                media_type = 'image'
            elif ext in ALLOWED_VIDEO_EXTENSIONS:
                media_type = 'video'
            else:
                continue
            size = os.path.getsize(fpath)
            medias.append({
                'filename': fname,
                'url': f'/static/uploads/{fname}',
                'type': media_type,
                'size': size,
            })
    return jsonify({'success': True, 'data': medias})


@bp.route('/api/medias/<filename>', methods=['DELETE'])
def delete_media(filename):
    """Supprime un fichier uploadé."""
    safe_name = secure_filename(filename)
    fpath = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.isfile(fpath):
        return jsonify({'error': 'Fichier non trouvé'}), 404
    os.remove(fpath)
    return jsonify({'success': True})
