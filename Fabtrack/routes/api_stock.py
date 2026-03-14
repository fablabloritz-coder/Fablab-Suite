"""Routes du module Stock — CRUD articles, mouvements, catégories, fournisseurs, inventaire."""

from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from models import get_db, init_db
from datetime import datetime

bp = Blueprint('stock', __name__, url_prefix='/stock')


def _rows_to_list(rows):
    return [dict(r) for r in rows]


def _parse_materiau_ids(data, is_json):
    """Normalise une liste d'IDs matériaux depuis JSON ou formulaire."""
    raw_values = []
    if is_json:
        raw = data.get('materiau_ids', [])
        if isinstance(raw, list):
            raw_values = raw
        elif isinstance(raw, str):
            raw_values = [v.strip() for v in raw.split(',') if v.strip()]
        elif raw is not None:
            raw_values = [raw]
    else:
        if hasattr(data, 'getlist'):
            raw_values = data.getlist('materiau_ids')
        else:
            raw = data.get('materiau_ids')
            raw_values = [raw] if raw is not None else []

    out = []
    seen = set()
    for val in raw_values:
        try:
            mid = int(val)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _sync_fournisseur_materiaux(db, fourn_id, materiau_ids):
    """Synchronise les matériaux fournis par un fournisseur."""
    db.execute('DELETE FROM stock_fournisseur_materiaux WHERE fournisseur_id=?', (fourn_id,))
    if not materiau_ids:
        return

    placeholders = ','.join('?' for _ in materiau_ids)
    rows = db.execute(
        f'SELECT id FROM materiaux WHERE actif=1 AND id IN ({placeholders})',
        tuple(materiau_ids)
    ).fetchall()
    valid_ids = [int(r['id']) for r in rows]

    for mid in valid_ids:
        db.execute(
            'INSERT OR IGNORE INTO stock_fournisseur_materiaux (fournisseur_id, materiau_id) VALUES (?,?)',
            (fourn_id, mid)
        )


def _prepare_article_stock_values(data):
    """Normalise les valeurs stock article et convertit planche/panneau vers m² si possible."""
    unite = (data.get('unite') or 'pièce').strip()
    longueur_cm = _to_float(data.get('longueur_cm'))
    largeur_cm = _to_float(data.get('largeur_cm'))
    quantite_actuelle = _to_float(data.get('quantite_actuelle')) or 0
    quantite_minimum = _to_float(data.get('quantite_minimum'))
    quantite_maximum = _to_float(data.get('quantite_maximum'))
    threshold_unit_mode = (data.get('threshold_unit_mode') or '').strip().lower()

    # Cas demandé: saisie en nombre de planches/panneaux + dimensions unitaire,
    # puis conversion automatique en surface totale m² pour garder des tableaux cohérents.
    unite_key = unite.lower()
    if unite_key in ('planche', 'panneau') and longueur_cm and largeur_cm and longueur_cm > 0 and largeur_cm > 0:
        surface_unitaire_m2 = (longueur_cm * largeur_cm) / 10000.0
        quantite_actuelle = quantite_actuelle * surface_unitaire_m2
        if quantite_minimum is not None:
            quantite_minimum *= surface_unitaire_m2
        if quantite_maximum is not None:
            quantite_maximum *= surface_unitaire_m2
        unite = 'm²'
    elif threshold_unit_mode == 'planches' and longueur_cm and largeur_cm and longueur_cm > 0 and largeur_cm > 0:
        # Permet de piloter les seuils en nb planches même si l'unité stockée reste en m².
        surface_unitaire_m2 = (longueur_cm * largeur_cm) / 10000.0
        if quantite_minimum is not None:
            quantite_minimum *= surface_unitaire_m2
        if quantite_maximum is not None:
            quantite_maximum *= surface_unitaire_m2

    return {
        'unite': unite,
        'longueur_cm': longueur_cm,
        'largeur_cm': largeur_cm,
        'quantite_actuelle': quantite_actuelle,
        'quantite_minimum': quantite_minimum,
        'quantite_maximum': quantite_maximum,
    }


