"""Routes API consommations — CRUD, batch, statistiques, export/import CSV."""

from flask import Blueprint, request, jsonify, Response
from models import get_db
from routes.api_reference import rows_to_list, _resolve_nom
from datetime import datetime
import csv, io

bp = Blueprint('api_consommations', __name__)


def _to_float(value):
    try:
        if value is None or value == '':
            return None
        if isinstance(value, str):
            value = value.replace(',', '.').strip()
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_unit(unit):
    return (unit or '').strip().lower().replace(' ', '')


def _to_bool_int(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) != 0 else 0
    text = str(value or '').strip().lower()
    return 1 if text in ('1', 'true', 'oui', 'yes', 'on') else 0


def _surface_from_action(action):
    surface = _to_float(action.get('surface_m2'))
    if surface is not None:
        return surface
    longueur_mm = _to_float(action.get('longueur_mm'))
    largeur_mm = _to_float(action.get('largeur_mm'))
    if longueur_mm and largeur_mm:
        return (longueur_mm * largeur_mm) / 1e6
    return None


def _machine_is_compatible_with_type(db, machine_id, type_activite_id):
    if not machine_id or not type_activite_id:
        return True
    row = db.execute(
        'SELECT 1 FROM machine_type_activite WHERE machine_id=? AND type_activite_id=?',
        (machine_id, type_activite_id)
    ).fetchone()
    return bool(row)


def _machine_ids_for_type(db, type_activite_id):
    if not type_activite_id:
        return set()
    return {
        r['machine_id'] for r in db.execute(
            'SELECT machine_id FROM machine_type_activite WHERE type_activite_id=?',
            (type_activite_id,)
        ).fetchall()
    }


def _material_is_compatible_with_selection(db, type_activite_id, machine_id, materiau_id):
    if not materiau_id:
        return True

    generic_match = db.execute(
                '''SELECT 1
                     FROM materiaux
                     WHERE id=?
                         AND id NOT IN (SELECT materiau_id FROM materiau_machine)''',
        (materiau_id,)
    ).fetchone()
    if generic_match:
        return True

    if machine_id:
        # Nouveau modèle : cellule type+machine+materiau explicite prioritaire si définie
        if type_activite_id:
            cell_rows = db.execute(
                'SELECT materiau_id FROM machine_type_materiau WHERE machine_id=? AND type_activite_id=?',
                (machine_id, type_activite_id)
            ).fetchall()
            if cell_rows:
                return materiau_id in {r['materiau_id'] for r in cell_rows}

        row = db.execute(
            'SELECT 1 FROM materiau_machine WHERE machine_id=? AND materiau_id=?',
            (machine_id, materiau_id)
        ).fetchone()
        return bool(row)

    if type_activite_id:
        machine_ids = _machine_ids_for_type(db, type_activite_id)
        if not machine_ids:
            return False

        # Matériau autorisé si présent dans au moins une cellule type+machine
        placeholders = ','.join(['?'] * len(machine_ids))
        cell_row = db.execute(
            f'''SELECT 1
                FROM machine_type_materiau
                WHERE type_activite_id=?
                  AND machine_id IN ({placeholders})
                  AND materiau_id=?
                LIMIT 1''',
            [type_activite_id, *machine_ids, materiau_id]
        ).fetchone()
        if cell_row:
            return True

        # Fallback : via liaison machine<->materiau
        row = db.execute(
            f'''SELECT 1
                FROM materiau_machine mm
                WHERE mm.materiau_id=? AND mm.machine_id IN ({placeholders})
            ''',
            [materiau_id, *machine_ids]
        ).fetchone()
        return bool(row)

    return False


def _validate_action_selection(db, action):
    type_activite_id = action.get('type_activite_id')
    machine_id = action.get('machine_id') or None
    materiau_id = action.get('materiau_id') or None

    if not _machine_is_compatible_with_type(db, machine_id, type_activite_id):
        return 'Machine incompatible avec le type d\'activité choisi.'

    if not _material_is_compatible_with_selection(db, type_activite_id, machine_id, materiau_id):
        return 'Matériau incompatible avec la machine ou le type d\'activité choisi.'

    return None


