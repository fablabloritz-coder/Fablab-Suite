"""PretGo — Blueprint : reservations"""

from datetime import datetime
import json

from flask import Blueprint, flash, redirect, render_template, request, url_for

from reservations_logic import (
    compute_expected_return_datetime,
    expire_old_reservations,
    find_creation_conflicts_for_category_reservation,
    find_creation_conflicts_for_reservation,
    format_db_datetime,
    parse_db_datetime,
    parse_form_datetime_local,
)
from database import get_setting
from utils import get_app_db, admin_required

bp = Blueprint('reservations', __name__)


def _extract_reservation_items(row):
    """Retourne la liste normalisée des items d'une réservation."""
    items = []
    raw = row['items_json'] if row and 'items_json' in row.keys() else None
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    desc = (item.get('description') or '').strip()
                    mid = item.get('materiel_id')
                    try:
                        mid = int(mid) if mid not in (None, '') else None
                    except (TypeError, ValueError):
                        mid = None
                    if desc:
                        items.append({'description': desc, 'materiel_id': mid})
        except Exception:
            items = []

    if not items:
        # Compatibilité legacy (réservation mono-matériel)
        desc = row['numero_inventaire'] if row and 'numero_inventaire' in row.keys() else ''
        if desc:
            items.append({'description': desc, 'materiel_id': row['materiel_id']})
    return items


def _first_linked_material_id(items):
    for item in items:
        if item.get('materiel_id'):
            return int(item['materiel_id'])
    return None


def _extract_reservation_category_demands(row):
    """Retourne les demandes de catégories d'une réservation."""
    demands = []
    raw = row['demande_categories_json'] if row and 'demande_categories_json' in row.keys() else None
    if not raw:
        return demands

    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            for item in loaded:
                if not isinstance(item, dict):
                    continue
                cat = (item.get('category') or item.get('categorie') or '').strip()
                try:
                    qty = int(item.get('quantity', 0))
                except (TypeError, ValueError):
                    qty = 0
                if cat and qty > 0:
                    demands.append({'category': cat, 'quantity': qty})
    except Exception:
        pass
    return demands


def _reservation_conflicts_for_items(conn, items, reservation_dt, reservation_end_dt, now_dt, exclude_reservation_id=None):
    """Vérifie les conflits pour tous les matériels liés aux items."""
    conflicts = []
    seen_materials = set()
    for item in items:
        materiel_id = item.get('materiel_id')
        if not materiel_id or materiel_id in seen_materials:
            continue
        seen_materials.add(materiel_id)
        messages = find_creation_conflicts_for_reservation(
            conn,
            materiel_id=materiel_id,
            reservation_dt=reservation_dt,
            reservation_end_dt=reservation_end_dt,
            now_dt=now_dt,
            exclude_reservation_id=exclude_reservation_id,
        )
        if not messages:
            continue
        conflicts.extend(messages)
    return conflicts


def _reservation_conflicts_for_categories(conn, category_demands, reservation_dt, reservation_end_dt, now_dt, exclude_reservation_id=None):
    """Vérifie les conflits de capacité pour des demandes par catégorie."""
    conflicts = []
    for demand in category_demands:
        messages = find_creation_conflicts_for_category_reservation(
            conn,
            category_name=demand['category'],
            quantity=demand['quantity'],
            reservation_dt=reservation_dt,
            reservation_end_dt=reservation_end_dt,
            now_dt=now_dt,
            exclude_reservation_id=exclude_reservation_id,
        )
        if messages:
            conflicts.extend(messages)
    return conflicts