def _ensure_stock_categories_seed(db):
    """Garantit des catégories minimales pour éviter les pages stock vides."""
    count_row = db.execute('SELECT COUNT(*) AS cnt FROM types_activite').fetchone()
    total = int(count_row['cnt']) if count_row else 0
    if total > 0:
        return total

    defaults = [
        ('Impression 3D', '🖨️', '#f59e0b', 'badge-3d', 'g'),
        ('Découpe Laser', '⚡', '#ef4444', 'badge-laser', 'm²'),
        ('CNC / Fraisage', '⚙️', '#3b82f6', 'badge-cnc', 'm²'),
        ('Impression Papier', '📄', '#22c55e', 'badge-papier', 'feuilles'),
        ('Thermoformage', '🔥', '#a855f7', 'badge-thermo', 'feuilles'),
        ('Bricolage', '🔧', '#6366f1', 'badge-bricolage', ''),
        ('Broderie', '🧵', '#ec4899', 'badge-broderie', ''),
    ]
    for nom, icone, couleur, badge, unite in defaults:
        db.execute(
            'INSERT OR IGNORE INTO types_activite (nom, icone, couleur, badge_class, unite_defaut, actif) VALUES (?,?,?,?,?,1)',
            (nom, icone, couleur, badge, unite),
        )
    db.commit()
    return len(defaults)


# ============================================================
#  PAGES HTML
# ============================================================

@bp.route('/')
def stock_index():
    """Dashboard stock — vue par catégorie, alertes, mouvements récents."""
    db = get_db()
    try:
        _ensure_stock_categories_seed(db)
        categories = db.execute(
            'SELECT * FROM types_activite WHERE actif = 1 ORDER BY nom'
        ).fetchall()
        if not categories:
            categories = db.execute('SELECT * FROM types_activite ORDER BY actif DESC, nom').fetchall()

        articles = db.execute('''
            SELECT a.*, c.nom AS cat_nom, c.couleur AS cat_couleur, c.icone AS cat_icone,
                   f.nom AS fourn_nom, m.nom AS materiau_nom
            FROM stock_articles a
            LEFT JOIN types_activite c ON a.categorie_id = c.id
            LEFT JOIN stock_fournisseurs f ON a.fournisseur_id = f.id
            LEFT JOIN materiaux m ON a.materiau_id = m.id
            WHERE a.actif = 1
            ORDER BY c.nom, a.nom
        ''').fetchall()

        mouvements = db.execute('''
            SELECT m.*, a.nom AS article_nom, a.unite, a.longueur_cm, a.largeur_cm
            FROM stock_mouvements m
            JOIN stock_articles a ON m.article_id = a.id
            ORDER BY m.date DESC, m.id DESC LIMIT 8
        ''').fetchall()

        nb_articles = len(articles)
        nb_alertes = sum(1 for a in articles
                         if a['quantite_minimum'] is not None
                         and a['quantite_actuelle'] < a['quantite_minimum'])

        # Grouper articles par catégorie
        cat_articles = {}
        for a in articles:
            cid = a['categorie_id'] or 0
            cat_articles.setdefault(cid, []).append(a)

        return render_template('stock/index.html', page='stock',
                               categories=categories, articles=articles,
                               cat_articles=cat_articles,
                               mouvements=mouvements,
                               nb_articles=nb_articles, nb_alertes=nb_alertes)
    finally:
        db.close()


