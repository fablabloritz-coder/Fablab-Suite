"""
Fabtrack — Missions : gestion de tâches / missions du FabLab
Blueprint avec CRUD API + page kanban
"""

from flask import Blueprint, request, jsonify, render_template
from models import get_db

bp = Blueprint('missions', __name__, url_prefix='/missions')


# ── Pages HTML ──

@bp.route('/')
def missions_index():
    """Page kanban des missions."""
    return render_template('missions/index.html', page='missions')


# ── API JSON ──

@bp.route('/api/list')
def api_list():
    """Liste toutes les missions."""
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM missions ORDER BY statut, priorite DESC, ordre, id'
        ).fetchall()
        return jsonify({'success': True, 'data': [dict(r) for r in rows]})
    finally:
        db.close()


@bp.route('/api/create', methods=['POST'])
def api_create():
    """Crée une nouvelle mission."""
    data = request.get_json()
    if not data or not data.get('titre', '').strip():
        return jsonify({'success': False, 'error': "Le titre est requis"}), 400

    titre = data['titre'].strip()
    description = data.get('description', '').strip()
    statut = data.get('statut', 'a_faire')
    priorite = int(data.get('priorite', 0))
    ordre = int(data.get('ordre', 0))
    date_echeance = data.get('date_echeance') or None

    if statut not in ('a_faire', 'en_cours', 'termine'):
        return jsonify({'success': False, 'error': "Statut invalide"}), 400
    if priorite not in (0, 1, 2):
        return jsonify({'success': False, 'error': "Priorité invalide"}), 400

    db = get_db()
    try:
        c = db.execute(
            '''INSERT INTO missions (titre, description, statut, priorite, ordre, date_echeance)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (titre, description, statut, priorite, ordre, date_echeance)
        )
        db.commit()
        mission = db.execute('SELECT * FROM missions WHERE id = ?', (c.lastrowid,)).fetchone()
        return jsonify({'success': True, 'data': dict(mission)}), 201
    finally:
        db.close()


@bp.route('/api/<int:mission_id>', methods=['PUT'])
def api_update(mission_id):
    """Met à jour une mission."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Payload JSON requis'}), 400

    db = get_db()
    try:
        existing = db.execute('SELECT * FROM missions WHERE id = ?', (mission_id,)).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Mission non trouvée'}), 404

        titre = data.get('titre', existing['titre']).strip()
        if not titre:
            return jsonify({'success': False, 'error': "Le titre est requis"}), 400

        description = data.get('description', existing['description']).strip()
        statut = data.get('statut', existing['statut'])
        priorite = int(data.get('priorite', existing['priorite']))
        ordre = int(data.get('ordre', existing['ordre']))
        date_echeance = data.get('date_echeance', existing['date_echeance'])
        if date_echeance == '':
            date_echeance = None

        if statut not in ('a_faire', 'en_cours', 'termine'):
            return jsonify({'success': False, 'error': "Statut invalide"}), 400
        if priorite not in (0, 1, 2):
            return jsonify({'success': False, 'error': "Priorité invalide"}), 400

        db.execute(
            '''UPDATE missions
               SET titre = ?, description = ?, statut = ?, priorite = ?, ordre = ?,
                   date_echeance = ?, updated_at = datetime('now','localtime')
               WHERE id = ?''',
            (titre, description, statut, priorite, ordre, date_echeance, mission_id)
        )
        db.commit()
        mission = db.execute('SELECT * FROM missions WHERE id = ?', (mission_id,)).fetchone()
        return jsonify({'success': True, 'data': dict(mission)})
    finally:
        db.close()


@bp.route('/api/<int:mission_id>', methods=['DELETE'])
def api_delete(mission_id):
    """Supprime une mission."""
    db = get_db()
    try:
        existing = db.execute('SELECT id FROM missions WHERE id = ?', (mission_id,)).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Mission non trouvée'}), 404
        db.execute('DELETE FROM missions WHERE id = ?', (mission_id,))
        db.commit()
        return jsonify({'success': True})
    finally:
        db.close()
