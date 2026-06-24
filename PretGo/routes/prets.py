"""PretGo — Blueprint : prets"""
import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from database import get_setting
from reservations_logic import (
    compute_expected_return_datetime,
    find_reservation_conflicts_for_loan,
    parse_db_datetime,
)
from utils import get_app_db, admin_required, calculer_annee_scolaire, liberer_materiels_pret
from datetime import datetime, timedelta

bp = Blueprint('prets', __name__)


def _parse_duree(form):
    """Parse les champs de durée depuis le formulaire.
    Retourne (duree_pret_jours, duree_pret_heures, date_retour_prevue, duree_type)."""
    duree_type = form.get('duree_type', 'defaut')
    duree_pret_jours = None
    duree_pret_heures = None
    date_retour_prevue = None

    if duree_type == 'heures':
        h = form.get('duree_heures', '').strip()
        if h:
            try:
                duree_pret_heures = float(h)
            except ValueError:
                pass
    elif duree_type == 'jours':
        j = form.get('duree_jours', '').strip()
        if j:
            try:
                duree_pret_jours = int(j)
            except ValueError:
                pass
    elif duree_type == 'date_precise':
        date_retour_prevue = form.get('date_retour_prevue', '').strip() or None
    elif duree_type == 'fin_journee':
        heure_fin = get_setting('heure_fin_journee', '17:45')
        h_fin, m_fin = (int(x) for x in heure_fin.split(':'))
        now = datetime.now()
        fin_journee = now.replace(hour=h_fin, minute=m_fin, second=0, microsecond=0)
        if fin_journee > now:
            delta = (fin_journee - now).total_seconds() / 3600
            duree_pret_heures = round(delta, 2)
        else:
            duree_pret_heures = 0.5

    return duree_pret_jours, duree_pret_heures, date_retour_prevue, duree_type


def _material_ids_from_items(items):
    """Retourne la liste unique des IDs matériel liés aux items."""
    return sorted({mat_id for _, mat_id in items if mat_id})


def _extract_reservation_items(row):
    """Retourne les items d'une réservation (JSON multi-items + fallback legacy)."""
    if not row:
        return []
    items = []
    raw = row['items_json'] if 'items_json' in row.keys() else None
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                for item in loaded:
                    if not isinstance(item, dict):
                        continue
                    desc = (item.get('description') or '').strip()
                    mat_id = item.get('materiel_id')
                    try:
                        mat_id = int(mat_id) if mat_id not in (None, '') else None
                    except (TypeError, ValueError):
                        mat_id = None
                    if desc:
                        items.append({'description': desc, 'materiel_id': mat_id})
        except Exception:
            items = []

    if not items:
        desc = (row['numero_inventaire'] or '').strip() if 'numero_inventaire' in row.keys() else ''
        if desc:
            items.append({'description': desc, 'materiel_id': row['materiel_id']})
    return items


def _extract_reservation_category_demands(row):
    """Retourne les demandes explicites par catégorie d'une réservation."""
    if not row:
        return []
    demands = []
    raw = row['demande_categories_json'] if 'demande_categories_json' in row.keys() else None
    if not raw:
        return demands
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            for item in loaded:
                if not isinstance(item, dict):
                    continue
                category = (item.get('category') or item.get('categorie') or '').strip()
                try:
                    quantity = int(item.get('quantity', 0))
                except (TypeError, ValueError):
                    quantity = 0
                if category and quantity > 0:
                    demands.append({'category': category, 'quantity': quantity})
    except Exception:
        return []
    return demands


def _inventory_map(conn, material_ids):
    """Retourne un dict inventaire par ID pour enrichir les labels en template."""
    ids = sorted({int(mid) for mid in material_ids if mid})
    if not ids:
        return {}
    placeholders = ','.join('?' for _ in ids)
    rows = conn.execute(
        f'''
        SELECT id, numero_inventaire, type_materiel, marque, modele
        FROM inventaire
        WHERE id IN ({placeholders})
        ''',
        ids,
    ).fetchall()
    return {int(r['id']): r for r in rows}


