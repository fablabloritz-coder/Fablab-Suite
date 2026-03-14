"""PretGo — Blueprint : prets"""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from database import get_setting
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

@bp.route('/nouveau-pret', methods=['GET', 'POST'])
def nouveau_pret():
    conn = get_app_db()

    if request.method == 'POST':
        personne_id = request.form.get('personne_id')
        notes = request.form.get('notes', '').strip()
        lieu_id = request.form.get('lieu_id', '').strip() or None

        # ── Récupération des items (multi-matériel) ──
        items_desc = request.form.getlist('items_description[]')
        items_mat = request.form.getlist('items_materiel_id[]')

        # Nettoyer les items vides
        items = []
        for i in range(len(items_desc)):
            desc = items_desc[i].strip() if i < len(items_desc) else ''
            mat_id = items_mat[i].strip() if i < len(items_mat) else ''
            if desc:
                items.append((desc, int(mat_id) if mat_id else None))

        # ── Gestion de la durée (heures ou jours) ──
        duree_pret_jours, duree_pret_heures, date_retour_prevue, duree_type = _parse_duree(request.form)

        if not personne_id or not items:
            flash('Veuillez sélectionner une personne et ajouter au moins un objet.', 'danger')
        else:
            # Construire le descriptif combiné
            descriptif = ' + '.join(desc for desc, _ in items)
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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
        duree_defaut=duree_defaut,
        unite_defaut=unite_defaut,
        heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
        mode_scanner=get_setting('mode_scanner', 'les_deux')
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

        # ── Récupération des items (multi-matériel) ──
        items_desc = request.form.getlist('items_description[]')
        items_mat = request.form.getlist('items_materiel_id[]')
        items = []
        for i in range(len(items_desc)):
            desc = items_desc[i].strip() if i < len(items_desc) else ''
            mat_id = items_mat[i].strip() if i < len(items_mat) else ''
            if desc:
                items.append((desc, int(mat_id) if mat_id else None))

        # ── Gestion de la durée ──
        duree_pret_jours, duree_pret_heures, date_retour_prevue, duree_type = _parse_duree(request.form)

        if not personne_id or not items:
            flash('Veuillez sélectionner une personne et ajouter au moins un objet.', 'danger')
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
        personnes=personnes,
        categories=categories,
        lieux=lieux,
        duree_defaut=duree_defaut,
        unite_defaut=unite_defaut,
        heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
        mode_scanner=get_setting('mode_scanner', 'les_deux')
    )



@bp.route('/pret/supprimer/<int:pret_id>', methods=['POST'])
@admin_required
def supprimer_pret(pret_id):
    conn = get_app_db()
    pret = conn.execute('SELECT materiel_id, retour_confirme FROM prets WHERE id = ?', (pret_id,)).fetchone()
    if pret and not pret['retour_confirme']:
        liberer_materiels_pret(conn, pret_id, pret_row=pret)
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


