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


def _normalize_referent_ids(raw_value):
    if raw_value in (None, '', []):
        return []

    values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
    result = []
    seen = set()
    for value in values:
        try:
            referent_id = int(value)
        except (TypeError, ValueError):
            continue
        if referent_id <= 0 or referent_id in seen:
            continue
        seen.add(referent_id)
        result.append(referent_id)
    return result


def _resolve_referent_links(db, raw_value):
    referent_ids = _normalize_referent_ids(raw_value)
    if not referent_ids:
        return []

    placeholders = ','.join(['?'] * len(referent_ids))
    rows = db.execute(
        f'SELECT id, nom FROM referents WHERE id IN ({placeholders})',
        referent_ids,
    ).fetchall()
    names_by_id = {int(row['id']): row['nom'] for row in rows}

    links = []
    for referent_id in referent_ids:
        name = names_by_id.get(referent_id, '').strip()
        if name:
            links.append({'id': referent_id, 'nom': name})
    return links


def _replace_consumption_referents(db, consommation_id, referent_links):
    db.execute('DELETE FROM consommation_referents WHERE consommation_id=?', (consommation_id,))
    for index, link in enumerate(referent_links):
        db.execute(
            '''
            INSERT INTO consommation_referents (consommation_id, ordre, referent_id, nom_referent)
            VALUES (?, ?, ?, ?)
            ''',
            (consommation_id, index, link['id'], link['nom']),
        )


def _get_type_row(db, type_activite_id):
    try:
        type_id = int(type_activite_id)
    except (TypeError, ValueError):
        return None
    return db.execute(
        'SELECT id, nom, unite_defaut FROM types_activite WHERE id=?',
        (type_id,),
    ).fetchone()


def _is_paper_type(type_row):
    if not type_row:
        return False
    type_name = str(type_row['nom'] or '').strip().lower()
    default_unit = str(type_row['unite_defaut'] or '').strip().lower()
    return type_name == 'impression papier' or default_unit == 'feuilles'


def _paper_material_matches(name, format_papier, impression_mode):
    text = str(name or '').strip().lower()
    format_token = str(format_papier or '').strip().lower()
    if not text or not format_token or format_token not in text:
        return False
    if impression_mode == 'couleur':
        return 'couleur' in text
    return 'n&b' in text or 'nb' in text or ('noir' in text and 'blanc' in text)


def _resolve_paper_material_id(db, type_row, action):
    format_papier = str(action.get('format_papier') or '').strip().upper()
    impression_mode = str(action.get('impression_couleur') or '').strip().lower()
    if not format_papier or impression_mode not in ('couleur', 'nb'):
        return None

    try:
        machine_id = int(action.get('machine_id') or 0)
    except (TypeError, ValueError):
        machine_id = 0

    if machine_id:
        rows = db.execute(
            '''
            SELECT m.id, m.nom
            FROM machine_type_materiau mtm
            JOIN materiaux m ON m.id = mtm.materiau_id
            WHERE mtm.machine_id=? AND mtm.type_activite_id=? AND m.actif=1
            ORDER BY m.nom ASC
            ''',
            (machine_id, type_row['id']),
        ).fetchall()
    else:
        rows = db.execute(
            '''
            SELECT DISTINCT m.id, m.nom
            FROM machine_type_materiau mtm
            JOIN machine_type_activite mta
              ON mta.machine_id = mtm.machine_id AND mta.type_activite_id = mtm.type_activite_id
            JOIN materiaux m ON m.id = mtm.materiau_id
            WHERE mtm.type_activite_id=? AND m.actif=1
            ORDER BY m.nom ASC
            ''',
            (type_row['id'],),
        ).fetchall()

    for row in rows:
        if _paper_material_matches(row['nom'], format_papier, impression_mode):
            return row['id']
    return None


def _resolve_effective_material_id(db, action):
    try:
        current_id = int(action.get('materiau_id') or 0)
    except (TypeError, ValueError):
        current_id = 0

    type_row = _get_type_row(db, action.get('type_activite_id'))
    if not _is_paper_type(type_row):
        return current_id or None

    resolved_id = _resolve_paper_material_id(db, type_row, action)
    return resolved_id or (current_id or None)


