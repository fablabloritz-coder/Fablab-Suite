"""
Fabtrack — Missions : gestion de tâches / missions du FabLab
Blueprint avec CRUD API + page kanban
"""

import math

from flask import Blueprint, request, jsonify, render_template
from models import get_db

bp = Blueprint('missions', __name__, url_prefix='/missions')

STATUTS_VALIDES = ('a_faire', 'en_cours', 'termine')


def _parse_int(value, default, min_value=None, max_value=None):
    """Parse un entier avec bornes optionnelles."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if min_value is not None:
        parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


def _log_mission_event(db, mission_id, event_type, from_statut=None, to_statut=None):
    """Insère un événement de mission dans l'historique."""
    db.execute(
        '''INSERT INTO mission_events (mission_id, event_type, from_statut, to_statut)
           VALUES (?, ?, ?, ?)''',
        (mission_id, event_type, from_statut, to_statut),
    )


def _timeline_select_sql():
    """Extrait SQL réutilisable des dates de timeline d'une mission."""
    return '''
        (SELECT MIN(e.created_at)
         FROM mission_events e
         WHERE e.mission_id = m.id AND e.event_type = 'created') AS timeline_created_at,
        (SELECT MIN(e.created_at)
         FROM mission_events e
         WHERE e.mission_id = m.id AND e.to_statut = 'en_cours') AS timeline_started_at,
        (SELECT MIN(e.created_at)
         FROM mission_events e
         WHERE e.mission_id = m.id AND e.to_statut = 'termine') AS timeline_finished_at,
        COALESCE(
            (SELECT MAX(e.created_at)
             FROM mission_events e
             WHERE e.mission_id = m.id AND e.to_statut = 'termine'),
            m.updated_at,
            m.created_at
        ) AS completed_at
    '''


def _fetch_mission_with_timeline(db, mission_id):
    """Retourne une mission enrichie avec ses dates de timeline."""
    return db.execute(
        f'''
        SELECT m.*, {_timeline_select_sql()}
        FROM missions m
        WHERE m.id = ?
        ''',
        (mission_id,),
    ).fetchone()


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
            f'''
            SELECT m.*, {_timeline_select_sql()}
            FROM missions m
            ORDER BY
                CASE m.statut
                    WHEN 'a_faire' THEN 0
                    WHEN 'en_cours' THEN 1
                    ELSE 2
                END,
                CASE
                    WHEN m.statut = 'termine' THEN datetime(completed_at)
                    ELSE NULL
                END DESC,
                m.priorite DESC,
                m.ordre,
                m.id
            '''
        ).fetchall()
        return jsonify({'success': True, 'data': [dict(r) for r in rows]})
    finally:
        db.close()


@bp.route('/api/history')
def api_history():
    """Historique paginé des missions terminées avec timeline."""
    page = _parse_int(request.args.get('page'), default=1, min_value=1)
    page_size = _parse_int(request.args.get('page_size'), default=10, min_value=1, max_value=50)

    db = get_db()
    try:
        total_row = db.execute(
            "SELECT COUNT(*) AS cnt FROM missions WHERE statut = 'termine'"
        ).fetchone()
        total = int(total_row['cnt']) if total_row else 0
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * page_size
        rows = db.execute(
            f'''
            SELECT m.*, {_timeline_select_sql()}
            FROM missions m
            WHERE m.statut = 'termine'
            ORDER BY datetime(completed_at) DESC, m.id DESC
            LIMIT ? OFFSET ?
            ''',
            (page_size, offset),
        ).fetchall()

        return jsonify({
            'success': True,
            'data': [dict(r) for r in rows],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': total_pages,
            },
        })
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

    if statut not in STATUTS_VALIDES:
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
        mission_id = c.lastrowid
        _log_mission_event(db, mission_id, 'created', None, 'a_faire')

        # Si la mission est créée directement avancée, journaliser la transition.
        if statut == 'en_cours':
            _log_mission_event(db, mission_id, 'status_changed', 'a_faire', 'en_cours')
        elif statut == 'termine':
            _log_mission_event(db, mission_id, 'status_changed', 'a_faire', 'en_cours')
            _log_mission_event(db, mission_id, 'status_changed', 'en_cours', 'termine')

        db.commit()
        mission = _fetch_mission_with_timeline(db, mission_id)
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

        titre = data.get('titre', existing['titre'])
        titre = titre.strip() if isinstance(titre, str) else str(titre).strip()
        if not titre:
            return jsonify({'success': False, 'error': "Le titre est requis"}), 400

        description = data.get('description', existing['description'])
        description = description.strip() if isinstance(description, str) else ''
        statut = data.get('statut', existing['statut'])
        priorite = int(data.get('priorite', existing['priorite']))
        ordre = int(data.get('ordre', existing['ordre']))
        date_echeance = data.get('date_echeance', existing['date_echeance'])
        if date_echeance == '':
            date_echeance = None

        if statut not in STATUTS_VALIDES:
            return jsonify({'success': False, 'error': "Statut invalide"}), 400
        if priorite not in (0, 1, 2):
            return jsonify({'success': False, 'error': "Priorité invalide"}), 400

        old_statut = existing['statut']

        db.execute(
            '''UPDATE missions
               SET titre = ?, description = ?, statut = ?, priorite = ?, ordre = ?,
                   date_echeance = ?, updated_at = datetime('now','localtime')
               WHERE id = ?''',
            (titre, description, statut, priorite, ordre, date_echeance, mission_id)
        )

        if old_statut != statut:
            _log_mission_event(db, mission_id, 'status_changed', old_statut, statut)

        db.commit()
        mission = _fetch_mission_with_timeline(db, mission_id)
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
