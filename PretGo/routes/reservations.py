"""PretGo — Blueprint : reservations"""

from datetime import datetime
import json

from flask import Blueprint, flash, redirect, render_template, request, url_for

from reservations_logic import (
    expire_old_reservations,
    find_creation_conflicts_for_reservation,
    format_db_datetime,
    parse_form_datetime_local,
)
from database import get_setting
from utils import get_app_db, admin_required

bp = Blueprint('reservations', __name__)


@bp.route('/reservations/<int:reservation_id>/convertir')
def convertir_reservation(reservation_id):
    conn = get_app_db()
    row = conn.execute(
        '''
        SELECT id, statut
        FROM reservations
        WHERE id = ?
        ''',
        (reservation_id,),
    ).fetchone()

    if not row:
        flash('Réservation introuvable.', 'danger')
        return redirect(url_for('reservations.reservations'))

    if row['statut'] not in ('confirmee', 'demande'):
        flash('Cette réservation ne peut plus être convertie en prêt.', 'warning')
        return redirect(url_for('reservations.reservations'))

    return redirect(url_for('prets.nouveau_pret', reservation_id=reservation_id))


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

        # Multi-items
        items_descriptions = request.form.getlist('res_items_description[]')
        items_materiel_ids = request.form.getlist('res_items_materiel_id[]')
        
        # Nettoyer et filtrer
        items = []
        for desc, mid in zip(items_descriptions, items_materiel_ids):
            desc = (desc or '').strip()
            mid = (mid or '').strip()
            if desc:  # Au moins description requise (mid peut être vide = texte libre)
                items.append({'description': desc, 'materiel_id': int(mid) if mid else None})
        
        reservation_dt = parse_form_datetime_local(date_reservation_raw)
        reservation_end_dt = parse_form_datetime_local(date_fin_raw) if date_fin_raw else None

        if not personne_id or not items or not reservation_dt:
            flash('Veuillez renseigner la personne, ajouter au moins un objet, et la date de réservation.', 'danger')
        elif reservation_dt <= now_dt:
            flash('La date de réservation doit être dans le futur.', 'danger')
        elif reservation_end_dt and reservation_end_dt < reservation_dt:
            flash('La date de fin doit être égale ou ultérieure à la date de début.', 'danger')
        else:
            # Si pas de date fin, utiliser la date début (réservation mono-jour)
            if not reservation_end_dt:
                reservation_end_dt = reservation_dt
            
            # Utiliser le premier materiel_id pour les conflits (s'il existe)
            main_materiel_id = next((i['materiel_id'] for i in items if i['materiel_id']), None)
            
            conflicts = []
            if main_materiel_id:  # Vérifier conflits seulement si on a un ID d'inventaire
                conflicts = find_creation_conflicts_for_reservation(
                    conn,
                    materiel_id=main_materiel_id,
                    reservation_dt=reservation_dt,
                    reservation_end_dt=reservation_end_dt,
                    now_dt=now_dt,
                )
            
            if conflicts:
                for msg in conflicts:
                    flash(msg, 'warning')
            else:
                items_json = json.dumps(items, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO reservations (personne_id, materiel_id, date_reservation, date_fin_reservation, statut, notes, items_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(personne_id), main_materiel_id, format_db_datetime(reservation_dt), format_db_datetime(reservation_end_dt), statut, notes, items_json),
                )
                conn.commit()
                flash('Réservation enregistrée avec succès.', 'success')
                return redirect(url_for('reservations.reservations'))

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

    return render_template(
        'reservations.html',
        reservations=reservations_rows,
        personnes=personnes,
        inventaire=inventaire,
        now_dt=now_dt,
        mode_scanner=get_setting('mode_scanner', 'les_deux'),
    )


@bp.route('/reservations/<int:reservation_id>/annuler', methods=['POST'])
@admin_required
def annuler_reservation(reservation_id):
    conn = get_app_db()
    row = conn.execute('SELECT id, statut FROM reservations WHERE id = ?', (reservation_id,)).fetchone()
    if not row:
        flash('Réservation introuvable.', 'danger')
        return redirect(url_for('reservations.reservations'))

    if row['statut'] in ('annulee', 'expiree'):
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