def _consumed_qty_for_unit(action, stock_unit):
    """Retourne la quantité consommée dans l'unité de l'article stock."""
    poids_g = _to_float(action.get('poids_grammes'))
    surface_m2 = _surface_from_action(action)
    nb_feuilles = _to_float(action.get('nb_feuilles'))
    nb_feuilles_pl = _to_float(action.get('nb_feuilles_plastique'))
    quantite = _to_float(action.get('quantite'))

    unit = _normalize_unit(stock_unit)

    if unit in ('g', 'gr', 'gramme', 'grammes') and poids_g and poids_g > 0:
        return poids_g
    if unit in ('kg', 'kilogramme', 'kilogrammes') and poids_g and poids_g > 0:
        return poids_g / 1000.0
    if unit in ('m²', 'm2') and surface_m2 and surface_m2 > 0:
        return surface_m2
    if unit in ('cm²', 'cm2') and surface_m2 and surface_m2 > 0:
        return surface_m2 * 10000.0
    if 'feuille' in unit:
        if nb_feuilles and nb_feuilles > 0:
            return nb_feuilles
        if nb_feuilles_pl and nb_feuilles_pl > 0:
            return nb_feuilles_pl

    if quantite and quantite > 0:
        return quantite

    # Fallback volontairement permissif: on privilégie une estimation plutôt qu'un blocage.
    for candidate in (poids_g, surface_m2, nb_feuilles, nb_feuilles_pl):
        if candidate and candidate > 0:
            return candidate

    return 0.0


def _decrease_stock_from_action(db, consommation_id, action):
    """Décrémente le stock lié au matériau consommé; ne bloque jamais la saisie."""
    materiau_id = action.get('materiau_id')
    try:
        materiau_id = int(materiau_id)
    except (ValueError, TypeError):
        return False

    article = db.execute('''
        SELECT id, nom, unite, quantite_actuelle
        FROM stock_articles
        WHERE actif=1 AND materiau_id=?
        ORDER BY quantite_actuelle DESC, id ASC
        LIMIT 1
    ''', (materiau_id,)).fetchone()
    if not article:
        return False

    qty = _consumed_qty_for_unit(action, article['unite'])
    if qty <= 0:
        return False

    avant = float(article['quantite_actuelle'] or 0)
    apres = avant - qty
    note = f"Consommation #{consommation_id}"
    commentaire = (action.get('commentaire') or '').strip()
    if commentaire:
        note += f" — {commentaire[:120]}"

    db.execute('''
        INSERT INTO stock_mouvements
        (article_id, type, quantite, quantite_avant, quantite_apres, source, notes)
        VALUES (?, 'sortie', ?, ?, ?, 'consommation', ?)
    ''', (article['id'], qty, avant, apres, note))

    db.execute(
        "UPDATE stock_articles SET quantite_actuelle=?, date_modification=datetime('now','localtime') WHERE id=?",
        (apres, article['id'])
    )
    return True


# ── CRUD Consommations ──

