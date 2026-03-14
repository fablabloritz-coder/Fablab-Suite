"""
FabTrack — Routes gestion stock (fusion FabStock)
Importé dans app.py via register_stock_routes(app).
"""

from flask import render_template, request, jsonify, send_file
from models import get_db
from stock_utils import stock_status, format_stock_display
from werkzeug.utils import secure_filename
from datetime import datetime
import os, json


def register_stock_routes(app):
    """Enregistre toutes les routes stock sur l'app Flask."""

    # Dossier uploads fournisseurs
    DOCS_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'documents_fournisseurs')
    os.makedirs(DOCS_FOLDER, exist_ok=True)

    def rows_to_list(rows):
        return [dict(r) for r in rows]

    # ============================================================
    # PAGES
    # ============================================================

    @app.route('/stock')
    def page_stock():
        return render_template('stock.html', page='stock')

    @app.route('/fournisseurs')
    def page_fournisseurs():
        return render_template('fournisseurs.html', page='fournisseurs')

    @app.route('/inventaire')
    def page_inventaire():
        return render_template('inventaire.html', page='inventaire')

    @app.route('/mouvements-stock')
    def page_mouvements_stock():
        return render_template('mouvements_stock.html', page='mouvements_stock')

    # ============================================================
    # API — STOCK DASHBOARD
    # ============================================================

    @app.route('/api/stock/dashboard')
    def api_stock_dashboard():
        """Retourne les matériaux suivis groupés par catégorie avec statut stock."""
        db = get_db()
        try:
            rows = db.execute('''
                SELECT m.*, c.nom as categorie_nom, c.couleur as categorie_couleur,
                       c.icone as categorie_icone, c.ordre as categorie_ordre,
                       f.nom as fournisseur_nom
                FROM materiaux m
                LEFT JOIN categories_materiau c ON m.categorie_materiau_id = c.id
                LEFT JOIN fournisseurs f ON m.fournisseur_id = f.id
                WHERE m.actif = 1 AND m.quantite_actuelle IS NOT NULL
                ORDER BY c.ordre, c.nom, m.nom
            ''').fetchall()

            categories = {}
            alertes = 0
            for r in rows:
                mat = dict(r)
                mat['stock_display'] = format_stock_display(mat)
                mat['stock_status'] = stock_status(mat)
                if mat['stock_status'] in ('alerte', 'critique'):
                    alertes += 1

                cat_nom = mat.get('categorie_nom') or 'Non classé'
                if cat_nom not in categories:
                    categories[cat_nom] = {
                        'nom': cat_nom,
                        'couleur': mat.get('categorie_couleur') or '#94a3b8',
                        'icone': mat.get('categorie_icone') or 'bi-box',
                        'materiaux': []
                    }
                categories[cat_nom]['materiaux'].append(mat)

            return jsonify({
                'success': True,
                'categories': list(categories.values()),
                'alertes': alertes,
                'total': len(rows)
            })
        finally:
            db.close()

    @app.route('/api/stock/alertes-count')
    def api_stock_alertes_count():
        """Retourne le nombre de matériaux en alerte/critique (pour badge navbar)."""
        db = get_db()
        try:
            rows = db.execute('''
                SELECT quantite_actuelle, quantite_minimum
                FROM materiaux
                WHERE actif = 1 AND quantite_actuelle IS NOT NULL AND quantite_minimum IS NOT NULL
            ''').fetchall()
            count = sum(1 for r in rows if r['quantite_actuelle'] <= r['quantite_minimum'])
            return jsonify({'success': True, 'count': count})
        finally:
            db.close()

    # ============================================================
    # API — MOUVEMENT RAPIDE
    # ============================================================

    @app.route('/api/stock/mouvement-rapide', methods=['POST'])
    def api_mouvement_rapide():
        """Mouvement stock rapide (+/-) depuis modal."""
        data = request.get_json()
        materiau_id = data.get('materiau_id')
        type_mvt = data.get('type', 'entree')  # entree ou sortie
        quantite = float(data.get('quantite', 0))
        notes = data.get('notes', '')
        utilisateur = data.get('utilisateur', '')

        if not materiau_id or quantite <= 0:
            return jsonify({'success': False, 'error': 'Données invalides'}), 400
        if type_mvt not in ('entree', 'sortie'):
            return jsonify({'success': False, 'error': 'Type invalide'}), 400

        db = get_db()
        try:
            row = db.execute('SELECT quantite_actuelle FROM materiaux WHERE id=?', (materiau_id,)).fetchone()
            if not row or row['quantite_actuelle'] is None:
                return jsonify({'success': False, 'error': 'Matériau sans suivi stock'}), 400

            avant = row['quantite_actuelle']
            if type_mvt == 'entree':
                apres = avant + quantite
            else:
                apres = max(0, avant - quantite)

            db.execute('UPDATE materiaux SET quantite_actuelle=? WHERE id=?', (apres, materiau_id))
            db.execute('''INSERT INTO mouvements_stock
                (materiau_id, type, quantite, quantite_avant, quantite_apres, utilisateur, notes, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manuel')''',
                (materiau_id, type_mvt, quantite, avant, apres, utilisateur, notes))
            db.commit()
            return jsonify({'success': True, 'quantite_avant': avant, 'quantite_apres': apres})
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    # ============================================================
    # API — CATÉGORIES MATÉRIAU
    # ============================================================

    @app.route('/api/stock/categories', methods=['GET'])
    def api_stock_categories_get():
        db = get_db()
        try:
            rows = db.execute('SELECT * FROM categories_materiau ORDER BY ordre, nom').fetchall()
            return jsonify({'success': True, 'data': rows_to_list(rows)})
        finally:
            db.close()

    @app.route('/api/stock/categories', methods=['POST'])
    def api_stock_categories_create():
        data = request.get_json()
        nom = (data.get('nom') or '').strip()
        if not nom:
            return jsonify({'success': False, 'error': 'Nom requis'}), 400
        db = get_db()
        try:
            cur = db.execute('INSERT INTO categories_materiau (nom, couleur, icone, ordre) VALUES (?,?,?,?)',
                (nom, data.get('couleur', '#e67e22'), data.get('icone', 'bi-box'), data.get('ordre', 0)))
            db.commit()
            return jsonify({'success': True, 'id': cur.lastrowid}), 201
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    @app.route('/api/stock/categories/<int:id>', methods=['PUT'])
    def api_stock_categories_update(id):
        data = request.get_json()
        db = get_db()
        try:
            db.execute('UPDATE categories_materiau SET nom=?, couleur=?, icone=?, ordre=? WHERE id=?',
                (data.get('nom'), data.get('couleur'), data.get('icone'), data.get('ordre', 0), id))
            db.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    @app.route('/api/stock/categories/<int:id>', methods=['DELETE'])
    def api_stock_categories_delete(id):
        db = get_db()
        try:
            # Retirer la catégorie des matériaux associés
            db.execute('UPDATE materiaux SET categorie_materiau_id=NULL WHERE categorie_materiau_id=?', (id,))
            db.execute('DELETE FROM categories_materiau WHERE id=?', (id,))
            db.commit()
            return jsonify({'success': True})
        finally:
            db.close()

    # ============================================================
    # API — UNITÉS
    # ============================================================

    @app.route('/api/stock/unites')
    def api_stock_unites():
        db = get_db()
        try:
            rows = db.execute('SELECT * FROM unites ORDER BY famille, ordre, nom').fetchall()
            # Grouper par famille
            familles = {}
            for r in rows:
                d = dict(r)
                fam = d['famille']
                if fam not in familles:
                    familles[fam] = []
                familles[fam].append(d)
            return jsonify({'success': True, 'data': rows_to_list(rows), 'familles': familles})
        finally:
            db.close()

    # ============================================================
    # API — FOURNISSEURS
    # ============================================================

    @app.route('/api/fournisseurs/<int:id>')
    def api_fournisseur_get(id):
        db = get_db()
        try:
            row = db.execute('SELECT * FROM fournisseurs WHERE id=?', (id,)).fetchone()
            if not row:
                return jsonify({'success': False, 'error': 'Non trouvé'}), 404
            f = dict(row)
            # Charger les documents
            docs = db.execute('SELECT * FROM documents_fournisseur WHERE fournisseur_id=? ORDER BY date_upload DESC',
                (id,)).fetchall()
            f['documents'] = rows_to_list(docs)
            return jsonify({'success': True, 'data': f})
        finally:
            db.close()

    @app.route('/api/fournisseurs')
    def api_fournisseurs_list():
        db = get_db()
        try:
            rows = db.execute('SELECT * FROM fournisseurs WHERE actif=1 ORDER BY nom').fetchall()
            return jsonify({'success': True, 'data': rows_to_list(rows)})
        finally:
            db.close()

    @app.route('/fournisseurs/ajouter', methods=['POST'])
    def fournisseur_ajouter():
        data = request.get_json()
        nom = (data.get('nom') or '').strip()
        if not nom:
            return jsonify({'success': False, 'error': 'Nom requis'}), 400
        db = get_db()
        try:
            cur = db.execute('''INSERT INTO fournisseurs
                (nom, contact, email, telephone, telephone2, url_google, specialites, notes)
                VALUES (?,?,?,?,?,?,?,?)''',
                (nom, data.get('contact',''), data.get('email',''),
                 data.get('telephone',''), data.get('telephone2',''),
                 data.get('url_google',''), data.get('specialites',''), data.get('notes','')))
            db.commit()
            return jsonify({'success': True, 'id': cur.lastrowid}), 201
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    @app.route('/fournisseurs/<int:id>/modifier', methods=['POST'])
    def fournisseur_modifier(id):
        data = request.get_json()
        db = get_db()
        try:
            db.execute('''UPDATE fournisseurs SET
                nom=?, contact=?, email=?, telephone=?, telephone2=?,
                url_google=?, specialites=?, notes=?
                WHERE id=?''',
                (data.get('nom',''), data.get('contact',''), data.get('email',''),
                 data.get('telephone',''), data.get('telephone2',''),
                 data.get('url_google',''), data.get('specialites',''), data.get('notes',''), id))
            db.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    @app.route('/fournisseurs/<int:id>/supprimer', methods=['POST'])
    def fournisseur_supprimer(id):
        db = get_db()
        try:
            db.execute('UPDATE fournisseurs SET actif=0 WHERE id=?', (id,))
            db.commit()
            return jsonify({'success': True})
        finally:
            db.close()

    # ============================================================
    # API — DOCUMENTS FOURNISSEUR (coffre)
    # ============================================================

    @app.route('/fournisseurs/<int:id>/documents/upload', methods=['POST'])
    def fournisseur_doc_upload(id):
        if 'fichier' not in request.files:
            return jsonify({'success': False, 'error': 'Aucun fichier'}), 400
        f = request.files['fichier']
        if not f.filename:
            return jsonify({'success': False, 'error': 'Fichier vide'}), 400

        nom_doc = request.form.get('nom', f.filename)
        type_doc = request.form.get('type_document', '')
        filename = secure_filename(f.filename)
        # Préfixer avec l'id fournisseur pour éviter les collisions
        save_name = f"{id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        save_path = os.path.join(DOCS_FOLDER, save_name)
        f.save(save_path)

        db = get_db()
        try:
            cur = db.execute('''INSERT INTO documents_fournisseur
                (fournisseur_id, nom, fichier_path, type_document)
                VALUES (?,?,?,?)''', (id, nom_doc, save_name, type_doc))
            db.commit()
            return jsonify({'success': True, 'id': cur.lastrowid})
        except Exception as e:
            db.rollback()
            # Nettoyer le fichier en cas d'erreur DB
            if os.path.exists(save_path):
                os.remove(save_path)
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    @app.route('/fournisseurs/<int:fid>/documents/<int:doc_id>/telecharger')
    def fournisseur_doc_telecharger(fid, doc_id):
        db = get_db()
        try:
            row = db.execute('SELECT * FROM documents_fournisseur WHERE id=? AND fournisseur_id=?',
                (doc_id, fid)).fetchone()
            if not row:
                return jsonify({'success': False, 'error': 'Document non trouvé'}), 404
            file_path = os.path.join(DOCS_FOLDER, row['fichier_path'])
            if not os.path.exists(file_path):
                return jsonify({'success': False, 'error': 'Fichier manquant'}), 404
            return send_file(file_path, as_attachment=True, download_name=row['nom'])
        finally:
            db.close()

    @app.route('/fournisseurs/<int:fid>/documents/<int:doc_id>/supprimer', methods=['POST'])
    def fournisseur_doc_supprimer(fid, doc_id):
        db = get_db()
        try:
            row = db.execute('SELECT fichier_path FROM documents_fournisseur WHERE id=? AND fournisseur_id=?',
                (doc_id, fid)).fetchone()
            if row:
                file_path = os.path.join(DOCS_FOLDER, row['fichier_path'])
                if os.path.exists(file_path):
                    os.remove(file_path)
                db.execute('DELETE FROM documents_fournisseur WHERE id=?', (doc_id,))
                db.commit()
            return jsonify({'success': True})
        finally:
            db.close()

    # ============================================================
    # API — INVENTAIRE
    # ============================================================

    @app.route('/api/inventaire')
    def api_inventaire():
        """Retourne les matériaux suivis pour l'inventaire physique."""
        db = get_db()
        try:
            rows = db.execute('''
                SELECT m.id, m.nom, m.unite, m.quantite_actuelle, m.quantite_minimum,
                       c.nom as categorie_nom
                FROM materiaux m
                LEFT JOIN categories_materiau c ON m.categorie_materiau_id = c.id
                WHERE m.actif = 1 AND m.quantite_actuelle IS NOT NULL
                ORDER BY c.ordre, c.nom, m.nom
            ''').fetchall()
            return jsonify({'success': True, 'data': rows_to_list(rows)})
        finally:
            db.close()

    @app.route('/inventaire/valider', methods=['POST'])
    def inventaire_valider():
        """Valide un inventaire physique : ajustements groupés."""
        data = request.get_json()
        ajustements = data.get('ajustements', [])
        utilisateur = data.get('utilisateur', '')

        if not ajustements:
            return jsonify({'success': False, 'error': 'Aucun ajustement'}), 400

        db = get_db()
        count = 0
        try:
            for aj in ajustements:
                mat_id = aj.get('materiau_id')
                comptage = aj.get('comptage')
                if mat_id is None or comptage is None:
                    continue

                comptage = float(comptage)
                row = db.execute('SELECT quantite_actuelle FROM materiaux WHERE id=?', (mat_id,)).fetchone()
                if not row or row['quantite_actuelle'] is None:
                    continue

                avant = row['quantite_actuelle']
                if avant == comptage:
                    continue  # Pas d'écart

                db.execute('UPDATE materiaux SET quantite_actuelle=? WHERE id=?', (comptage, mat_id))
                db.execute('''INSERT INTO mouvements_stock
                    (materiau_id, type, quantite, quantite_avant, quantite_apres, utilisateur, notes, source)
                    VALUES (?, 'ajustement', ?, ?, ?, ?, 'Inventaire physique', 'inventaire')''',
                    (mat_id, abs(comptage - avant), avant, comptage, utilisateur))
                count += 1

            db.commit()
            return jsonify({'success': True, 'ajustements': count})
        except Exception as e:
            db.rollback()
            return jsonify({'success': False, 'error': str(e)}), 400
        finally:
            db.close()

    # ============================================================
    # API — MOUVEMENTS STOCK (historique)
    # ============================================================

    @app.route('/api/mouvements-stock')
    def api_mouvements_stock():
        db = get_db()
        try:
            page = max(1, int(request.args.get('page', 1)))
            per_page = min(max(1, int(request.args.get('per_page', 50))), 200)
            materiau_id = request.args.get('materiau_id', '')
            type_mvt = request.args.get('type', '')
            source = request.args.get('source', '')
            date_debut = request.args.get('date_debut', '')
            date_fin = request.args.get('date_fin', '')

            query = '''
                SELECT ms.*, m.nom as materiau_nom, m.unite
                FROM mouvements_stock ms
                LEFT JOIN materiaux m ON ms.materiau_id = m.id
                WHERE 1=1
            '''
            count_q = 'SELECT COUNT(*) as total FROM mouvements_stock ms WHERE 1=1'
            params = []
            cp = []

            if materiau_id:
                query += ' AND ms.materiau_id = ?'; params.append(int(materiau_id))
                count_q += ' AND ms.materiau_id = ?'; cp.append(int(materiau_id))
            if type_mvt:
                query += ' AND ms.type = ?'; params.append(type_mvt)
                count_q += ' AND ms.type = ?'; cp.append(type_mvt)
            if source:
                query += ' AND ms.source = ?'; params.append(source)
                count_q += ' AND ms.source = ?'; cp.append(source)
            if date_debut:
                query += ' AND ms.date >= ?'; params.append(date_debut)
                count_q += ' AND ms.date >= ?'; cp.append(date_debut)
            if date_fin:
                query += ' AND ms.date <= ?'; params.append(date_fin + ' 23:59:59')
                count_q += ' AND ms.date <= ?'; cp.append(date_fin + ' 23:59:59')

            total = db.execute(count_q, cp).fetchone()['total']
            query += ' ORDER BY ms.date DESC, ms.id DESC LIMIT ? OFFSET ?'
            params.extend([per_page, (page - 1) * per_page])

            rows = db.execute(query, params).fetchall()
            return jsonify({
                'success': True,
                'data': rows_to_list(rows),
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': max(1, (total + per_page - 1) // per_page)
            })
        finally:
            db.close()
