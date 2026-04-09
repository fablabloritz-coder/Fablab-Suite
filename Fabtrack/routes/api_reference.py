"""Routes API référentiels — CRUD types_activite, machines, materiaux, classes, referents, preparateurs."""

import sqlite3

from flask import Blueprint, request, jsonify
from models import get_db, init_db

bp = Blueprint('api_reference', __name__)


def rows_to_list(rows):
    return [dict(r) for r in rows]


def _resolve_nom(db, table, id_val):
    """Résout un nom à partir d'un ID dans une table de référence."""
    ALLOWED = {'preparateurs', 'types_activite', 'machines', 'classes', 'referents', 'materiaux'}
    if not id_val or table not in ALLOWED:
        return ''
    row = db.execute(f'SELECT nom FROM {table} WHERE id=?', (id_val,)).fetchone()
    return row['nom'] if row else ''


# ── Données de référence ──

@bp.route('/api/reference')
def api_reference():
    db = get_db()
    try:
        try:
            return jsonify({
                'preparateurs':  rows_to_list(db.execute('SELECT * FROM preparateurs WHERE actif=1 ORDER BY nom').fetchall()),
                'types_activite':rows_to_list(db.execute('SELECT * FROM types_activite WHERE actif=1 ORDER BY id').fetchall()),
                'machines':      rows_to_list(db.execute('SELECT * FROM machines WHERE actif=1 ORDER BY type_activite_id, nom').fetchall()),
                'materiaux':     rows_to_list(db.execute('SELECT * FROM materiaux WHERE actif=1 ORDER BY nom').fetchall()),
                'machine_type_activite': rows_to_list(db.execute('SELECT * FROM machine_type_activite').fetchall()),
                'machine_type_materiau': rows_to_list(db.execute('SELECT * FROM machine_type_materiau').fetchall()),
                'classes':       rows_to_list(db.execute('SELECT * FROM classes WHERE actif=1 ORDER BY nom').fetchall()),
                'referents':     rows_to_list(db.execute('SELECT * FROM referents WHERE actif=1 ORDER BY categorie, nom').fetchall()),
            })
        except sqlite3.OperationalError as e:
            # Auto-répare les cas "no such table" observés après certaines réinitialisations.
            if 'no such table' not in str(e).lower():
                raise
            db.close()
            init_db()
            db2 = get_db()
            try:
                return jsonify({
                    'preparateurs':  rows_to_list(db2.execute('SELECT * FROM preparateurs WHERE actif=1 ORDER BY nom').fetchall()),
                    'types_activite':rows_to_list(db2.execute('SELECT * FROM types_activite WHERE actif=1 ORDER BY id').fetchall()),
                    'machines':      rows_to_list(db2.execute('SELECT * FROM machines WHERE actif=1 ORDER BY type_activite_id, nom').fetchall()),
                    'materiaux':     rows_to_list(db2.execute('SELECT * FROM materiaux WHERE actif=1 ORDER BY nom').fetchall()),
                    'machine_type_activite': rows_to_list(db2.execute('SELECT * FROM machine_type_activite').fetchall()),
                    'machine_type_materiau': rows_to_list(db2.execute('SELECT * FROM machine_type_materiau').fetchall()),
                    'classes':       rows_to_list(db2.execute('SELECT * FROM classes WHERE actif=1 ORDER BY nom').fetchall()),
                    'referents':     rows_to_list(db2.execute('SELECT * FROM referents WHERE actif=1 ORDER BY categorie, nom').fetchall()),
                })
            finally:
                db2.close()
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── Types d'activité ──