@bp.route('/articles')
def stock_articles():
    """Liste des articles avec filtres."""
    db = get_db()
    try:
        _ensure_stock_categories_seed(db)
        categories = db.execute('SELECT * FROM types_activite WHERE actif = 1 ORDER BY nom').fetchall()
        if not categories:
            categories = db.execute('SELECT * FROM types_activite ORDER BY actif DESC, nom').fetchall()
        fournisseurs = db.execute('SELECT * FROM stock_fournisseurs WHERE actif=1 ORDER BY nom').fetchall()
        materiaux = db.execute('SELECT id, nom FROM materiaux WHERE actif=1 ORDER BY nom').fetchall()
        unites = db.execute('SELECT * FROM stock_unites ORDER BY ordre, nom').fetchall()

        # Filtres
        cat_id = request.args.get('categorie', type=int)
        statut = request.args.get('statut', '')
        fourn_id = request.args.get('fournisseur', type=int)
        recherche = request.args.get('q', '').strip()

        query = '''
            SELECT a.*, c.nom AS cat_nom, c.couleur AS cat_couleur,
                   f.nom AS fourn_nom, m.nom AS materiau_nom
            FROM stock_articles a
            LEFT JOIN types_activite c ON a.categorie_id = c.id
            LEFT JOIN stock_fournisseurs f ON a.fournisseur_id = f.id
            LEFT JOIN materiaux m ON a.materiau_id = m.id
            WHERE a.actif = 1
        '''
        params = []

        if cat_id:
            query += ' AND a.categorie_id = ?'
            params.append(cat_id)
        if fourn_id:
            query += ' AND a.fournisseur_id = ?'
            params.append(fourn_id)
        if recherche:
            query += ' AND (a.nom LIKE ? OR m.nom LIKE ? OR a.emplacement LIKE ?)'
            like = f'%{recherche}%'
            params.extend([like, like, like])
        if statut == 'faible':
            query += ' AND a.quantite_minimum IS NOT NULL AND a.quantite_actuelle > 0 AND a.quantite_actuelle < a.quantite_minimum'
        elif statut == 'vide':
            query += ' AND a.quantite_actuelle <= 0'
        elif statut == 'ok':
            query += ' AND (a.quantite_minimum IS NULL OR a.quantite_actuelle >= a.quantite_minimum)'

        query += ' ORDER BY c.nom, a.nom'
        articles = db.execute(query, params).fetchall()

        return render_template('stock/articles.html', page='stock',
                               articles=articles, categories=categories,
                               fournisseurs=fournisseurs, materiaux=materiaux, unites=unites,
                               filtre_cat=cat_id, filtre_statut=statut,
                               filtre_fourn=fourn_id, filtre_q=recherche)
    finally:
        db.close()


@bp.route('/mouvements')
def stock_mouvements():
    """Historique des mouvements avec filtres et pagination."""
    db = get_db()
    try:
        page_num = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page_num - 1) * per_page

        article_id = request.args.get('article', type=int)
        type_mvt = request.args.get('type', '')
        source = request.args.get('source', '')

        query = '''
            SELECT m.*, a.nom AS article_nom, a.unite, a.longueur_cm, a.largeur_cm
            FROM stock_mouvements m
            JOIN stock_articles a ON m.article_id = a.id
            WHERE 1=1
        '''
        count_query = '''
            SELECT COUNT(*) AS cnt FROM stock_mouvements m WHERE 1=1
        '''
        params = []
        count_params = []

        if article_id:
            query += ' AND m.article_id = ?'
            count_query += ' AND m.article_id = ?'
            params.append(article_id)
            count_params.append(article_id)
        if type_mvt:
            query += ' AND m.type = ?'
            count_query += ' AND m.type = ?'
            params.append(type_mvt)
            count_params.append(type_mvt)
        if source:
            query += ' AND m.source = ?'
            count_query += ' AND m.source = ?'
            params.append(source)
            count_params.append(source)

        total = db.execute(count_query, count_params).fetchone()['cnt']
        query += ' ORDER BY m.date DESC, m.id DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])
        mouvements = db.execute(query, params).fetchall()

        articles = db.execute(
            'SELECT id, nom FROM stock_articles WHERE actif=1 ORDER BY nom'
        ).fetchall()

        total_pages = (total + per_page - 1) // per_page

        return render_template('stock/mouvements.html', page='stock',
                               mouvements=mouvements, articles=articles,
                               page_num=page_num, total_pages=total_pages,
                               total=total,
                               filtre_article=article_id, filtre_type=type_mvt,
                               filtre_source=source)
    finally:
        db.close()