def _split_items_by_kind(items):
    """Sépare les items liés à l'inventaire du texte libre."""
    linked_items = []
    free_text_items = []

    for item in items or []:
        if not item:
            continue

        if hasattr(item, 'keys'):
            desc = (item.get('description') or item.get('descriptif_objets') or '').strip()
            mat_id = item.get('materiel_id')
        else:
            desc = (item[0] or '').strip() if len(item) > 0 else ''
            mat_id = item[1] if len(item) > 1 else None

        try:
            mat_id = int(mat_id) if mat_id not in (None, '') else None
        except (TypeError, ValueError):
            mat_id = None

        if not desc and mat_id is None:
            continue

        payload = {'description': desc, 'materiel_id': mat_id}
        if mat_id:
            linked_items.append(payload)
        else:
            free_text_items.append(payload)

    return linked_items, free_text_items


def _items_from_form(form):
    """Lit les items du formulaire avec support du nouveau champ texte libre."""
    linked_descriptions = form.getlist('linked_items_description[]')
    linked_materiel_ids = form.getlist('linked_items_materiel_id[]')
    free_text_items = form.getlist('free_text_items[]')

    legacy_descriptions = form.getlist('items_description[]')
    legacy_materiel_ids = form.getlist('items_materiel_id[]')

    linked_items = []
    if linked_descriptions or linked_materiel_ids:
        max_len = max(len(linked_descriptions), len(linked_materiel_ids))
        for i in range(max_len):
            desc = linked_descriptions[i].strip() if i < len(linked_descriptions) else ''
            mat_raw = linked_materiel_ids[i].strip() if i < len(linked_materiel_ids) else ''
            if not desc:
                continue
            try:
                mat_id = int(mat_raw) if mat_raw else None
            except (TypeError, ValueError):
                mat_id = None
            linked_items.append({'description': desc, 'materiel_id': mat_id})
    else:
        max_len = max(len(legacy_descriptions), len(legacy_materiel_ids))
        for i in range(max_len):
            desc = legacy_descriptions[i].strip() if i < len(legacy_descriptions) else ''
            mat_raw = legacy_materiel_ids[i].strip() if i < len(legacy_materiel_ids) else ''
            if not desc:
                continue
            try:
                mat_id = int(mat_raw) if mat_raw else None
            except (TypeError, ValueError):
                mat_id = None
            linked_items.append({'description': desc, 'materiel_id': mat_id})

    free_items = []
    for text in free_text_items:
        text = (text or '').strip()
        if text:
            free_items.append({'description': text, 'materiel_id': None})

    all_items = linked_items + free_items
    all_items_as_tuples = [
        (item['description'], item['materiel_id'])
        for item in all_items
    ]

    return linked_items, free_items, all_items_as_tuples