@bp.route('/api/types_activite', methods=['POST'])
def api_add_type_activite():
    data = request.get_json(); db = get_db()
    try:
        nom = data['nom'].strip()
        cur = db.execute(
            'INSERT OR IGNORE INTO types_activite (nom,icone,couleur,badge_class,unite_defaut,image_path) VALUES (?,?,?,?,?,?)',
            (nom, data.get('icone','🔧'), data.get('couleur','#6b7280'),
             data.get('badge_class',''), data.get('unite_defaut',''), data.get('image_path','')))
        db.commit()
        rid = cur.lastrowid
        if not rid:
            row = db.execute('SELECT id FROM types_activite WHERE nom=?', (nom,)).fetchone()
            rid = row['id'] if row else 0
        return jsonify({'success':True,'id':rid})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/types_activite/<int:id>', methods=['PUT'])
def api_update_type_activite(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('UPDATE types_activite SET nom=?,icone=?,couleur=?,badge_class=?,unite_defaut=?,image_path=? WHERE id=?',
                   (data['nom'].strip(), data.get('icone',''), data.get('couleur',''),
                    data.get('badge_class',''), data.get('unite_defaut',''), data.get('image_path',''), id))
        db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/types_activite/<int:id>', methods=['DELETE'])
def api_delete_type_activite(id):
    db = get_db()
    try:
        db.execute('UPDATE types_activite SET actif=0 WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


# ── Machines ──

@bp.route('/api/machines', methods=['POST'])
def api_add_machine():
    data = request.get_json(); db = get_db()
    try:
        cur = db.execute(
            'INSERT INTO machines (nom,type_activite_id,quantite,marque,zone_travail,puissance,description,image_path,principes_conception) VALUES (?,?,?,?,?,?,?,?,?)',
            (data['nom'].strip(), data['type_activite_id'],
             int(data.get('quantite',1) or 1),
             data.get('marque','').strip(), data.get('zone_travail','').strip(),
             data.get('puissance','').strip(), data.get('description','').strip(),
             data.get('image_path',''), data.get('principes_conception','')))
        machine_id = cur.lastrowid
        db.execute(
            'INSERT OR IGNORE INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)',
            (machine_id, int(data['type_activite_id']))
        )
        db.commit()
        return jsonify({'success':True,'id':machine_id})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/machines/<int:id>', methods=['PUT'])
def api_update_machine(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('''UPDATE machines SET nom=?,type_activite_id=?,quantite=?,
                      marque=?,zone_travail=?,puissance=?,description=?,statut=?,image_path=?,principes_conception=? WHERE id=?''',
                   (data['nom'].strip(), data['type_activite_id'],
                    int(data.get('quantite',1) or 1),
                    data.get('marque','').strip(), data.get('zone_travail','').strip(),
                    data.get('puissance','').strip(), data.get('description','').strip(),
                    data.get('statut','disponible'), data.get('image_path',''),
                    data.get('principes_conception',''), id))
        db.execute(
            'INSERT OR IGNORE INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)',
            (id, int(data['type_activite_id']))
        )
        db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/machines/<int:id>', methods=['DELETE'])
def api_delete_machine(id):
    db = get_db()
    try:
        db.execute('UPDATE machines SET actif=0 WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


# ── Matériaux ──

@bp.route('/api/materiaux', methods=['POST'])
def api_add_materiau():
    data = request.get_json(); db = get_db()
    try:
        cur = db.execute('INSERT INTO materiaux (nom,unite,image_path) VALUES (?,?,?)',
                         (data['nom'].strip(), data.get('unite',''), data.get('image_path','')))
        mat_id = cur.lastrowid
        db.commit()
        return jsonify({'success':True,'id':mat_id})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/materiaux/<int:id>', methods=['DELETE'])
def api_delete_materiau(id):
    db = get_db()
    try:
        db.execute('UPDATE materiaux SET actif=0 WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/materiaux/<int:id>', methods=['PUT'])
def api_update_materiau(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('UPDATE materiaux SET nom=?,unite=?,image_path=? WHERE id=?',
                   (data['nom'].strip(), data.get('unite',''),
                    data.get('image_path',''), id))
        db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


@bp.route('/api/compat/matrix-cell', methods=['PUT'])
def api_update_matrix_cell():
    data = request.get_json() or {}
    db = get_db()
    try:
        type_id = int(data.get('type_id') or 0)
        machine_id = int(data.get('machine_id') or 0)
        compatible = bool(data.get('compatible', False))
        material_ids = [int(mid) for mid in (data.get('material_ids') or [])]

        if not type_id or not machine_id:
            return jsonify({'success': False, 'error': 'type_id et machine_id requis'}), 400

        # Compatibilité machine <-> type
        if compatible:
            db.execute(
                'INSERT OR IGNORE INTO machine_type_activite (machine_id, type_activite_id) VALUES (?, ?)',
                (machine_id, type_id)
            )
        else:
            db.execute(
                'DELETE FROM machine_type_activite WHERE machine_id=? AND type_activite_id=?',
                (machine_id, type_id)
            )
            db.execute(
                'DELETE FROM machine_type_materiau WHERE machine_id=? AND type_activite_id=?',
                (machine_id, type_id)
            )
            db.commit()
            return jsonify({'success': True})

        # Matériaux dédiés à cette cellule
        db.execute(
            'DELETE FROM machine_type_materiau WHERE machine_id=? AND type_activite_id=?',
            (machine_id, type_id)
        )
        for mat_id in material_ids:
            db.execute(
                'INSERT OR IGNORE INTO machine_type_materiau (machine_id, type_activite_id, materiau_id) VALUES (?, ?, ?)',
                (machine_id, type_id, mat_id)
            )

        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


# ── Classes ──

@bp.route('/api/classes', methods=['POST'])
def api_add_classe():
    data = request.get_json(); db = get_db()
    try:
        nom = data['nom'].strip()
        cur = db.execute('INSERT OR IGNORE INTO classes (nom,image_path) VALUES (?,?)', (nom, data.get('image_path','')))
        db.commit()
        rid = cur.lastrowid
        if not rid:
            row = db.execute('SELECT id FROM classes WHERE nom=?', (nom,)).fetchone()
            rid = row['id'] if row else 0
        return jsonify({'success':True,'id':rid})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/classes/<int:id>', methods=['DELETE'])
def api_delete_classe(id):
    db = get_db()
    try:
        db.execute('UPDATE classes SET actif=0 WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/classes/<int:id>', methods=['PUT'])
def api_update_classe(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('UPDATE classes SET nom=?,image_path=? WHERE id=?',
                   (data['nom'].strip(), data.get('image_path',''), id))
        db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


# ── Référents ──

@bp.route('/api/referents', methods=['POST'])
def api_add_referent():
    data = request.get_json(); db = get_db()
    try:
        nom = data['nom'].strip()
        cat = data.get('categorie','Professeur').strip() or 'Professeur'
        cur = db.execute('INSERT OR IGNORE INTO referents (nom,categorie,image_path) VALUES (?,?,?)',
                         (nom, cat, data.get('image_path','')))
        db.commit()
        rid = cur.lastrowid
        if not rid:
            row = db.execute('SELECT id FROM referents WHERE nom=?', (nom,)).fetchone()
            rid = row['id'] if row else 0
        return jsonify({'success':True,'id':rid})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/referents/<int:id>', methods=['PUT'])
def api_update_referent(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('UPDATE referents SET nom=?,categorie=?,image_path=? WHERE id=?',
                   (data['nom'].strip(), data.get('categorie','Professeur'), data.get('image_path',''), id))
        db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/referents/<int:id>', methods=['DELETE'])
def api_delete_referent(id):
    db = get_db()
    try:
        db.execute('UPDATE referents SET actif=0 WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


# ── Préparateurs ──

@bp.route('/api/preparateurs', methods=['POST'])
def api_add_preparateur():
    data = request.get_json(); db = get_db()
    try:
        nom = data['nom'].strip()
        cur = db.execute('INSERT OR IGNORE INTO preparateurs (nom,image_path) VALUES (?,?)', (nom, data.get('image_path','')))
        db.commit()
        rid = cur.lastrowid
        if not rid:
            row = db.execute('SELECT id FROM preparateurs WHERE nom=?', (nom,)).fetchone()
            rid = row['id'] if row else 0
        return jsonify({'success':True,'id':rid})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/preparateurs/<int:id>', methods=['DELETE'])
def api_delete_preparateur(id):
    db = get_db()
    try:
        db.execute('UPDATE preparateurs SET actif=0 WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()

@bp.route('/api/preparateurs/<int:id>', methods=['PUT'])
def api_update_preparateur(id):
    data = request.get_json(); db = get_db()
    try:
        db.execute('UPDATE preparateurs SET nom=?,image_path=? WHERE id=?',
                   (data['nom'].strip(), data.get('image_path',''), id))
        db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


# ── Vérification dépendances avant suppression ──

ENTITY_FK_MAP = {
    'machines': 'machine_id',
    'types_activite': 'type_activite_id',
    'materiaux': 'materiau_id',
    'classes': 'classe_id',
    'referents': 'referent_id',
    'preparateurs': 'preparateur_id',
}

@bp.route('/api/<entity>/<int:id>/usage-count')
def api_usage_count(entity, id):
    """Compte le nombre de consommations utilisant cet élément."""
    col = ENTITY_FK_MAP.get(entity)
    if not col:
        return jsonify({'error': 'Entité inconnue'}), 400
    db = get_db()
    try:
        count = db.execute(f'SELECT COUNT(*) as n FROM consommations WHERE {col}=?', (id,)).fetchone()['n']
        return jsonify({'count': count, 'entity': entity, 'id': id})
    finally:
        db.close()

@bp.route('/api/<entity>/<int:id>/replace-and-delete', methods=['POST'])
def api_replace_and_delete(entity, id):
    """Remplace toutes les références puis désactive l'élément."""
    col = ENTITY_FK_MAP.get(entity)
    if not col:
        return jsonify({'success': False, 'error': 'Entité inconnue'}), 400
    data = request.get_json()
    replacement_id = data.get('replacement_id')
    db = get_db()
    try:
        nom_col_map = {
            'preparateur_id': 'nom_preparateur', 'type_activite_id': 'nom_type_activite',
            'machine_id': 'nom_machine', 'classe_id': 'nom_classe',
            'referent_id': 'nom_referent', 'materiau_id': 'nom_materiau',
        }
        nom_col = nom_col_map.get(col)
        if replacement_id:
            new_nom = _resolve_nom(db, entity, replacement_id) if nom_col else ''
            if nom_col:
                db.execute(f'UPDATE consommations SET {col}=?, {nom_col}=? WHERE {col}=?',
                           (replacement_id, new_nom, id))
            else:
                db.execute(f'UPDATE consommations SET {col}=? WHERE {col}=?', (replacement_id, id))
        else:
            db.execute(f'UPDATE consommations SET {col}=NULL WHERE {col}=?', (id,))
        db.execute(f'UPDATE {entity} SET actif=0 WHERE id=?', (id,))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


# ── Suppression de masse ──

@bp.route('/api/<entity>/mass-delete', methods=['POST'])
def api_mass_delete(entity):
    """Supprime (désactive) plusieurs éléments à la fois."""
    allowed = {'machines','materiaux','classes','referents','preparateurs','types_activite'}
    if entity not in allowed:
        return jsonify({'success':False,'error':'Entité inconnue'}), 400

    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'success':False,'error':'Aucun ID fourni'}), 400

    db = get_db()
    try:
        placeholders = ','.join('?' * len(ids))
        db.execute(f'UPDATE {entity} SET actif=0 WHERE id IN ({placeholders})', ids)
        db.commit()
        return jsonify({'success':True,'count':len(ids)})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()