@bp.route('/fournisseurs')
def stock_fournisseurs():
    """Liste des fournisseurs."""
    db = get_db()
    try:
        fournisseurs_rows = db.execute('''
            SELECT f.*, COUNT(a.id) AS nb_articles
            FROM stock_fournisseurs f
            LEFT JOIN stock_articles a ON a.fournisseur_id = f.id AND a.actif = 1
            WHERE f.actif = 1
            GROUP BY f.id
            ORDER BY f.nom
        ''').fetchall()

        materiaux_rows = db.execute('''
            SELECT fm.fournisseur_id, m.id AS materiau_id, m.nom AS materiau_nom
            FROM stock_fournisseur_materiaux fm
            JOIN materiaux m ON m.id = fm.materiau_id
            WHERE m.actif = 1
            ORDER BY m.nom
        ''').fetchall()

        by_fournisseur = {}
        for r in materiaux_rows:
            fid = int(r['fournisseur_id'])
            slot = by_fournisseur.setdefault(fid, {'ids': [], 'noms': []})
            slot['ids'].append(int(r['materiau_id']))
            slot['noms'].append(r['materiau_nom'])

        fournisseurs = []
        for fr in fournisseurs_rows:
            item = dict(fr)
            links = by_fournisseur.get(item['id'], {'ids': [], 'noms': []})
            item['materiau_ids'] = links['ids']
            item['materiaux_noms'] = links['noms']
            fournisseurs.append(item)

        materiaux = db.execute(
            'SELECT id, nom FROM materiaux WHERE actif=1 ORDER BY nom'
        ).fetchall()

        return render_template('stock/fournisseurs.html', page='stock',
                               fournisseurs=fournisseurs,
                               materiaux=_rows_to_list(materiaux))
    finally:
        db.close()


@bp.route('/inventaire')
def stock_inventaire():
    """Page d'inventaire physique."""
    db = get_db()
    try:
        _ensure_stock_categories_seed(db)
        categories = db.execute('SELECT * FROM types_activite WHERE actif = 1 ORDER BY nom').fetchall()
        if not categories:
            categories = db.execute('SELECT * FROM types_activite ORDER BY actif DESC, nom').fetchall()
        articles = db.execute('''
            SELECT a.*, c.nom AS cat_nom, c.couleur AS cat_couleur
            FROM stock_articles a
            LEFT JOIN types_activite c ON a.categorie_id = c.id
            WHERE a.actif = 1
            ORDER BY c.nom, a.nom
        ''').fetchall()
        return render_template('stock/inventaire.html', page='stock',
                               categories=categories, articles=articles)
    finally:
        db.close()


@bp.route('/categories')
def stock_categories():
    """Liste des catégories de stock (= types d'activité)."""
    db = get_db()
    try:
        _ensure_stock_categories_seed(db)
        categories = db.execute('''
            SELECT t.*, COUNT(a.id) AS nb_articles
            FROM types_activite t
            LEFT JOIN stock_articles a ON a.categorie_id = t.id AND a.actif = 1
            GROUP BY t.id
            ORDER BY t.actif DESC, t.nom
        ''').fetchall()

        # Auto-répare les installations où les catégories seraient vides après reset.
        if not categories:
            db.close()
            init_db()
            db = get_db()
            categories = db.execute('''
                SELECT t.*, COUNT(a.id) AS nb_articles
                FROM types_activite t
                LEFT JOIN stock_articles a ON a.categorie_id = t.id AND a.actif = 1
                GROUP BY t.id
                ORDER BY t.actif DESC, t.nom
            ''').fetchall()

        active_count = sum(1 for c in categories if c['actif'] == 1)

        return render_template('stock/categories.html', page='stock',
                               categories=categories,
                               active_count=active_count,
                               total_count=len(categories))
    finally:
        db.close()