def _serialize_consumption_detail(db, consommation_id):
    row = db.execute(
        '''
        SELECT c.*,
               COALESCE(p.nom, c.nom_preparateur) as preparateur_nom,
               COALESCE(t.nom, c.nom_type_activite) as type_activite_nom,
               t.icone as type_icone, t.badge_class,
               COALESCE(m.nom, c.nom_machine) as machine_nom,
               COALESCE(cl.nom, c.nom_classe) as classe_nom,
               COALESCE(
                   NULLIF((
                       SELECT GROUP_CONCAT(ref_names.nom, ' | ')
                       FROM (
                           SELECT COALESCE(r2.nom, cr.nom_referent) AS nom
                           FROM consommation_referents cr
                           LEFT JOIN referents r2 ON r2.id = cr.referent_id
                           WHERE cr.consommation_id = c.id
                           ORDER BY cr.ordre ASC
                       ) AS ref_names
                   ), ''),
                   COALESCE(r.nom, c.nom_referent)
               ) as referent_nom,
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
        WHERE c.id=?
        ''',
        (consommation_id,),
    ).fetchone()
    if not row:
        return None

    payload = dict(row)
    referent_rows = db.execute(
        '''
        SELECT referent_id
        FROM consommation_referents
        WHERE consommation_id=?
        ORDER BY ordre ASC
        ''',
        (consommation_id,),
    ).fetchall()
    payload['referent_ids'] = [int(ref['referent_id']) for ref in referent_rows if ref['referent_id']]
    if not payload['referent_ids'] and payload.get('referent_id'):
        payload['referent_ids'] = [int(payload['referent_id'])]
    return payload


def _surface_from_action(action):
    surface = _to_float(action.get('surface_m2'))
    if surface is not None:
        return surface
    longueur_mm = _to_float(action.get('longueur_mm'))
    largeur_mm = _to_float(action.get('largeur_mm'))
    if longueur_mm and largeur_mm:
        return (longueur_mm * largeur_mm) / 1e6
    return None


def _normalized_occurrence_count(action):
    try:
        count = int(action.get('occurrence_count') or 1)
    except (TypeError, ValueError):
        count = 1
    return max(1, count)


def _resolve_material_row(db, materiau_id):
    if not materiau_id:
        return None
    return db.execute(
        'SELECT nom, count_occurrences FROM materiaux WHERE id=?',
        (materiau_id,),
    ).fetchone()


# Familles d'unités pour lesquelles les occurrences sont applicables.
_OCCURRENCE_UNITS = ('g', 'm²', 'feuilles')
_OCCURRENCE_TYPE_NAMES = ('impression 3d', 'découpe laser', 'cnc / fraisage', 'impression papier')


def _type_supports_occurrences(type_row):
    """Retourne True si le type d'activité est dans une famille qui supporte le multiplicateur."""
    type_name = str(type_row['nom'] or '').strip().lower()
    default_unit = str(type_row['unite_defaut'] or '').strip().lower()
    return default_unit in _OCCURRENCE_UNITS or type_name in _OCCURRENCE_TYPE_NAMES


def _occurrence_multiplier_allowed(db, action):
    type_row = _get_type_row(db, action.get('type_activite_id'))
    if not type_row:
        return False

    # Si le type ne supporte pas les occurrences, on n'applique jamais le multiplicateur.
    if not _type_supports_occurrences(type_row):
        return False

    # Le flag matériau est prioritaire — mais seulement dans les familles autorisées.
    material_row = _resolve_material_row(db, action.get('materiau_id'))
    if material_row and material_row['count_occurrences'] in (0, 1):
        return bool(material_row['count_occurrences'])

    # Défaut : activé pour toutes les familles supportées.
    return True