@bp.route('/nouveau-pret', methods=['GET', 'POST'])
def nouveau_pret():
    conn = get_app_db()
    reservation_prefill = None
    reservation_prefill_items = []
    reservation_prefill_linked_items = []
    reservation_prefill_free_text_items = []
    reservation_prefill_categories = []
    form_state = None

    reservation_id_raw = (request.values.get('reservation_id') or '').strip()
    reservation_id = int(reservation_id_raw) if reservation_id_raw.isdigit() else None

    if reservation_id:
        reservation_prefill = conn.execute(
            '''
            SELECT r.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
                   inv.numero_inventaire, inv.type_materiel, inv.marque, inv.modele
            FROM reservations r
            JOIN personnes pe ON pe.id = r.personne_id
            LEFT JOIN inventaire inv ON inv.id = r.materiel_id
            WHERE r.id = ?
            ''',
            (reservation_id,),
        ).fetchone()
        if reservation_prefill and reservation_prefill['statut'] not in ('confirmee', 'demande', 'expiree'):
            flash('Cette réservation ne peut plus être convertie en prêt.', 'warning')
            return redirect(url_for('reservations.reservations'))
        reservation_prefill_items = _extract_reservation_items(reservation_prefill)
        reservation_prefill_categories = _extract_reservation_category_demands(reservation_prefill)
        reservation_prefill_linked_items, reservation_prefill_free_text_items = _split_items_by_kind(reservation_prefill_items)

    if request.method == 'POST':
        personne_id = request.form.get('personne_id')
        notes = request.form.get('notes', '').strip()
        lieu_id = request.form.get('lieu_id', '').strip() or None
        form_state = {
            'personne_id': personne_id,
            'notes': notes,
            'lieu_id': lieu_id,
            'duree_type': request.form.get('duree_type', 'aucune'),
            'duree_heures': request.form.get('duree_heures', '').strip(),
            'duree_jours': request.form.get('duree_jours', '').strip(),
            'date_retour_prevue': request.form.get('date_retour_prevue', '').strip(),
            'linked_items': [],
            'free_text_items': [],
        }

        linked_items, free_text_items, items = _items_from_form(request.form)
        form_state['linked_items'] = linked_items
        form_state['free_text_items'] = free_text_items

        # ── Gestion de la durée (heures ou jours) ──
        duree_pret_jours, duree_pret_heures, date_retour_prevue, duree_type = _parse_duree(request.form)

        if not personne_id or not items:
            flash('Veuillez sélectionner une personne et ajouter au moins un objet.', 'danger')
        else:
            # Protection contre la double soumission : vérification de disponibilité au moment de la soumission
            mat_ids = [mid for _, mid in items if mid]
            already_loaned = []
            if mat_ids:
                ph = ','.join('?' * len(mat_ids))
                already_loaned = conn.execute(
                    f"SELECT type_materiel, numero_inventaire FROM inventaire "
                    f"WHERE id IN ({ph}) AND etat != 'disponible'",
                    mat_ids
                ).fetchall()

            if already_loaned:
                labels = [f"{r['type_materiel']} {r['numero_inventaire']}".strip() for r in already_loaned]
                flash(f"Prêt refusé : objet(s) déjà emprunté(s) ou indisponible(s) : {', '.join(labels)}", 'danger')
            else:
                now_dt = datetime.now()
                expected_return_dt = compute_expected_return_datetime(
                    conn,
                    now_dt,
                    duree_pret_heures,
                    duree_pret_jours,
                    date_retour_prevue,
                )
                conflicts = find_reservation_conflicts_for_loan(
                    conn,
                    _material_ids_from_items(items),
                    expected_return_dt,
                    now_dt=now_dt,
                    exclude_reservation_id=reservation_id,
                )

                if conflicts:
                    for conflict in conflicts:
                        flash(conflict['message'], 'warning')
                    conflict_labels = sorted({conflict['material_label'] for conflict in conflicts if conflict.get('material_label')})
                    if conflict_labels:
                        flash('Objets en conflit: ' + ', '.join(conflict_labels), 'danger')
                    flash('Prêt refusé: conflit avec une réservation future. Les informations saisies ont été conservées.', 'danger')
                else:
                    # Construire le descriptif combiné
                    descriptif = ' + '.join(desc for desc, _ in items)
                    now = now_dt.strftime('%Y-%m-%d %H:%M:%S')

                    # Snapshot de la classe au moment du prêt
                    pers = conn.execute('SELECT classe FROM personnes WHERE id = ?', (personne_id,)).fetchone()
                    classe_snap = pers['classe'] if pers else ''
                    annee_scol = calculer_annee_scolaire()

                    cursor = conn.execute(
                        '''INSERT INTO prets (personne_id, descriptif_objets, date_emprunt,
                           notes, duree_pret_jours, duree_pret_heures, type_duree, date_retour_prevue,
                           classe_snapshot, annee_scolaire, lieu_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (personne_id, descriptif, now, notes, duree_pret_jours, duree_pret_heures,
                         duree_type, date_retour_prevue, classe_snap, annee_scol, lieu_id)
                    )
                    pret_id = cursor.lastrowid

                    # Insérer chaque item dans pret_materiels
                    for desc, mat_id in items:
                        conn.execute(
                            'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                            (pret_id, mat_id, desc)
                        )
                        if mat_id:
                            conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (mat_id,))

                    if reservation_id:
                        conn.execute(
                            '''
                            UPDATE reservations
                            SET statut = 'convertie_en_pret', pret_id = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            ''',
                            (pret_id, reservation_id),
                        )

                    conn.commit()
                    flash('Prêt enregistré avec succès !', 'success')
                    return redirect(url_for('core.index'))

    personnes = conn.execute(
        'SELECT * FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()
    categories = conn.execute(
        'SELECT * FROM categories_materiel ORDER BY nom'
    ).fetchall()
    inventaire = conn.execute(
        "SELECT * FROM inventaire WHERE actif = 1 AND etat = 'disponible' ORDER BY type_materiel, numero_inventaire"
    ).fetchall()
    lieux = conn.execute(
        'SELECT * FROM lieux WHERE actif = 1 ORDER BY nom'
    ).fetchall()

    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    return render_template(
        'nouveau_pret.html',
        personnes=personnes,
        categories=categories,
        inventaire=inventaire,
        lieux=lieux,
        reservation_prefill=reservation_prefill,
        reservation_prefill_items=reservation_prefill_items,
        reservation_prefill_linked_items=reservation_prefill_linked_items,
        reservation_prefill_free_text_items=reservation_prefill_free_text_items,
        reservation_prefill_categories=reservation_prefill_categories,
        form_state=form_state,
        linked_items=(form_state['linked_items'] if form_state else reservation_prefill_linked_items),
        free_text_items=(form_state['free_text_items'] if form_state else reservation_prefill_free_text_items),
        inventory_by_id=_inventory_map(
            conn,
            [
                item['materiel_id']
                for item in (
                    (form_state or {}).get('linked_items')
                    or reservation_prefill_linked_items
                )
                if item.get('materiel_id')
            ],
        ),
        duree_defaut=duree_defaut,
        unite_defaut=unite_defaut,
        heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
        mode_scanner=get_setting('mode_scanner', 'les_deux'),
        scanner_douchette_auto_validate=get_setting('scanner_douchette_auto_validate', '1')
    )



@bp.route('/retour')
def retour():
    conn = get_app_db()
    recherche = request.args.get('q', '').strip()

    if recherche:
        prets = conn.execute('''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE p.retour_confirme = 0
            AND (pe.nom LIKE ? OR pe.prenom LIKE ? OR p.descriptif_objets LIKE ?)
            ORDER BY p.date_emprunt DESC
        ''', (f'%{recherche}%', f'%{recherche}%', f'%{recherche}%')).fetchall()
    else:
        prets = conn.execute('''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE p.retour_confirme = 0
            ORDER BY p.date_emprunt DESC
        ''').fetchall()

    return render_template('retour.html', prets=prets, recherche=recherche)



@bp.route('/retour/<int:pret_id>', methods=['POST'])
def confirmer_retour(pret_id):
    conn = get_app_db()
    signature = request.form.get('signature', '')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Récupérer le materiel_id legacy avant de confirmer le retour
    pret = conn.execute('SELECT materiel_id FROM prets WHERE id = ?', (pret_id,)).fetchone()

    conn.execute(
        'UPDATE prets SET date_retour = ?, retour_confirme = 1, signature_retour = ? WHERE id = ?',
        (now, signature, pret_id)
    )
    liberer_materiels_pret(conn, pret_id, pret_row=pret)
    conn.commit()
    flash('Retour confirmé avec succès !', 'success')
    return redirect(url_for('prets.retour'))



@bp.route('/retour/masse', methods=['POST'])
def retour_masse():
    """Confirme le retour de plusieurs prêts en une seule action."""
    pret_ids = request.form.getlist('pret_ids')
    if not pret_ids:
        flash('Aucun prêt sélectionné.', 'warning')
        return redirect(url_for('prets.retour'))

    conn = get_app_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    nb = 0
    for pid in pret_ids:
        if not pid.isdigit():
            continue
        pid = int(pid)
        pret = conn.execute(
            'SELECT materiel_id FROM prets WHERE id = ? AND retour_confirme = 0', (pid,)
        ).fetchone()
        if not pret:
            continue
        conn.execute(
            'UPDATE prets SET date_retour = ?, retour_confirme = 1, signature_retour = ? WHERE id = ?',
            (now, '', pid)
        )
        liberer_materiels_pret(conn, pid, pret_row=pret)
        nb += 1
    conn.commit()
    if nb:
        flash(f'{nb} retour(s) confirmé(s) avec succès !', 'success')
    else:
        flash('Aucun retour effectué.', 'warning')
    return redirect(url_for('prets.retour'))



@bp.route('/pret/<int:pret_id>')
def detail_pret(pret_id):
    conn = get_app_db()
    pret = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
               l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.id = ?
    ''', (pret_id,)).fetchone()

    if not pret:
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('core.index'))

    # Charger les items multi-matériel
    pret_items = conn.execute('''
        SELECT pm.*, inv.marque, inv.modele, inv.numero_inventaire, inv.image
        FROM pret_materiels pm
        LEFT JOIN inventaire inv ON pm.materiel_id = inv.id
        WHERE pm.pret_id = ?
    ''', (pret_id,)).fetchall()

    # Rétrocompat : ancien champ materiel_id (pour les prêts créés avant multi-matériel)
    materiel_legacy = None
    if not pret_items and pret['materiel_id']:
        materiel_legacy = conn.execute('''
            SELECT image, marque, modele, numero_inventaire
            FROM inventaire WHERE id = ?
        ''', (pret['materiel_id'],)).fetchone()


    return render_template('detail_pret.html', pret=pret,
                           pret_items=pret_items, materiel_legacy=materiel_legacy)