@bp.route('/api/consommations', methods=['GET'])
def api_get_consommations():
    db = get_db()
    try:
        date_debut = request.args.get('date_debut','')
        date_fin   = request.args.get('date_fin','')
        type_activite_id = request.args.get('type_activite_id','')
        preparateur_id   = request.args.get('preparateur_id','')
        classe_id  = request.args.get('classe_id','')
        referent_id= request.args.get('referent_id','')
        page     = max(1, int(request.args.get('page',1) or 1))
        per_page = min(max(1, int(request.args.get('per_page',50) or 50)), 10000)

        query = '''
            SELECT c.*,
                   COALESCE(p.nom, c.nom_preparateur) as preparateur_nom,
                   COALESCE(t.nom, c.nom_type_activite) as type_activite_nom,
                   t.icone as type_icone, t.badge_class,
                   COALESCE(m.nom, c.nom_machine) as machine_nom,
                   COALESCE(cl.nom, c.nom_classe) as classe_nom,
                   COALESCE(r.nom, c.nom_referent) as referent_nom, r.categorie as referent_categorie,
                     COALESCE(mat.nom, c.nom_materiau) as materiau_nom,
                     mat.unite as materiau_unite,
                     mat.image_path as materiau_image_path
            FROM consommations c
            LEFT JOIN preparateurs p ON c.preparateur_id=p.id
            LEFT JOIN types_activite t ON c.type_activite_id=t.id
            LEFT JOIN machines m ON c.machine_id=m.id
            LEFT JOIN classes cl ON c.classe_id=cl.id
            LEFT JOIN referents r ON c.referent_id=r.id
            LEFT JOIN materiaux mat ON c.materiau_id=mat.id
            WHERE 1=1
        '''
        params = []
        count_q = 'SELECT COUNT(*) as total FROM consommations c WHERE 1=1'
        cp = []

        for col, val, cast in [
            ('c.date_saisie >=', date_debut, str),
            ('c.date_saisie <=', date_fin + ' 23:59:59' if date_fin and len(date_fin) == 10 else date_fin, str),
            ('c.type_activite_id =', type_activite_id, int),
            ('c.preparateur_id =', preparateur_id, int),
            ('c.classe_id =', classe_id, int),
            ('c.referent_id =', referent_id, int),
        ]:
            if val:
                query += f' AND {col} ?'; params.append(cast(val))
                count_q += f' AND {col} ?'; cp.append(cast(val))

        total = db.execute(count_q, cp).fetchone()['total']
        query += ' ORDER BY c.date_saisie DESC, c.created_at DESC LIMIT ? OFFSET ?'
        params.extend([per_page, (page-1)*per_page])

        return jsonify({
            'data': rows_to_list(db.execute(query, params).fetchall()),
            'total': total, 'page': page, 'per_page': per_page,
            'pages': max(1, (total + per_page - 1) // per_page),
        })
    finally:
        db.close()


@bp.route('/api/consommations', methods=['POST'])
def api_create_consommation():
    data = request.get_json(); db = get_db()
    try:
        validation_error = _validate_action_selection(db, data)
        if validation_error:
            return jsonify({'success': False, 'error': validation_error}), 400

        surface = _surface_from_action(data)

        nom_prep = _resolve_nom(db, 'preparateurs', data.get('preparateur_id'))
        nom_type = _resolve_nom(db, 'types_activite', data.get('type_activite_id'))
        nom_mach = _resolve_nom(db, 'machines', data.get('machine_id'))
        nom_cls  = _resolve_nom(db, 'classes', data.get('classe_id'))
        nom_ref  = _resolve_nom(db, 'referents', data.get('referent_id'))
        nom_mat  = _resolve_nom(db, 'materiaux', data.get('materiau_id'))

        cur = db.execute('''
            INSERT INTO consommations (
                date_saisie, preparateur_id, type_activite_id, machine_id,
                classe_id, referent_id, materiau_id,
                nom_preparateur, nom_type_activite, nom_machine, nom_classe, nom_referent, nom_materiau,
                quantite, unite,
                poids_grammes, longueur_mm, largeur_mm, surface_m2, epaisseur,
                nb_feuilles, format_papier,
                nb_feuilles_plastique, type_feuille, commentaire,
                impression_couleur, projet_nom, projet_personnel
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            data.get('date_saisie', datetime.now().strftime('%Y-%m-%d %H:%M')),
            data.get('preparateur_id'), data.get('type_activite_id'),
            data.get('machine_id') or None, data.get('classe_id') or None,
            data.get('referent_id') or None,
            data.get('materiau_id') or None,
            nom_prep, nom_type, nom_mach, nom_cls, nom_ref, nom_mat,
            data.get('quantite') or 0, data.get('unite',''),
            data.get('poids_grammes') or None,
            data.get('longueur_mm') or None, data.get('largeur_mm') or None,
            surface or None,
            data.get('epaisseur') or None,
            data.get('nb_feuilles') or None, data.get('format_papier') or None,
            data.get('nb_feuilles_plastique') or None,
            data.get('type_feuille') or None, data.get('commentaire',''),
            data.get('impression_couleur',''),
            data.get('projet_nom',''),
            _to_bool_int(data.get('projet_personnel', 0)),
        ))

        # Synchronisation stock non bloquante (on autorise les stocks négatifs).
        try:
            _decrease_stock_from_action(db, cur.lastrowid, data)
        except Exception:
            pass

        db.commit()
        return jsonify({'success':True,'id':cur.lastrowid}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


@bp.route('/api/consommations/batch', methods=['POST'])
def api_create_consommation_batch():
    """Crée plusieurs consommations en une seule requête (multi-action saisie)."""
    data = request.get_json()
    actions = data.get('actions', [])
    if not actions:
        return jsonify({'success': False, 'error': 'Aucune action fournie'}), 400

    common = {
        'date_saisie': data.get('date_saisie', datetime.now().strftime('%Y-%m-%d %H:%M')),
        'preparateur_id': data.get('preparateur_id'),
        'classe_id': data.get('classe_id'),
        'referent_id': data.get('referent_id'),
        'projet_nom': data.get('projet_nom', ''),
        'projet_personnel': _to_bool_int(data.get('projet_personnel', 0)),
    }

    db = get_db()
    ids = []
    try:
        nom_prep = _resolve_nom(db, 'preparateurs', common['preparateur_id'])
        nom_cls  = _resolve_nom(db, 'classes', common['classe_id'])
        nom_ref  = _resolve_nom(db, 'referents', common['referent_id'])

        for index, action in enumerate(actions, start=1):
            validation_error = _validate_action_selection(db, action)
            if validation_error:
                db.rollback()
                return jsonify({'success': False, 'error': f'Action {index}: {validation_error}'}), 400

            surface = _surface_from_action(action)

            nom_type = _resolve_nom(db, 'types_activite', action.get('type_activite_id'))
            nom_mach = _resolve_nom(db, 'machines', action.get('machine_id'))
            nom_mat  = _resolve_nom(db, 'materiaux', action.get('materiau_id'))

            cur = db.execute('''
                INSERT INTO consommations (
                    date_saisie, preparateur_id, type_activite_id, machine_id,
                    classe_id, referent_id, materiau_id,
                    nom_preparateur, nom_type_activite, nom_machine, nom_classe, nom_referent, nom_materiau,
                    quantite, unite,
                    poids_grammes, longueur_mm, largeur_mm, surface_m2, epaisseur,
                    nb_feuilles, format_papier,
                    nb_feuilles_plastique, type_feuille, commentaire,
                    impression_couleur, projet_nom, projet_personnel
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                common['date_saisie'], common['preparateur_id'],
                action.get('type_activite_id'), action.get('machine_id') or None,
                common.get('classe_id') or None, common.get('referent_id') or None,
                action.get('materiau_id') or None,
                nom_prep, nom_type, nom_mach, nom_cls, nom_ref, nom_mat,
                action.get('quantite') or 0, action.get('unite', ''),
                action.get('poids_grammes') or None,
                action.get('longueur_mm') or None, action.get('largeur_mm') or None,
                surface or None,
                action.get('epaisseur') or None,
                action.get('nb_feuilles') or None, action.get('format_papier') or None,
                action.get('nb_feuilles_plastique') or None,
                action.get('type_feuille') or None, action.get('commentaire', ''),
                action.get('impression_couleur', ''), common['projet_nom'], common['projet_personnel'],
            ))
            conso_id = cur.lastrowid
            ids.append(conso_id)

            # Synchronisation stock non bloquante (on autorise les stocks négatifs).
            try:
                _decrease_stock_from_action(db, conso_id, action)
            except Exception:
                pass

        db.commit()
        return jsonify({'success': True, 'ids': ids, 'count': len(ids)}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    finally:
        db.close()


@bp.route('/api/consommations/<int:id>', methods=['DELETE'])
def api_delete_consommation(id):
    db = get_db()
    try:
        db.execute('DELETE FROM consommations WHERE id=?',(id,)); db.commit()
        return jsonify({'success':True})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


@bp.route('/api/consommations/<int:id>', methods=['PUT'])
def api_update_consommation(id):
    data = request.get_json(); db = get_db()
    try:
        validation_error = _validate_action_selection(db, data)
        if validation_error:
            return jsonify({'success': False, 'error': validation_error}), 400

        surface = _surface_from_action(data)

        nom_prep = _resolve_nom(db, 'preparateurs', data.get('preparateur_id'))
        nom_type = _resolve_nom(db, 'types_activite', data.get('type_activite_id'))
        nom_mach = _resolve_nom(db, 'machines', data.get('machine_id'))
        nom_cls  = _resolve_nom(db, 'classes', data.get('classe_id'))
        nom_ref  = _resolve_nom(db, 'referents', data.get('referent_id'))
        nom_mat  = _resolve_nom(db, 'materiaux', data.get('materiau_id'))

        db.execute('''
            UPDATE consommations SET
                date_saisie=?, preparateur_id=?, type_activite_id=?, machine_id=?,
                classe_id=?, referent_id=?, materiau_id=?,
                nom_preparateur=?, nom_type_activite=?, nom_machine=?, nom_classe=?, nom_referent=?, nom_materiau=?,
                quantite=?, unite=?,
                poids_grammes=?, longueur_mm=?, largeur_mm=?, surface_m2=?, epaisseur=?,
                nb_feuilles=?, format_papier=?,
                nb_feuilles_plastique=?, type_feuille=?, commentaire=?,
                impression_couleur=?,
                projet_nom=?,
                projet_personnel=?,
                updated_at=datetime('now','localtime')
            WHERE id=?
        ''', (
            data.get('date_saisie'), data.get('preparateur_id'),
            data.get('type_activite_id'), data.get('machine_id') or None,
            data.get('classe_id') or None, data.get('referent_id') or None,
            data.get('materiau_id') or None,
            nom_prep, nom_type, nom_mach, nom_cls, nom_ref, nom_mat,
            data.get('quantite') or 0, data.get('unite',''),
            data.get('poids_grammes') or None,
            data.get('longueur_mm') or None, data.get('largeur_mm') or None,
            surface or None,
            data.get('epaisseur') or None,
            data.get('nb_feuilles') or None, data.get('format_papier') or None,
            data.get('nb_feuilles_plastique') or None,
            data.get('type_feuille') or None, data.get('commentaire',''),
            data.get('impression_couleur',''),
            data.get('projet_nom',''),
            _to_bool_int(data.get('projet_personnel', 0)),
            id,
        ))
        db.commit(); return jsonify({'success':True})
    except Exception as e:
        db.rollback(); return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()


# ── Statistiques ──

@bp.route('/api/stats/summary')
def api_stats_summary():
    db = get_db()
    try:
        dd = request.args.get('date_debut','')
        df = request.args.get('date_fin','')
        w = '1=1'; p = []
        if dd: w+=' AND c.date_saisie >= ?'; p.append(dd)
        if df: w+=' AND c.date_saisie <= ?'; p.append(df + ' 23:59:59' if df and len(df) == 10 else df)

        total = db.execute(f'SELECT COUNT(*) as n FROM consommations c WHERE {w}', p).fetchone()['n']

        by_type = rows_to_list(db.execute(f'''
            SELECT t.nom,t.icone,t.couleur,t.badge_class,COUNT(*) as count
            FROM consommations c JOIN types_activite t ON c.type_activite_id=t.id
            WHERE {w} GROUP BY t.id ORDER BY count DESC''', p).fetchall())

        by_prep = rows_to_list(db.execute(f'''
            SELECT p.nom,COUNT(*) as count FROM consommations c
            JOIN preparateurs p ON c.preparateur_id=p.id
            WHERE {w} GROUP BY p.id ORDER BY count DESC''', p).fetchall())

        total_3d = db.execute(f'''
            SELECT COALESCE(SUM(c.poids_grammes),0) as t FROM consommations c
            JOIN types_activite t ON c.type_activite_id=t.id
            WHERE t.unite_defaut='g' AND {w}''', p).fetchone()['t']

        total_decoupe = db.execute(f'''
            SELECT COALESCE(SUM(c.surface_m2),0) as t FROM consommations c
            JOIN types_activite t ON c.type_activite_id=t.id
            WHERE t.unite_defaut='m²' AND {w}''', p).fetchone()['t']

        total_papier = db.execute(f'''
            SELECT COALESCE(SUM(c.nb_feuilles),0) as t FROM consommations c
            JOIN types_activite t ON c.type_activite_id=t.id
            WHERE t.unite_defaut='feuilles' AND {w}''', p).fetchone()['t']

        papier_detail = db.execute(f'''
            SELECT
                COALESCE(SUM(CASE WHEN COALESCE(mat.nom, c.nom_materiau) LIKE '%Couleur%' THEN c.nb_feuilles ELSE 0 END),0) as couleur,
                COALESCE(SUM(CASE WHEN COALESCE(mat.nom, c.nom_materiau) LIKE '%N&B%' THEN c.nb_feuilles ELSE 0 END),0) as nb
            FROM consommations c
            JOIN types_activite t ON c.type_activite_id=t.id
            LEFT JOIN materiaux mat ON c.materiau_id=mat.id
            WHERE t.unite_defaut='feuilles' AND {w}''', p).fetchone()

        return jsonify({
            'total_interventions': total,
            'by_type': by_type, 'by_preparateur': by_prep,
            'total_3d_grammes': round(total_3d, 1),
            'total_decoupe_m2': round(total_decoupe, 3),
            'total_papier_feuilles': int(total_papier),
            'total_papier_couleur': int(papier_detail['couleur']),
            'total_papier_nb': int(papier_detail['nb']),
        })
    finally:
        db.close()


@bp.route('/api/stats/activity')
def api_stats_activity():
    """Statistiques d'activité journalière : répartition par heure, par jour de semaine, filtrable."""
    db = get_db()
    try:
        dd = request.args.get('date_debut', '')
        df = request.args.get('date_fin', '')
        prep_id = request.args.get('preparateur_id', '')
        machine_id = request.args.get('machine_id', '')
        w = '1=1'; p = []
        if dd: w += ' AND c.date_saisie >= ?'; p.append(dd)
        if df: w += ' AND c.date_saisie <= ?'; p.append(df + ' 23:59:59' if df and len(df) == 10 else df)
        if prep_id: w += ' AND c.preparateur_id = ?'; p.append(int(prep_id))
        if machine_id: w += ' AND c.machine_id = ?'; p.append(int(machine_id))

        by_hour = rows_to_list(db.execute(f'''
            SELECT CAST(strftime('%H', c.date_saisie) AS INTEGER) as hour, COUNT(*) as count
            FROM consommations c WHERE {w}
            GROUP BY hour ORDER BY hour
        ''', p).fetchall())

        by_dow = rows_to_list(db.execute(f'''
            SELECT CAST(strftime('%w', c.date_saisie) AS INTEGER) as dow, COUNT(*) as count
            FROM consommations c WHERE {w}
            GROUP BY dow ORDER BY dow
        ''', p).fetchall())

        by_hour_prep = rows_to_list(db.execute(f'''
            SELECT CAST(strftime('%H', c.date_saisie) AS INTEGER) as hour,
                   pr.nom as preparateur, COUNT(*) as count
            FROM consommations c
            JOIN preparateurs pr ON c.preparateur_id=pr.id
            WHERE {w}
            GROUP BY hour, pr.id ORDER BY hour
        ''', p).fetchall())

        return jsonify({
            'by_hour': by_hour,
            'by_day_of_week': by_dow,
            'by_hour_prep': by_hour_prep,
        })
    finally:
        db.close()


@bp.route('/api/stats/timeline')
def api_stats_timeline():
    db = get_db()
    try:
        dd = request.args.get('date_debut','')
        df = request.args.get('date_fin','')
        gb = request.args.get('group_by','month')
        w = '1=1'; p = []
        if dd: w+=' AND c.date_saisie >= ?'; p.append(dd)
        if df: w+=' AND c.date_saisie <= ?'; p.append(df + ' 23:59:59' if df and len(df) == 10 else df)

        dex = {"day":"strftime('%Y-%m-%d',c.date_saisie)","week":"strftime('%Y-W%W',c.date_saisie)","month":"strftime('%Y-%m',c.date_saisie)"}.get(gb,"strftime('%Y-%m',c.date_saisie)")

        timeline = rows_to_list(db.execute(f'''
            SELECT {dex} as period,t.nom as type_nom,t.couleur,COUNT(*) as count
            FROM consommations c JOIN types_activite t ON c.type_activite_id=t.id
            WHERE {w} GROUP BY period,t.id ORDER BY period''', p).fetchall())

        timeline_3d = rows_to_list(db.execute(f'''
            SELECT {dex} as period, mat.nom as materiau,
                   COALESCE(SUM(c.poids_grammes),0) as total_g
            FROM consommations c JOIN types_activite t ON c.type_activite_id=t.id
            LEFT JOIN materiaux mat ON c.materiau_id=mat.id
            WHERE t.unite_defaut='g' AND {w}
            GROUP BY period,mat.nom ORDER BY period''', p).fetchall())

        timeline_decoupe = rows_to_list(db.execute(f'''
            SELECT {dex} as period, mat.nom as materiau,
                   COALESCE(SUM(c.surface_m2),0) as total_m2
            FROM consommations c JOIN types_activite t ON c.type_activite_id=t.id
            LEFT JOIN materiaux mat ON c.materiau_id=mat.id
            WHERE t.unite_defaut='m²' AND {w}
            GROUP BY period,mat.nom ORDER BY period''', p).fetchall())

        timeline_papier = rows_to_list(db.execute(f'''
            SELECT {dex} as period,
                   CASE
                       WHEN COALESCE(mat.nom, c.nom_materiau) LIKE '%Couleur%' THEN 'Couleur'
                       WHEN COALESCE(mat.nom, c.nom_materiau) LIKE '%N&B%' THEN 'N&B'
                       ELSE 'Autre'
                   END as type_impression,
                   COALESCE(SUM(c.nb_feuilles),0) as total_feuilles
            FROM consommations c
            JOIN types_activite t ON c.type_activite_id=t.id
            LEFT JOIN materiaux mat ON c.materiau_id=mat.id
            WHERE t.unite_defaut='feuilles' AND {w}
            GROUP BY period, type_impression ORDER BY period''', p).fetchall())

        top_machines = rows_to_list(db.execute(f'''
            SELECT m.nom,t.nom as type_nom,t.couleur,COUNT(*) as count
            FROM consommations c JOIN machines m ON c.machine_id=m.id
            JOIN types_activite t ON c.type_activite_id=t.id
            WHERE {w} GROUP BY m.id ORDER BY count DESC LIMIT 10''', p).fetchall())

        top_classes = rows_to_list(db.execute(f'''
            SELECT cl.nom,COUNT(*) as count FROM consommations c
            JOIN classes cl ON c.classe_id=cl.id
            WHERE {w} GROUP BY cl.id ORDER BY count DESC LIMIT 10''', p).fetchall())

        return jsonify({
            'timeline': timeline, 'timeline_3d': timeline_3d,
            'timeline_decoupe': timeline_decoupe,
            'timeline_papier': timeline_papier,
            'top_machines': top_machines, 'top_classes': top_classes,
        })
    finally:
        db.close()


# ── Export CSV ──

@bp.route('/api/export/csv')
def api_export_csv():
    db = get_db()
    try:
        dd = request.args.get('date_debut','')
        df = request.args.get('date_fin','')
        ta = request.args.get('type_activite_id','')
        w = '1=1'; p = []
        if dd: w+=' AND c.date_saisie >= ?'; p.append(dd)
        if df: w+=' AND c.date_saisie <= ?'; p.append(df + ' 23:59:59' if df and len(df) == 10 else df)
        if ta: w+=' AND c.type_activite_id = ?'; p.append(int(ta))

        rows = db.execute(f'''
            SELECT c.date_saisie,
                   COALESCE(p.nom, c.nom_preparateur) as preparateur,
                   COALESCE(t.nom, c.nom_type_activite) as type_activite,
                   COALESCE(m.nom, c.nom_machine) as machine,
                   COALESCE(cl.nom, c.nom_classe) as classe,
                   COALESCE(r.nom, c.nom_referent) as referent,
                   r.categorie as ref_categorie,
                   COALESCE(mat.nom, c.nom_materiau) as materiau,
                   c.poids_grammes,c.surface_m2,c.longueur_mm,c.largeur_mm,
                   c.epaisseur,c.nb_feuilles,c.format_papier,c.impression_couleur,
                     c.nb_feuilles_plastique,c.type_feuille,c.projet_nom,c.projet_personnel,c.commentaire
            FROM consommations c
            LEFT JOIN preparateurs p ON c.preparateur_id=p.id
            LEFT JOIN types_activite t ON c.type_activite_id=t.id
            LEFT JOIN machines m ON c.machine_id=m.id
            LEFT JOIN classes cl ON c.classe_id=cl.id
            LEFT JOIN referents r ON c.referent_id=r.id
            LEFT JOIN materiaux mat ON c.materiau_id=mat.id
            WHERE {w} ORDER BY c.date_saisie DESC
        ''', p).fetchall()

        out = io.StringIO()
        out.write('\ufeff')
        wr = csv.writer(out, delimiter=';')
        wr.writerow(['Date','Préparateur','Type activité','Machine','Classe',
                      'Référent','Catégorie réf.','Matériau',
                      'Poids (g)','Surface (m²)','Longueur (mm)','Largeur (mm)',
                      'Épaisseur','Nb feuilles','Format papier','Impression couleur','Projet personnel',
                      'Nb feuilles plastique','Type feuille','Projet','Commentaire'])
        for row in rows:
            wr.writerow([row[k] or '' for k in row.keys()])

        out.seek(0)
        return Response(out.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition':f'attachment; filename=fabtrack_export_{datetime.now().strftime("%Y%m%d")}.csv'})
    finally:
        db.close()


# ── Gabarits CSV ──

CSV_TEMPLATES = {
    'machines':   ('nom;type_activite;quantite;marque;zone_travail;puissance;description;principes_conception\n'
                   'Exemple Machine;Impression 3D;1;Marque;300x300 mm;100W;Description;ajout\n'),
    'materiaux':  ('nom;unite;machines\n'
                   'Exemple Matériau;g;Creality CR10-S,Raise 3D Pro\n'),
    'classes':    ('nom\n501\n502\nBTS CPRP\n'),
    'referents':  ('nom;categorie\n'
                   'M. Dupont;Professeur\nMme Martin;Agent technique\nEntreprise X;Demande extérieure\n'),
    'preparateurs':('nom\nJean Martin\nMarie Curie\n'),
}

@bp.route('/api/template/<entity>')
def api_download_template(entity):
    tpl = CSV_TEMPLATES.get(entity)
    if not tpl:
        return jsonify({'error':'Gabarit inconnu'}), 404
    return Response('\ufeff'+tpl, mimetype='text/csv',
                    headers={'Content-Disposition':f'attachment; filename=gabarit_{entity}.csv'})


# ── Import CSV ──

@bp.route('/api/import/<entity>', methods=['POST'])
def api_import_csv(entity):
    """Import en masse depuis un fichier CSV."""
    if 'file' not in request.files:
        return jsonify({'success':False,'error':'Aucun fichier'}), 400
    f = request.files['file']
    content = f.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content), delimiter=';')
    db = get_db()
    imported = 0
    errors = []

    try:
        type_map = {r[1]:r[0] for r in db.execute('SELECT id,nom FROM types_activite').fetchall()}

        for i, row in enumerate(reader, start=2):
            try:
                if entity == 'machines':
                    tid = type_map.get(row.get('type_activite','').strip())
                    if not tid:
                        errors.append(f"Ligne {i}: type activité inconnu '{row.get('type_activite','')}'")
                        continue
                    db.execute('INSERT INTO machines (nom,type_activite_id,quantite,marque,zone_travail,puissance,description,principes_conception) VALUES (?,?,?,?,?,?,?,?)',
                               (row['nom'].strip(), tid, int(row.get('quantite',1) or 1),
                                row.get('marque','').strip(), row.get('zone_travail','').strip(),
                                row.get('puissance','').strip(), row.get('description','').strip(),
                                row.get('principes_conception','').strip()))

                elif entity == 'materiaux':
                    cur_mat = db.execute('INSERT OR IGNORE INTO materiaux (nom,unite) VALUES (?,?)',
                               (row['nom'].strip(), row.get('unite','').strip()))
                    if cur_mat.lastrowid:
                        mat_id = cur_mat.lastrowid
                    else:
                        mat_id = db.execute('SELECT id FROM materiaux WHERE nom=?', (row['nom'].strip(),)).fetchone()[0]
                    machines_str = row.get('machines', '').strip()
                    if machines_str:
                        for mnom in machines_str.split(','):
                            mnom = mnom.strip()
                            mrow = db.execute('SELECT id FROM machines WHERE nom=?', (mnom,)).fetchone()
                            if mrow:
                                db.execute('INSERT OR IGNORE INTO materiau_machine (materiau_id, machine_id) VALUES (?,?)', (mat_id, mrow[0]))

                elif entity == 'classes':
                    db.execute('INSERT OR IGNORE INTO classes (nom) VALUES (?)', (row['nom'].strip(),))

                elif entity == 'referents':
                    cat = row.get('categorie','Professeur').strip() or 'Professeur'
                    db.execute('INSERT OR IGNORE INTO referents (nom,categorie) VALUES (?,?)',
                               (row['nom'].strip(), cat))

                elif entity == 'preparateurs':
                    db.execute('INSERT OR IGNORE INTO preparateurs (nom) VALUES (?)', (row['nom'].strip(),))
                else:
                    return jsonify({'success':False,'error':'Entité inconnue'}), 400

                imported += 1
            except Exception as e:
                errors.append(f"Ligne {i}: {e}")

        db.commit()
        return jsonify({'success':True,'imported':imported,'errors':errors})
    except Exception as e:
        db.rollback()
        return jsonify({'success':False,'error':str(e)}), 400
    finally:
        db.close()