def _apply_occurrence_multiplier(db, action):
    payload = dict(action or {})
    count = _normalized_occurrence_count(payload)
    payload['occurrence_count'] = count

    if count <= 1 or not _occurrence_multiplier_allowed(db, payload):
        return payload

    poids = _to_float(payload.get('poids_grammes'))
    if poids is not None:
        payload['poids_grammes'] = poids * count

    surface = _surface_from_action(payload)
    if surface is not None:
        payload['surface_m2'] = surface * count

    nb_feuilles = _to_float(payload.get('nb_feuilles'))
    if nb_feuilles is not None:
        payload['nb_feuilles'] = int(round(nb_feuilles * count))

    nb_feuilles_plastique = _to_float(payload.get('nb_feuilles_plastique'))
    if nb_feuilles_plastique is not None:
        payload['nb_feuilles_plastique'] = int(round(nb_feuilles_plastique * count))

    payload['quantite'] = count
    payload['unite'] = 'occurrences'
    return payload


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

    if machine_id:
        if not type_activite_id:
            return False
        row = db.execute(
            'SELECT 1 FROM machine_type_materiau WHERE machine_id=? AND type_activite_id=? AND materiau_id=?',
            (machine_id, type_activite_id, materiau_id)
        ).fetchone()
        return bool(row)

    if type_activite_id:
        machine_ids = _machine_ids_for_type(db, type_activite_id)
        if not machine_ids:
            return False

        # Matériau autorisé si présent dans au moins une cellule de la matrice pour ce type.
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

        return False

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
        q          = request.args.get('q','').strip()
        page     = max(1, int(request.args.get('page',1) or 1))
        per_page = min(max(1, int(request.args.get('per_page',50) or 50)), 10000)

        query = '''
            SELECT c.*,
                   COALESCE(p.nom, c.nom_preparateur) as preparateur_nom,
                   COALESCE(t.nom, c.nom_type_activite) as type_activite_nom,
                   t.icone as type_icone, t.badge_class,
                   COALESCE(m.nom, c.nom_machine) as machine_nom,
                   COALESCE(cl.nom, c.nom_classe) as classe_nom,
                   COALESCE(
                       NULLIF((
                           SELECT GROUP_CONCAT(ref_names.nom, ' | ')
                           FROM (
                               SELECT COALESCE(r2.nom, cr.nom_referent) AS nom
                               FROM consommation_referents cr
                               LEFT JOIN referents r2 ON r2.id = cr.referent_id
                               WHERE cr.consommation_id = c.id
                               ORDER BY cr.ordre ASC
                           ) AS ref_names
                       ), ''),
                       COALESCE(r.nom, c.nom_referent)
                   ) as referent_nom,
                   r.categorie as referent_categorie,
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
        ]:
            if val:
                query += f' AND {col} ?'; params.append(cast(val))
                count_q += f' AND {col} ?'; cp.append(cast(val))

        if referent_id:
            ref_val = int(referent_id)
            query += ' AND (c.referent_id = ? OR EXISTS (SELECT 1 FROM consommation_referents crf WHERE crf.consommation_id = c.id AND crf.referent_id = ?))'
            params.extend([ref_val, ref_val])
            count_q += ' AND (c.referent_id = ? OR EXISTS (SELECT 1 FROM consommation_referents crf WHERE crf.consommation_id = c.id AND crf.referent_id = ?))'
            cp.extend([ref_val, ref_val])

        if q:
            query   += ' AND c.projet_nom LIKE ?'; params.append(f'%{q}%')
            count_q += ' AND c.projet_nom LIKE ?'; cp.append(f'%{q}%')

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


@bp.route('/api/consommations/<int:id>', methods=['GET'])
def api_get_consommation(id):
    db = get_db()
    try:
        payload = _serialize_consumption_detail(db, id)
        if not payload:
            return jsonify({'success': False, 'error': 'Saisie introuvable'}), 404
        return jsonify({'success': True, 'data': payload})
    finally:
        db.close()


@bp.route('/api/consommations', methods=['POST'])
def api_create_consommation():
    data = request.get_json(); db = get_db()
    try:
        payload = dict(data or {})
        payload['materiau_id'] = _resolve_effective_material_id(db, payload)
        payload = _apply_occurrence_multiplier(db, payload)

        validation_error = _validate_action_selection(db, payload)
        if validation_error:
            return jsonify({'success': False, 'error': validation_error}), 400

        surface = _surface_from_action(payload)
        referent_links = _resolve_referent_links(db, payload.get('referent_ids', payload.get('referent_id')))
        primary_referent = referent_links[0] if referent_links else None

        nom_prep = _resolve_nom(db, 'preparateurs', payload.get('preparateur_id'))
        nom_type = _resolve_nom(db, 'types_activite', payload.get('type_activite_id'))
        nom_mach = _resolve_nom(db, 'machines', payload.get('machine_id'))
        nom_cls  = _resolve_nom(db, 'classes', payload.get('classe_id'))
        nom_mat  = _resolve_nom(db, 'materiaux', payload.get('materiau_id'))

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
            payload.get('date_saisie', datetime.now().strftime('%Y-%m-%d %H:%M')),
            payload.get('preparateur_id'), payload.get('type_activite_id'),
            payload.get('machine_id') or None, payload.get('classe_id') or None,
            primary_referent['id'] if primary_referent else None,
            payload.get('materiau_id') or None,
            nom_prep, nom_type, nom_mach, nom_cls, (primary_referent['nom'] if primary_referent else ''), nom_mat,
            payload.get('quantite') or 0, payload.get('unite',''),
            payload.get('poids_grammes') or None,
            payload.get('longueur_mm') or None, payload.get('largeur_mm') or None,
            surface or None,
            payload.get('epaisseur') or None,
            payload.get('nb_feuilles') or None, payload.get('format_papier') or None,
            payload.get('nb_feuilles_plastique') or None,
            payload.get('type_feuille') or None, payload.get('commentaire',''),
            payload.get('impression_couleur',''),
            payload.get('projet_nom',''),
            _to_bool_int(payload.get('projet_personnel', 0)),
        ))

        _replace_consumption_referents(db, cur.lastrowid, referent_links)

        # Synchronisation stock non bloquante (on autorise les stocks négatifs).
        try:
            _decrease_stock_from_action(db, cur.lastrowid, payload)
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
        'referent_ids': data.get('referent_ids', data.get('referent_id')),
        'projet_nom': data.get('projet_nom', ''),
        'projet_personnel': _to_bool_int(data.get('projet_personnel', 0)),
    }

    db = get_db()
    ids = []
    try:
        nom_prep = _resolve_nom(db, 'preparateurs', common['preparateur_id'])
        nom_cls  = _resolve_nom(db, 'classes', common['classe_id'])
        referent_links = _resolve_referent_links(db, common['referent_ids'])
        primary_referent = referent_links[0] if referent_links else None

        for index, action in enumerate(actions, start=1):
            action_payload = dict(action or {})
            action_payload['materiau_id'] = _resolve_effective_material_id(db, action_payload)
            action_payload = _apply_occurrence_multiplier(db, action_payload)

            validation_error = _validate_action_selection(db, action_payload)
            if validation_error:
                db.rollback()
                return jsonify({'success': False, 'error': f'Action {index}: {validation_error}'}), 400

            surface = _surface_from_action(action_payload)

            nom_type = _resolve_nom(db, 'types_activite', action_payload.get('type_activite_id'))
            nom_mach = _resolve_nom(db, 'machines', action_payload.get('machine_id'))
            nom_mat  = _resolve_nom(db, 'materiaux', action_payload.get('materiau_id'))

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
                action_payload.get('type_activite_id'), action_payload.get('machine_id') or None,
                common.get('classe_id') or None, (primary_referent['id'] if primary_referent else None),
                action_payload.get('materiau_id') or None,
                nom_prep, nom_type, nom_mach, nom_cls, (primary_referent['nom'] if primary_referent else ''), nom_mat,
                action_payload.get('quantite') or 0, action_payload.get('unite', ''),
                action_payload.get('poids_grammes') or None,
                action_payload.get('longueur_mm') or None, action_payload.get('largeur_mm') or None,
                surface or None,
                action_payload.get('epaisseur') or None,
                action_payload.get('nb_feuilles') or None, action_payload.get('format_papier') or None,
                action_payload.get('nb_feuilles_plastique') or None,
                action_payload.get('type_feuille') or None, action_payload.get('commentaire', ''),
                action_payload.get('impression_couleur', ''), common['projet_nom'], common['projet_personnel'],
            ))
            conso_id = cur.lastrowid
            ids.append(conso_id)

            _replace_consumption_referents(db, conso_id, referent_links)

            # Synchronisation stock non bloquante (on autorise les stocks négatifs).
            try:
                _decrease_stock_from_action(db, conso_id, action_payload)
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
        payload = dict(data or {})
        payload['materiau_id'] = _resolve_effective_material_id(db, payload)
        payload = _apply_occurrence_multiplier(db, payload)

        validation_error = _validate_action_selection(db, payload)
        if validation_error:
            return jsonify({'success': False, 'error': validation_error}), 400

        surface = _surface_from_action(payload)
        referent_links = _resolve_referent_links(db, payload.get('referent_ids', payload.get('referent_id')))
        primary_referent = referent_links[0] if referent_links else None

        nom_prep = _resolve_nom(db, 'preparateurs', payload.get('preparateur_id'))
        nom_type = _resolve_nom(db, 'types_activite', payload.get('type_activite_id'))
        nom_mach = _resolve_nom(db, 'machines', payload.get('machine_id'))
        nom_cls  = _resolve_nom(db, 'classes', payload.get('classe_id'))
        nom_mat  = _resolve_nom(db, 'materiaux', payload.get('materiau_id'))

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
            payload.get('date_saisie'), payload.get('preparateur_id'),
            payload.get('type_activite_id'), payload.get('machine_id') or None,
            payload.get('classe_id') or None, (primary_referent['id'] if primary_referent else None),
            payload.get('materiau_id') or None,
            nom_prep, nom_type, nom_mach, nom_cls, (primary_referent['nom'] if primary_referent else ''), nom_mat,
            payload.get('quantite') or 0, payload.get('unite',''),
            payload.get('poids_grammes') or None,
            payload.get('longueur_mm') or None, payload.get('largeur_mm') or None,
            surface or None,
            payload.get('epaisseur') or None,
            payload.get('nb_feuilles') or None, payload.get('format_papier') or None,
            payload.get('nb_feuilles_plastique') or None,
            payload.get('type_feuille') or None, payload.get('commentaire',''),
            payload.get('impression_couleur',''),
            payload.get('projet_nom',''),
            _to_bool_int(payload.get('projet_personnel', 0)),
            id,
        ))
        _replace_consumption_referents(db, id, referent_links)
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
                    db.execute('INSERT OR IGNORE INTO materiaux (nom,unite) VALUES (?,?)',
                               (row['nom'].strip(), row.get('unite','').strip()))

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