# ============================================================
#  API JSON — Articles
# ============================================================

@bp.route('/api/articles', methods=['GET'])
def api_stock_articles():
    """Liste des articles en JSON."""
    db = get_db()
    try:
        articles = db.execute('''
            SELECT a.*, c.nom AS cat_nom, f.nom AS fourn_nom, m.nom AS materiau_nom
            FROM stock_articles a
            LEFT JOIN types_activite c ON a.categorie_id = c.id
            LEFT JOIN stock_fournisseurs f ON a.fournisseur_id = f.id
            LEFT JOIN materiaux m ON a.materiau_id = m.id
            WHERE a.actif = 1
            ORDER BY a.nom
        ''').fetchall()
        return jsonify(_rows_to_list(articles))
    finally:
        db.close()


@bp.route('/api/articles/<int:article_id>', methods=['GET'])
def api_stock_article(article_id):
    """Détail d'un article en JSON."""
    db = get_db()
    try:
        a = db.execute('''
            SELECT a.*, c.nom AS cat_nom, f.nom AS fourn_nom, m.nom AS materiau_nom
            FROM stock_articles a
            LEFT JOIN types_activite c ON a.categorie_id = c.id
            LEFT JOIN stock_fournisseurs f ON a.fournisseur_id = f.id
            LEFT JOIN materiaux m ON a.materiau_id = m.id
            WHERE a.id = ?
        ''', (article_id,)).fetchone()
        if not a:
            return jsonify({'error': 'Article introuvable'}), 404
        return jsonify(dict(a))
    finally:
        db.close()