@bp.route('/pret/modifier/<int:pret_id>', methods=['GET', 'POST'])
@admin_required
def modifier_pret(pret_id):
    conn = get_app_db()

    pret = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.id = ?
    ''', (pret_id,)).fetchone()

    if not pret:
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('core.index'))

    if pret['retour_confirme']:
        flash('Ce prêt est déjà retourné, il ne peut plus être modifié.', 'warning')
        return redirect(url_for('prets.detail_pret', pret_id=pret_id))

    if request.method == 'POST':
        personne_id = request.form.get('personne_id', '').strip()
        notes = request.form.get('notes', '').strip()
        lieu_id = request.form.get('lieu_id', '').strip() or None

        linked_items, free_text_items, items = _items_from_form(request.form)

        # ── Gestion de la durée ──
        duree_pret_jours, duree_pret_heures, date_retour_prevue, duree_type = _parse_duree(request.form)

        if not personne_id or not items:
            flash('Veuillez sélectionner une personne et ajouter au moins un objet.', 'danger')
        else:
            base_dt = parse_db_datetime(pret['date_emprunt']) or datetime.now()
            expected_return_dt = compute_expected_return_datetime(
                conn,
                base_dt,
                duree_pret_heures,
                duree_pret_jours,
                date_retour_prevue,
            )
            conflicts = find_reservation_conflicts_for_loan(
                conn,
                _material_ids_from_items(items),
                expected_return_dt,
                now_dt=datetime.now(),
            )
            if conflicts:
                for conflict in conflicts:
                    flash(conflict['message'], 'warning')
                flash('Modification refusée: conflit avec une réservation future.', 'danger')
            else:
                descriptif = ' + '.join(desc for desc, _ in items)

                liberer_materiels_pret(conn, pret_id, pret_row=pret)

                # Supprimer anciens items et recréer
                conn.execute('DELETE FROM pret_materiels WHERE pret_id = ?', (pret_id,))
                for desc, mat_id in items:
                    conn.execute(
                        'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                        (pret_id, mat_id, desc)
                    )
                    if mat_id:
                        conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (mat_id,))

                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # Mettre à jour le snapshot de classe si la personne change
                pers_nouveau = conn.execute('SELECT classe FROM personnes WHERE id = ?', (personne_id,)).fetchone()
                classe_snap = pers_nouveau['classe'] if pers_nouveau else ''

                conn.execute(
                    '''UPDATE prets SET personne_id=?, descriptif_objets=?, notes=?,
                       duree_pret_jours=?, duree_pret_heures=?, type_duree=?, date_retour_prevue=?,
                       classe_snapshot=?, materiel_id=NULL, lieu_id=?, date_modification=?
                       WHERE id=?''',
                    (personne_id, descriptif, notes, duree_pret_jours, duree_pret_heures,
                     duree_type, date_retour_prevue, classe_snap, lieu_id, now, pret_id)
                )
                conn.commit()
                flash('Prêt modifié avec succès.', 'success')
                return redirect(url_for('prets.detail_pret', pret_id=pret_id))

    # Charger les items existants
    pret_items = conn.execute('''
        SELECT pm.*, inv.marque, inv.modele, inv.numero_inventaire
        FROM pret_materiels pm
        LEFT JOIN inventaire inv ON pm.materiel_id = inv.id
        WHERE pm.pret_id = ?
    ''', (pret_id,)).fetchall()
    pret_linked_items, pret_free_text_items = _split_items_by_kind(pret_items)

    personnes = conn.execute(
        'SELECT * FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()
    categories = conn.execute(
        'SELECT * FROM categories_materiel ORDER BY nom'
    ).fetchall()
    lieux = conn.execute(
        'SELECT * FROM lieux WHERE actif = 1 ORDER BY nom'
    ).fetchall()

    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    return render_template(
        'modifier_pret.html',
        pret=pret,
        pret_items=pret_items,
        pret_linked_items=pret_linked_items,
        pret_free_text_items=pret_free_text_items,
        personnes=personnes,
        categories=categories,
        lieux=lieux,
        duree_defaut=duree_defaut,
        unite_defaut=unite_defaut,
        heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
        mode_scanner=get_setting('mode_scanner', 'les_deux'),
        scanner_douchette_auto_validate=get_setting('scanner_douchette_auto_validate', '1')
    )



@bp.route('/pret/supprimer/<int:pret_id>', methods=['POST'])
@admin_required
def supprimer_pret(pret_id):
    conn = get_app_db()
    pret = conn.execute('SELECT materiel_id, retour_confirme FROM prets WHERE id = ?', (pret_id,)).fetchone()
    if pret and not pret['retour_confirme']:
        liberer_materiels_pret(conn, pret_id, pret_row=pret)

    # Evite les erreurs de contrainte FK pour les prêts issus de conversion de réservation.
    conn.execute(
        '''
        UPDATE reservations
        SET pret_id = NULL,
            statut = CASE WHEN statut = 'convertie_en_pret' THEN 'confirmee' ELSE statut END,
            updated_at = CURRENT_TIMESTAMP
        WHERE pret_id = ?
        ''',
        (pret_id,),
    )

    conn.execute('DELETE FROM pret_materiels WHERE pret_id = ?', (pret_id,))
    conn.execute('DELETE FROM prets WHERE id = ?', (pret_id,))
    conn.commit()
    flash('Prêt supprimé.', 'success')
    return redirect(url_for('core.historique'))



@bp.route('/pret/<int:pret_id>/fiche')
def fiche_pret(pret_id):
    """Générer une fiche de prêt pré-remplie imprimable."""
    conn = get_app_db()
    pret = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
               l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.id = ?
    ''', (pret_id,)).fetchone()

    if not pret:
        flash('Prêt non trouvé.', 'danger')
        return redirect(url_for('core.index'))

    pret_items = conn.execute('''
        SELECT pm.*, inv.marque, inv.modele, inv.numero_inventaire, inv.numero_serie
        FROM pret_materiels pm
        LEFT JOIN inventaire inv ON pm.materiel_id = inv.id
        WHERE pm.pret_id = ?
    ''', (pret_id,)).fetchall()

    nom_etablissement = get_setting('nom_etablissement', '')
    return render_template('fiche_pret.html', pret=pret, pret_items=pret_items,
                           nom_etablissement=nom_etablissement)