@bp.route('/reservations/<int:reservation_id>/convertir')
def convertir_reservation(reservation_id):
    conn = get_app_db()
    row = conn.execute(
        '''
        SELECT r.id, r.statut, r.date_reservation, r.date_fin_reservation, r.items_json,
             r.demande_categories_json,
               COALESCE(pe.nom, '[Inconnu]') AS nom,
               COALESCE(pe.prenom, '') AS prenom,
               inv.id AS materiel_id,
               inv.numero_inventaire, inv.type_materiel, inv.marque, inv.modele
        FROM reservations r
        LEFT JOIN personnes pe ON pe.id = r.personne_id
        LEFT JOIN inventaire inv ON inv.id = r.materiel_id
        WHERE r.id = ?
        ''',
        (reservation_id,),
    ).fetchone()

    if not row:
        flash('Réservation introuvable.', 'danger')
        return redirect(url_for('reservations.reservations'))

    if row['statut'] not in ('confirmee', 'demande', 'expiree'):
        flash('Cette réservation ne peut plus être convertie en prêt.', 'warning')
        return redirect(url_for('reservations.reservations'))

    start_dt = parse_db_datetime(row['date_reservation'])
    end_dt = parse_db_datetime(row['date_fin_reservation']) if row['date_fin_reservation'] else None
    now_dt = datetime.now()
    can_convert_now = bool(start_dt and now_dt >= start_dt)

    action = (request.args.get('action') or '').strip().lower()
    if action:
        if action == 'convert':
            if not can_convert_now:
                flash("La date de départ n'est pas encore atteinte. Vous pouvez forcer le départ si nécessaire.", 'warning')
                return redirect(url_for('reservations.convertir_reservation', reservation_id=reservation_id))
            return redirect(url_for('prets.nouveau_pret', reservation_id=reservation_id))

        if action == 'force':
            now_str = format_db_datetime(now_dt)
            new_end_dt = end_dt
            if not new_end_dt or new_end_dt < now_dt:
                new_end_dt = now_dt
            conn.execute(
                '''
                UPDATE reservations
                SET date_reservation = ?,
                    date_fin_reservation = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (now_str, format_db_datetime(new_end_dt), reservation_id),
            )
            conn.commit()
            flash('Départ de réservation forcé : la date de début a été ajustée à maintenant.', 'info')
            return redirect(url_for('prets.nouveau_pret', reservation_id=reservation_id))

        flash('Action de conversion invalide.', 'warning')
        return redirect(url_for('reservations.convertir_reservation', reservation_id=reservation_id))

    category_demands = _extract_reservation_category_demands(row)
    if category_demands:
        material_label = ' + '.join(
            f"{d['quantity']} × {d['category']}" for d in category_demands
        )
    else:
        material_label = row['numero_inventaire'] or 'Objet non lié à l\'inventaire'
        if row['marque'] or row['modele']:
            material_label = f"{material_label} — {' '.join(p for p in [row['marque'], row['modele']] if p)}"

    return render_template(
        'reservation_conversion.html',
        reservation=row,
        reservation_items=_extract_reservation_items(row),
        reservation_category_demands=category_demands,
        start_dt=start_dt,
        now_dt=now_dt,
        can_convert_now=can_convert_now,
        material_label=material_label,
    )



@bp.route('/reservations', methods=['GET', 'POST'])
def reservations():
    conn = get_app_db()
    now_dt = datetime.now()

    expire_old_reservations(conn, now_dt=now_dt)

    if request.method == 'POST':
        personne_id = (request.form.get('personne_id') or '').strip()
        date_reservation_raw = (request.form.get('date_reservation') or '').strip()
        date_fin_raw = (request.form.get('date_fin_reservation') or '').strip()
        notes = (request.form.get('notes') or '').strip()
        statut = (request.form.get('statut') or 'confirmee').strip().lower()
        if statut not in ('demande', 'confirmee'):
            statut = 'confirmee'

        # Multi-items (texte libre et/ou matériel précis legacy)
        items_descriptions = request.form.getlist('res_items_description[]')
        items_materiel_ids = request.form.getlist('res_items_materiel_id[]')
        
        # Nettoyer et filtrer
        items = []
        for desc, mid in zip(items_descriptions, items_materiel_ids):
            desc = (desc or '').strip()
            mid = (mid or '').strip()
            if desc:  # Au moins description requise (mid peut être vide = texte libre)
                items.append({'description': desc, 'materiel_id': int(mid) if mid else None})
        
        # Nouvelles demandes par catégorie (quantité)
        cat_names = request.form.getlist('res_category_name[]')
        cat_qtys = request.form.getlist('res_category_qty[]')
        category_demands = []
        for cat, qty in zip(cat_names, cat_qtys):
            cat = (cat or '').strip()
            qty_raw = (qty or '').strip()
            if not cat:
                continue
            try:
                qty_val = int(qty_raw)
            except (TypeError, ValueError):
                qty_val = 0
            if qty_val > 0:
                category_demands.append({'category': cat, 'quantity': qty_val})

        reservation_dt = parse_form_datetime_local(date_reservation_raw)
        reservation_end_dt = parse_form_datetime_local(date_fin_raw) if date_fin_raw else None

        if not personne_id or not personne_id.isdigit():
            flash('Veuillez sélectionner une personne valide.', 'danger')
        elif (not items and not category_demands) or not reservation_dt:
            flash('Veuillez renseigner la personne, ajouter au moins une catégorie (ou un objet libre), et la date de réservation.', 'danger')
        elif reservation_dt <= now_dt:
            flash('La date de réservation doit être dans le futur.', 'danger')
        elif reservation_end_dt and reservation_end_dt < reservation_dt:
            flash('La date de fin doit être égale ou ultérieure à la date de début.', 'danger')
        else:
            # Si pas de date fin, utiliser la date début (réservation mono-jour)
            if not reservation_end_dt:
                reservation_end_dt = reservation_dt
            
            main_materiel_id = _first_linked_material_id(items)
            conflicts = []
            conflicts.extend(_reservation_conflicts_for_items(
                conn,
                items,
                reservation_dt,
                reservation_end_dt,
                now_dt,
            ))
            conflicts.extend(_reservation_conflicts_for_categories(
                conn,
                category_demands,
                reservation_dt,
                reservation_end_dt,
                now_dt,
            ))
            
            if conflicts:
                for msg in conflicts:
                    flash(msg, 'warning')
            else:
                items_json = json.dumps(items, ensure_ascii=False)
                categories_json = json.dumps(category_demands, ensure_ascii=False)
                try:
                    conn.execute(
                        """
                        INSERT INTO reservations (personne_id, materiel_id, date_reservation, date_fin_reservation, statut, notes, items_json, demande_categories_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(personne_id),
                            main_materiel_id,
                            format_db_datetime(reservation_dt),
                            format_db_datetime(reservation_end_dt),
                            statut,
                            notes,
                            items_json,
                            categories_json,
                        ),
                    )
                    conn.commit()
                    flash('Réservation enregistrée avec succès.', 'success')
                    return redirect(url_for('reservations.reservations'))
                except Exception as e:
                    import sqlite3 as _sq
                    if isinstance(e, _sq.IntegrityError):
                        flash('Personne ou matériel invalide — vérifiez les informations saisies.', 'danger')
                    else:
                        raise

    # GET et POST-validation-échouée : afficher la page des réservations
    reservations_rows = conn.execute(
        """
         SELECT r.*, pe.nom, pe.prenom, pe.categorie,
               inv.numero_inventaire, inv.type_materiel, inv.marque, inv.modele, inv.etat
             , p.id AS linked_pret_id
        FROM reservations r
        JOIN personnes pe ON pe.id = r.personne_id
         LEFT JOIN inventaire inv ON inv.id = r.materiel_id
         LEFT JOIN prets p ON p.id = r.pret_id
        ORDER BY r.date_reservation ASC
        """
    ).fetchall()

    personnes = conn.execute(
        'SELECT id, nom, prenom, categorie, classe FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()

    inventaire = conn.execute(
        """
        SELECT id, numero_inventaire, type_materiel, marque, modele, etat
        FROM inventaire
        WHERE actif = 1 AND etat != 'hors_service'
        ORDER BY numero_inventaire ASC
        """
    ).fetchall()

    categories = conn.execute(
        'SELECT nom FROM categories_materiel ORDER BY nom'
    ).fetchall()

    prets_actifs = conn.execute(
        """
        SELECT p.id, p.date_emprunt, p.duree_pret_heures, p.duree_pret_jours,
               p.date_retour_prevue, p.descriptif_objets,
               pe.nom, pe.prenom
        FROM prets p
        JOIN personnes pe ON pe.id = p.personne_id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
        """
    ).fetchall()

    planning_items = []
    for r in reservations_rows:
        start_dt = parse_db_datetime(r['date_reservation'])
        end_dt = parse_db_datetime(r['date_fin_reservation']) if r['date_fin_reservation'] else start_dt
        if not start_dt:
            continue
        if not end_dt or end_dt < start_dt:
            end_dt = start_dt

        cat_demands = _extract_reservation_category_demands(r)
        if cat_demands:
            item_title = ' + '.join(f"{d['quantity']}×{d['category']}" for d in cat_demands)
        else:
            item_title = r['numero_inventaire'] or 'Objet libre'

        planning_items.append({
            'kind': 'reservation',
            'id': int(r['id']),
            'status': r['statut'],
            'title': f"{r['nom']} {r['prenom']} — {item_title}",
            'start': format_db_datetime(start_dt),
            'end': format_db_datetime(end_dt),
            'url': url_for('reservations.convertir_reservation', reservation_id=r['id'])
            if r['statut'] in ('confirmee', 'demande') else None,
        })

    for p in prets_actifs:
        start_dt = parse_db_datetime(p['date_emprunt'])
        if not start_dt:
            continue
        end_dt = compute_expected_return_datetime(
            conn,
            start_dt,
            p['duree_pret_heures'],
            p['duree_pret_jours'],
            p['date_retour_prevue'],
        )
        if not end_dt or end_dt < start_dt:
            end_dt = start_dt

        planning_items.append({
            'kind': 'pret',
            'id': int(p['id']),
            'status': 'actif',
            'title': f"{p['nom']} {p['prenom']} — {p['descriptif_objets']}",
            'start': format_db_datetime(start_dt),
            'end': format_db_datetime(end_dt),
            'url': url_for('prets.detail_pret', pret_id=p['id']),
        })

    return render_template(
        'reservations.html',
        reservations=reservations_rows,
        personnes=personnes,
        inventaire=inventaire,
        categories=categories,
        planning_items=planning_items,
        now_dt=now_dt,
        mode_scanner=get_setting('mode_scanner', 'les_deux'),
    )


@bp.route('/reservations/<int:reservation_id>/modifier', methods=['GET', 'POST'])
@admin_required
def modifier_reservation(reservation_id):
    conn = get_app_db()
    now_dt = datetime.now()

    row = conn.execute(
        '''
        SELECT r.*, pe.nom, pe.prenom, pe.categorie,
               inv.numero_inventaire, inv.type_materiel, inv.marque, inv.modele
        FROM reservations r
        JOIN personnes pe ON pe.id = r.personne_id
        LEFT JOIN inventaire inv ON inv.id = r.materiel_id
        WHERE r.id = ?
        ''',
        (reservation_id,),
    ).fetchone()

    if not row:
        flash('Réservation introuvable.', 'danger')
        return redirect(url_for('reservations.reservations'))

    if row['statut'] not in ('demande', 'confirmee', 'expiree'):
        flash('Cette réservation ne peut plus être modifiée.', 'warning')
        return redirect(url_for('reservations.reservations'))

    if request.method == 'POST':
        personne_id = (request.form.get('personne_id') or '').strip()
        date_reservation_raw = (request.form.get('date_reservation') or '').strip()
        date_fin_raw = (request.form.get('date_fin_reservation') or '').strip()
        notes = (request.form.get('notes') or '').strip()
        statut = (request.form.get('statut') or row['statut'] or 'confirmee').strip().lower()
        if statut not in ('demande', 'confirmee'):
            statut = 'confirmee'

        items_descriptions = request.form.getlist('res_items_description[]')
        items_materiel_ids = request.form.getlist('res_items_materiel_id[]')
        items = []
        for desc, mid in zip(items_descriptions, items_materiel_ids):
            desc = (desc or '').strip()
            mid = (mid or '').strip()
            if desc:
                items.append({'description': desc, 'materiel_id': int(mid) if mid else None})

        cat_names = request.form.getlist('res_category_name[]')
        cat_qtys = request.form.getlist('res_category_qty[]')
        category_demands = []
        for cat, qty in zip(cat_names, cat_qtys):
            cat = (cat or '').strip()
            qty_raw = (qty or '').strip()
            if not cat:
                continue
            try:
                qty_val = int(qty_raw)
            except (TypeError, ValueError):
                qty_val = 0
            if qty_val > 0:
                category_demands.append({'category': cat, 'quantity': qty_val})

        reservation_dt = parse_form_datetime_local(date_reservation_raw)
        reservation_end_dt = parse_form_datetime_local(date_fin_raw) if date_fin_raw else None

        if not personne_id or (not items and not category_demands) or not reservation_dt:
            flash('Veuillez renseigner la personne, ajouter au moins une catégorie (ou un objet libre), et la date de réservation.', 'danger')
        elif reservation_end_dt and reservation_end_dt < reservation_dt:
            flash('La date de fin doit être égale ou ultérieure à la date de début.', 'danger')
        else:
            if not reservation_end_dt:
                reservation_end_dt = reservation_dt

            main_materiel_id = _first_linked_material_id(items)
            conflicts = []
            conflicts.extend(_reservation_conflicts_for_items(
                conn,
                items,
                reservation_dt,
                reservation_end_dt,
                now_dt,
                exclude_reservation_id=reservation_id,
            ))
            conflicts.extend(_reservation_conflicts_for_categories(
                conn,
                category_demands,
                reservation_dt,
                reservation_end_dt,
                now_dt,
                exclude_reservation_id=reservation_id,
            ))
            if conflicts:
                for msg in conflicts:
                    flash(msg, 'warning')
            else:
                conn.execute(
                    '''
                    UPDATE reservations
                    SET personne_id = ?,
                        materiel_id = ?,
                        date_reservation = ?,
                        date_fin_reservation = ?,
                        statut = ?,
                        notes = ?,
                        items_json = ?,
                        demande_categories_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (
                        int(personne_id),
                        main_materiel_id,
                        format_db_datetime(reservation_dt),
                        format_db_datetime(reservation_end_dt),
                        statut,
                        notes,
                        json.dumps(items, ensure_ascii=False),
                        json.dumps(category_demands, ensure_ascii=False),
                        reservation_id,
                    ),
                )
                conn.commit()
                flash('Réservation modifiée avec succès.', 'success')
                return redirect(url_for('reservations.reservations'))

    personnes = conn.execute(
        'SELECT id, nom, prenom, categorie, classe FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()
    inventaire = conn.execute(
        '''
        SELECT id, numero_inventaire, type_materiel, marque, modele, etat
        FROM inventaire
        WHERE actif = 1 AND etat != 'hors_service'
        ORDER BY numero_inventaire ASC
        '''
    ).fetchall()
    categories = conn.execute(
        'SELECT nom FROM categories_materiel ORDER BY nom'
    ).fetchall()

    return render_template(
        'modifier_reservation.html',
        reservation=row,
        reservation_items=_extract_reservation_items(row),
        reservation_category_demands=_extract_reservation_category_demands(row),
        personnes=personnes,
        inventaire=inventaire,
        categories=categories,
        now_dt=now_dt,
        mode_scanner=get_setting('mode_scanner', 'les_deux'),
    )


@bp.route('/reservations/<int:reservation_id>/supprimer', methods=['POST'])
@admin_required
def supprimer_reservation(reservation_id):
    conn = get_app_db()
    row = conn.execute('SELECT id, pret_id, statut FROM reservations WHERE id = ?', (reservation_id,)).fetchone()
    if not row:
        flash('Réservation introuvable.', 'danger')
        return redirect(url_for('reservations.reservations'))

    if row['pret_id']:
        flash('Impossible de supprimer une réservation liée à un prêt. Supprimez d\'abord le prêt lié.', 'warning')
        return redirect(url_for('reservations.reservations'))

    conn.execute('DELETE FROM reservations WHERE id = ?', (reservation_id,))
    conn.commit()
    flash('Réservation supprimée.', 'success')
    return redirect(url_for('reservations.reservations'))


@bp.route('/reservations/<int:reservation_id>/annuler', methods=['POST'])
@admin_required
def annuler_reservation(reservation_id):
    conn = get_app_db()
    row = conn.execute('SELECT id, statut FROM reservations WHERE id = ?', (reservation_id,)).fetchone()
    if not row:
        flash('Réservation introuvable.', 'danger')
        return redirect(url_for('reservations.reservations'))

    if row['statut'] in ('annulee',):
        flash('Cette réservation est déjà clôturée.', 'warning')
        return redirect(url_for('reservations.reservations'))

    conn.execute(
        """
        UPDATE reservations
        SET statut = 'annulee', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (reservation_id,),
    )
    conn.commit()
    flash('Réservation annulée.', 'success')
    return redirect(url_for('reservations.reservations'))