@bp.route('/api/articles', methods=['POST'])
def api_stock_add_article():
    """Crée un article (JSON ou formulaire)."""
    db = get_db()
    try:
        data = request.get_json() if request.is_json else request.form
        nom = (data.get('nom') or '').strip()
        if not nom:
            return jsonify({'success': False, 'error': 'Nom requis'}), 400

        stock_vals = _prepare_article_stock_values(data)

        cur = db.execute('''
            INSERT INTO stock_articles
            (nom, materiau_id, categorie_id, fournisseur_id, unite,
             longueur_cm, largeur_cm, quantite_actuelle,
             quantite_minimum, quantite_maximum, prix_unitaire,
             emplacement, description)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            nom,
            data.get('materiau_id') or None,
            data.get('categorie_id') or None,
            data.get('fournisseur_id') or None,
            stock_vals['unite'],
            stock_vals['longueur_cm'],
            stock_vals['largeur_cm'],
            stock_vals['quantite_actuelle'],
            stock_vals['quantite_minimum'],
            stock_vals['quantite_maximum'],
            _to_float(data.get('prix_unitaire')),
            (data.get('emplacement') or '').strip(),
            (data.get('description') or '').strip(),
        ))
        article_id = cur.lastrowid

        # Mouvement initial si quantité > 0
        qte_init = stock_vals['quantite_actuelle']
        if qte_init > 0:
            db.execute('''
                INSERT INTO stock_mouvements
                (article_id, type, quantite, quantite_avant, quantite_apres, source, notes)
                VALUES (?, 'entree', ?, 0, ?, 'manuel', 'Stock initial')
            ''', (article_id, qte_init, qte_init))

        db.commit()
        if request.is_json:
            return jsonify({'success': True, 'id': article_id})
        flash('Article ajouté avec succès', 'success')
        return redirect(url_for('stock.stock_articles'))
    finally:
        db.close()


@bp.route('/api/articles/<int:article_id>', methods=['PUT', 'POST'])
def api_stock_update_article(article_id):
    """Met à jour un article."""
    db = get_db()
    try:
        existing = db.execute('SELECT id FROM stock_articles WHERE id=?', (article_id,)).fetchone()
        if not existing:
            return jsonify({'success': False, 'error': 'Article introuvable'}), 404

        data = request.get_json() if request.is_json else request.form
        stock_vals = _prepare_article_stock_values(data)
        db.execute('''
            UPDATE stock_articles SET
                nom=?, materiau_id=?, categorie_id=?, fournisseur_id=?,
                unite=?, longueur_cm=?, largeur_cm=?,
                quantite_minimum=?, quantite_maximum=?, prix_unitaire=?,
                emplacement=?, description=?, date_modification=?
            WHERE id=?
        ''', (
            (data.get('nom') or '').strip(),
            data.get('materiau_id') or None,
            data.get('categorie_id') or None,
            data.get('fournisseur_id') or None,
            stock_vals['unite'],
            stock_vals['longueur_cm'],
            stock_vals['largeur_cm'],
            stock_vals['quantite_minimum'],
            stock_vals['quantite_maximum'],
            _to_float(data.get('prix_unitaire')),
            (data.get('emplacement') or '').strip(),
            (data.get('description') or '').strip(),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            article_id,
        ))
        db.commit()
        if request.is_json:
            return jsonify({'success': True})
        flash('Article mis à jour', 'success')
        return redirect(url_for('stock.stock_articles'))
    finally:
        db.close()


@bp.route('/api/articles/<int:article_id>/archiver', methods=['POST'])
def api_stock_archive_article(article_id):
    """Archive (soft delete) un article."""
    db = get_db()
    try:
        db.execute('UPDATE stock_articles SET actif=0 WHERE id=?', (article_id,))
        db.commit()
        if request.is_json:
            return jsonify({'success': True})
        flash('Article archivé', 'success')
        return redirect(url_for('stock.stock_articles'))
    finally:
        db.close()


# ============================================================
#  API JSON — Mouvements
# ============================================================

@bp.route('/api/mouvements', methods=['POST'])
def api_stock_add_mouvement():
    """Crée un mouvement de stock (entrée ou sortie)."""
    db = get_db()
    try:
        data = request.get_json() if request.is_json else request.form
        article_id = int(data['article_id'])
        type_mvt = data['type']
        quantite = float(data['quantite'])

        if type_mvt not in ('entree', 'sortie'):
            return jsonify({'success': False, 'error': 'Type invalide'}), 400
        if quantite <= 0:
            return jsonify({'success': False, 'error': 'Quantité invalide'}), 400

        article = db.execute(
            'SELECT quantite_actuelle FROM stock_articles WHERE id=? AND actif=1',
            (article_id,)
        ).fetchone()
        if not article:
            return jsonify({'success': False, 'error': 'Article introuvable'}), 404

        avant = article['quantite_actuelle']
        if type_mvt == 'sortie':
            apres = avant - quantite
        else:
            apres = avant + quantite

        db.execute('''
            INSERT INTO stock_mouvements
            (article_id, type, quantite, quantite_avant, quantite_apres, source, utilisateur, notes)
            VALUES (?,?,?,?,?,?,?,?)
        ''', (
            article_id, type_mvt, quantite, avant, apres,
            data.get('source', 'manuel'),
            (data.get('utilisateur') or '').strip(),
            (data.get('notes') or '').strip(),
        ))
        db.execute(
            'UPDATE stock_articles SET quantite_actuelle=?, date_modification=? WHERE id=?',
            (apres, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), article_id)
        )
        db.commit()

        if request.is_json:
            return jsonify({'success': True, 'quantite_avant': avant, 'quantite_apres': apres})
        flash(f"{'Entrée' if type_mvt == 'entree' else 'Sortie'} enregistrée", 'success')
        return redirect(url_for('stock.stock_mouvements'))
    finally:
        db.close()


@bp.route('/api/mouvements/rapide', methods=['POST'])
def api_stock_mouvement_rapide():
    """Mouvement rapide en JSON (depuis modal stock)."""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Données manquantes'}), 400
    # Délègue au handler principal
    return api_stock_add_mouvement()


# ============================================================
#  API JSON — Inventaire physique
# ============================================================

@bp.route('/api/inventaire/valider', methods=['POST'])
def api_stock_validate_inventaire():
    """Valide un inventaire physique — crée des ajustements pour les écarts."""
    db = get_db()
    try:
        data = request.get_json() if request.is_json else request.form
        articles = db.execute(
            'SELECT id, nom, quantite_actuelle FROM stock_articles WHERE actif=1'
        ).fetchall()

        ajustements = 0
        for a in articles:
            key = f"compte_{a['id']}"
            val = data.get(key)
            if val is None or val == '':
                continue
            compte = float(val)
            ecart = compte - a['quantite_actuelle']
            if abs(ecart) < 0.001:
                continue

            db.execute('''
                INSERT INTO stock_mouvements
                (article_id, type, quantite, quantite_avant, quantite_apres, source, notes)
                VALUES (?, 'ajustement', ?, ?, ?, 'inventaire', ?)
            ''', (
                a['id'], abs(ecart), a['quantite_actuelle'], compte,
                f"Inventaire physique — écart : {'+'if ecart>0 else ''}{ecart:.2f}"
            ))
            db.execute(
                'UPDATE stock_articles SET quantite_actuelle=?, date_modification=? WHERE id=?',
                (compte, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), a['id'])
            )
            ajustements += 1

        db.commit()
        if request.is_json:
            return jsonify({'success': True, 'ajustements': ajustements})
        flash(f'Inventaire validé — {ajustements} ajustement(s)', 'success')
        return redirect(url_for('stock.stock_index'))
    finally:
        db.close()


# ============================================================
#  API JSON — Catégories
# ============================================================

@bp.route('/api/categories', methods=['GET'])
def api_stock_categories():
    """Liste des catégories stock (= types d'activité)."""
    db = get_db()
    try:
        _ensure_stock_categories_seed(db)
        rows = db.execute('SELECT * FROM types_activite WHERE actif = 1 ORDER BY nom').fetchall()
        if not rows:
            db.close()
            init_db()
            db = get_db()
            rows = db.execute('SELECT * FROM types_activite WHERE actif = 1 ORDER BY nom').fetchall()
        # Si aucune catégorie active, renvoyer aussi les inactives pour éviter une UI vide.
        if not rows:
            rows = db.execute('SELECT * FROM types_activite ORDER BY actif DESC, nom').fetchall()
        return jsonify(_rows_to_list(rows))
    finally:
        db.close()


# ============================================================
#  API JSON — Fournisseurs
# ============================================================

@bp.route('/api/fournisseurs', methods=['GET'])
def api_stock_fournisseurs():
    db = get_db()
    try:
        rows = db.execute('SELECT * FROM stock_fournisseurs WHERE actif=1 ORDER BY nom').fetchall()
        data = _rows_to_list(rows)

        links = db.execute('''
            SELECT fournisseur_id, materiau_id
            FROM stock_fournisseur_materiaux
        ''').fetchall()
        by_f = {}
        for l in links:
            by_f.setdefault(int(l['fournisseur_id']), []).append(int(l['materiau_id']))

        for item in data:
            item['materiau_ids'] = by_f.get(int(item['id']), [])

        return jsonify(data)
    finally:
        db.close()


@bp.route('/api/fournisseurs/<int:fourn_id>', methods=['GET'])
def api_stock_fournisseur(fourn_id):
    db = get_db()
    try:
        f = db.execute('SELECT * FROM stock_fournisseurs WHERE id=?', (fourn_id,)).fetchone()
        if not f:
            return jsonify({'error': 'Fournisseur introuvable'}), 404
        out = dict(f)
        mids = db.execute(
            'SELECT materiau_id FROM stock_fournisseur_materiaux WHERE fournisseur_id=? ORDER BY materiau_id',
            (fourn_id,)
        ).fetchall()
        out['materiau_ids'] = [int(r['materiau_id']) for r in mids]
        return jsonify(out)
    finally:
        db.close()


@bp.route('/api/fournisseurs', methods=['POST'])
def api_stock_add_fournisseur():
    db = get_db()
    try:
        is_json = request.is_json
        data = request.get_json() if is_json else request.form
        nom = (data.get('nom') or '').strip()
        if not nom:
            return jsonify({'success': False, 'error': 'Nom requis'}), 400

        materiau_ids = _parse_materiau_ids(data, is_json)

        cur = db.execute('''
            INSERT INTO stock_fournisseurs
            (nom, contact, email, telephone, telephone2, adresse_postale, image_path, notes)
            VALUES (?,?,?,?,?,?,?,?)
        ''', (
            nom,
            (data.get('contact') or '').strip(),
            (data.get('email') or '').strip(),
            (data.get('telephone') or '').strip(),
            (data.get('telephone2') or '').strip(),
            (data.get('adresse_postale') or '').strip(),
            (data.get('image_path') or '').strip(),
            (data.get('notes') or '').strip(),
        ))

        fournisseur_id = int(cur.lastrowid)
        _sync_fournisseur_materiaux(db, fournisseur_id, materiau_ids)
        db.commit()
        if is_json:
            return jsonify({'success': True, 'id': fournisseur_id})
        flash('Fournisseur ajouté', 'success')
        return redirect(url_for('stock.stock_fournisseurs'))
    finally:
        db.close()


@bp.route('/api/fournisseurs/<int:fourn_id>', methods=['PUT', 'POST'])
def api_stock_update_fournisseur(fourn_id):
    db = get_db()
    try:
        is_json = request.is_json
        data = request.get_json() if is_json else request.form
        materiau_ids = _parse_materiau_ids(data, is_json)

        db.execute('''
            UPDATE stock_fournisseurs SET
                nom=?, contact=?, email=?, telephone=?, telephone2=?,
                adresse_postale=?, image_path=?, notes=?
            WHERE id=?
        ''', (
            (data.get('nom') or '').strip(),
            (data.get('contact') or '').strip(),
            (data.get('email') or '').strip(),
            (data.get('telephone') or '').strip(),
            (data.get('telephone2') or '').strip(),
            (data.get('adresse_postale') or '').strip(),
            (data.get('image_path') or '').strip(),
            (data.get('notes') or '').strip(),
            fourn_id,
        ))

        _sync_fournisseur_materiaux(db, fourn_id, materiau_ids)
        db.commit()
        if is_json:
            return jsonify({'success': True})
        flash('Fournisseur mis à jour', 'success')
        return redirect(url_for('stock.stock_fournisseurs'))
    finally:
        db.close()


@bp.route('/api/fournisseurs/<int:fourn_id>/archiver', methods=['POST'])
def api_stock_archive_fournisseur(fourn_id):
    db = get_db()
    try:
        linked = db.execute(
            'SELECT COUNT(*) AS cnt FROM stock_articles WHERE fournisseur_id=? AND actif=1',
            (fourn_id,)
        ).fetchone()['cnt']
        if linked > 0:
            if request.is_json:
                return jsonify({'success': False, 'error': f'{linked} article(s) liés'}), 400
            flash(f'Impossible : {linked} article(s) liés à ce fournisseur', 'danger')
            return redirect(url_for('stock.stock_fournisseurs'))
        db.execute('UPDATE stock_fournisseurs SET actif=0 WHERE id=?', (fourn_id,))
        db.commit()
        if request.is_json:
            return jsonify({'success': True})
        flash('Fournisseur archivé', 'success')
        return redirect(url_for('stock.stock_fournisseurs'))
    finally:
        db.close()


# ============================================================
#  API JSON — Unités
# ============================================================

@bp.route('/api/unites', methods=['GET'])
def api_stock_unites():
    db = get_db()
    try:
        rows = db.execute('SELECT * FROM stock_unites ORDER BY ordre, nom').fetchall()
        return jsonify(_rows_to_list(rows))
    finally:
        db.close()


# ============================================================
#  UTILITAIRES
# ============================================================

def _to_float(val):
    """Convertit une valeur en float ou retourne None."""
    if val is None or val == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
