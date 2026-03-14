"""PretGo — Blueprint : personnes"""
from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from utils import get_app_db, admin_required, get_categories_personnes, get_champs_personnalises, get_valeurs_champs, sauver_valeurs_champs
import csv
import io
import unicodedata

bp = Blueprint('personnes', __name__)

@bp.route('/personnes')
@admin_required
def personnes():
    conn = get_app_db()
    filtre = request.args.get('categorie', 'tous')
    recherche = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    par_page = 50

    query = 'SELECT * FROM personnes WHERE actif = 1'
    count_query = 'SELECT COUNT(*) FROM personnes WHERE actif = 1'
    params = []
    count_params = []

    if filtre != 'tous':
        if filtre == '_autres':
            cats_connues = get_categories_personnes()
            cles = [c['cle'] for c in cats_connues]
            if cles:
                placeholders = ','.join('?' * len(cles))
                clause = f' AND categorie NOT IN ({placeholders})'
                query += clause
                count_query += clause
                params.extend(cles)
                count_params.extend(cles)
        else:
            query += ' AND categorie = ?'
            count_query += ' AND categorie = ?'
            params.append(filtre)
            count_params.append(filtre)

    if recherche:
        like_clause = ' AND (nom LIKE ? OR prenom LIKE ? OR classe LIKE ?)'
        query += like_clause
        count_query += like_clause
        params.extend([f'%{recherche}%', f'%{recherche}%', f'%{recherche}%'])
        count_params.extend([f'%{recherche}%', f'%{recherche}%', f'%{recherche}%'])

    total = conn.execute(count_query, count_params).fetchone()[0]
    total_pages = max(1, (total + par_page - 1) // par_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * par_page

    query += ' ORDER BY categorie, nom, prenom LIMIT ? OFFSET ?'
    params.extend([par_page, offset])
    personnes_list = conn.execute(query, params).fetchall()

    # Comptages par catégorie (dynamique depuis la base)
    comptages = {}
    cats = get_categories_personnes()
    for cat in cats:
        comptages[cat['cle']] = conn.execute(
            'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie = ?', (cat['cle'],)
        ).fetchone()[0]
    cles_connues = [c['cle'] for c in cats]
    if cles_connues:
        placeholders = ','.join('?' * len(cles_connues))
        autres = conn.execute(
            f'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie NOT IN ({placeholders})',
            cles_connues
        ).fetchone()[0]
    else:
        autres = conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0]
    if autres > 0:
        comptages['_autres'] = autres
    comptages['total'] = conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0]

    return render_template(
        'personnes.html',
        personnes=personnes_list,
        filtre=filtre,
        recherche=recherche,
        comptages=comptages,
        cats_list=cats,
        page=page,
        total_pages=total_pages,
        total=total
    )



@bp.route('/personnes/ajouter', methods=['GET', 'POST'])
@admin_required
def ajouter_personne():
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip().upper()
        prenom = request.form.get('prenom', '').strip().title()
        categorie = request.form.get('categorie', '')
        classe = request.form.get('classe', '').strip()
        email = request.form.get('email', '').strip().lower()

        if not nom or not prenom or not categorie:
            flash('Veuillez remplir tous les champs obligatoires.', 'danger')
        else:
            conn = get_app_db()
            conn.execute(
                'INSERT INTO personnes (nom, prenom, categorie, classe, email) VALUES (?, ?, ?, ?, ?)',
                (nom, prenom, categorie, classe, email)
            )
            conn.commit()
            personne_id_new = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            # Sauvegarder les champs personnalisés
            sauver_valeurs_champs(personne_id_new, 'personne', request.form)
            flash(f'{prenom} {nom} a été ajouté(e) avec succès !', 'success')
            return redirect(url_for('personnes.personnes'))

    cats = get_categories_personnes()
    champs_custom = get_champs_personnalises('personne')
    return render_template('ajouter_personne.html', cats_personnes=cats, champs_custom=champs_custom)



@bp.route('/personnes/modifier/<int:personne_id>', methods=['GET', 'POST'])
@admin_required
def modifier_personne(personne_id):
    conn = get_app_db()

    if request.method == 'POST':
        nom = request.form.get('nom', '').strip().upper()
        prenom = request.form.get('prenom', '').strip().title()
        categorie = request.form.get('categorie', '')
        classe = request.form.get('classe', '').strip()
        email = request.form.get('email', '').strip().lower()

        if not nom or not prenom or not categorie:
            flash('Veuillez remplir tous les champs obligatoires.', 'danger')
        else:
            conn.execute(
                'UPDATE personnes SET nom=?, prenom=?, categorie=?, classe=?, email=? WHERE id=?',
                (nom, prenom, categorie, classe, email, personne_id)
            )
            conn.commit()
            # Sauvegarder les champs personnalisés
            sauver_valeurs_champs(personne_id, 'personne', request.form)
            flash('Personne modifiée avec succès !', 'success')
            return redirect(url_for('personnes.personnes'))

    personne = conn.execute('SELECT * FROM personnes WHERE id = ?', (personne_id,)).fetchone()

    if not personne:
        flash('Personne non trouvée.', 'danger')
        return redirect(url_for('personnes.personnes'))

    champs_custom = get_champs_personnalises('personne')
    valeurs_custom = get_valeurs_champs(personne_id, 'personne')
    return render_template('modifier_personne.html', personne=personne,
                           cats_personnes=get_categories_personnes(),
                           champs_custom=champs_custom, valeurs_custom=valeurs_custom)



@bp.route('/personnes/supprimer/<int:personne_id>', methods=['POST'])
@admin_required
def supprimer_personne(personne_id):
    conn = get_app_db()

    # Vérifier si la personne a des prêts en cours (non retournés)
    prets_actifs = conn.execute(
        'SELECT COUNT(*) FROM prets WHERE personne_id = ? AND retour_confirme = 0',
        (personne_id,)
    ).fetchone()[0]

    if prets_actifs > 0:
        personne = conn.execute(
            'SELECT nom, prenom FROM personnes WHERE id = ?', (personne_id,)
        ).fetchone()
        flash(
            f'Impossible de supprimer {personne["prenom"]} {personne["nom"]} : '
            f'{prets_actifs} prêt(s) en cours. Effectuez d\'abord le retour du matériel.',
            'danger'
        )
        return redirect(url_for('personnes.personnes'))

    conn.execute('UPDATE personnes SET actif = 0 WHERE id = ?', (personne_id,))
    conn.commit()
    flash('Personne supprimée avec succès.', 'success')
    return redirect(url_for('personnes.personnes'))



@bp.route('/personnes/importer', methods=['GET', 'POST'])
@admin_required
def importer_personnes():
    champs_custom = [dict(ch) for ch in get_champs_personnalises('personne')]
    custom_by_name = {ch['nom_champ']: ch for ch in champs_custom}

    if request.method == 'POST':
        if 'fichier_csv' not in request.files:
            flash('Aucun fichier sélectionné.', 'danger')
            return redirect(request.url)

        fichier = request.files['fichier_csv']
        if fichier.filename == '':
            flash('Aucun fichier sélectionné.', 'danger')
            return redirect(request.url)

        if not fichier.filename.lower().endswith('.csv'):
            flash('Veuillez sélectionner un fichier CSV (.csv).', 'danger')
            return redirect(request.url)

        mode = request.form.get('mode', 'ajouter')  # 'ajouter' ou 'synchroniser'

        try:
            contenu = fichier.read().decode('utf-8-sig')

            # Détection automatique du délimiteur
            first_line = contenu.split('\n')[0]
            if ';' in first_line:
                delimiter = ';'
            elif '\t' in first_line:
                delimiter = '\t'
            else:
                delimiter = ','

            lecteur = csv.DictReader(io.StringIO(contenu), delimiter=delimiter)

            conn = get_app_db()
            ajoutes = 0
            mis_a_jour = 0
            ignores = 0
            ids_importes = set()

            # Pré-charger les catégories (hors boucle pour performance)
            cats_db = get_categories_personnes()
            cats_by_cle = {c['cle']: c['cle'] for c in cats_db}
            cats_by_libelle = {c['libelle'].lower(): c['cle'] for c in cats_db}
            synonymes = {
                'élève': 'eleve', 'etudiant': 'eleve', 'étudiant': 'eleve', 'elève': 'eleve',
                'professeur': 'enseignant', 'prof': 'enseignant',
            }

            for ligne in lecteur:
                # Accepter différents noms de colonnes
                nom = (ligne.get('nom') or ligne.get('Nom') or '').strip().upper()
                prenom = (ligne.get('prenom') or ligne.get('Prenom')
                          or ligne.get('Prénom') or ligne.get('prénom') or '').strip().title()
                categorie = (ligne.get('categorie') or ligne.get('Categorie')
                             or ligne.get('Catégorie') or ligne.get('catégorie') or '').strip().lower()
                classe = (ligne.get('classe') or ligne.get('Classe') or '').strip()
                email = (ligne.get('email') or ligne.get('Email') or ligne.get('e-mail') or ligne.get('E-mail') or ligne.get('courriel') or ligne.get('Courriel') or '').strip().lower()

                # Colonnes des champs personnalisés: custom_<nom_champ>
                custom_form_data = {}
                for nom_champ, champ in custom_by_name.items():
                    colonne = f'custom_{nom_champ}'
                    valeur = (ligne.get(colonne) or '').strip()
                    if champ.get('type_champ') == 'case_a_cocher':
                        valeur_norm = valeur.lower()
                        if valeur_norm in ('1', 'true', 'vrai', 'oui', 'yes', 'x'):
                            custom_form_data[colonne] = 'oui'
                        else:
                            custom_form_data[colonne] = ''
                    else:
                        custom_form_data[colonne] = valeur

                # Ignorer les lignes vides ou les séparateurs de catégorie
                if not nom or not prenom:
                    continue
                if nom.startswith('#'):
                    continue

                if categorie in cats_by_cle:
                    categorie = cats_by_cle[categorie]
                elif categorie in cats_by_libelle:
                    categorie = cats_by_libelle[categorie]
                elif categorie in synonymes:
                    categorie = synonymes[categorie]
                elif not categorie:
                    categorie = 'non_enseignant'
                # Sinon : garder la valeur telle quelle (catégorie personnalisée)

                # Vérifier les doublons : par email (prioritaire) ou nom+prénom+catégorie
                existant = None
                if email:
                    existant = conn.execute(
                        'SELECT id FROM personnes WHERE email = ? AND email != ?',
                        (email, '')
                    ).fetchone()
                if not existant:
                    existant = conn.execute(
                        'SELECT id FROM personnes WHERE nom = ? AND prenom = ? AND categorie = ?',
                        (nom, prenom, categorie)
                    ).fetchone()

                if existant:
                    ids_importes.add(existant['id'])
                    if mode == 'synchroniser':
                        # Mettre à jour la classe, l'email et réactiver si désactivée
                        conn.execute(
                            'UPDATE personnes SET classe = ?, email = ?, actif = 1 WHERE id = ?',
                            (classe, email, existant['id'])
                        )
                        sauver_valeurs_champs(existant['id'], 'personne', custom_form_data)
                        mis_a_jour += 1
                    else:
                        ignores += 1
                else:
                    # Vérifier aussi par nom + prénom seul (personne existante
                    # qui aurait changé de catégorie)
                    existant_autre = conn.execute(
                        'SELECT id FROM personnes WHERE nom = ? AND prenom = ? AND actif = 1',
                        (nom, prenom)
                    ).fetchone()

                    if existant_autre and mode == 'synchroniser':
                        # Mettre à jour catégorie + classe + email
                        conn.execute(
                            'UPDATE personnes SET categorie = ?, classe = ?, email = ?, actif = 1 WHERE id = ?',
                            (categorie, classe, email, existant_autre['id'])
                        )
                        ids_importes.add(existant_autre['id'])
                        sauver_valeurs_champs(existant_autre['id'], 'personne', custom_form_data)
                        mis_a_jour += 1
                    else:
                        cursor = conn.execute(
                            'INSERT INTO personnes (nom, prenom, categorie, classe, email) VALUES (?, ?, ?, ?, ?)',
                            (nom, prenom, categorie, classe, email)
                        )
                        ids_importes.add(cursor.lastrowid)
                        sauver_valeurs_champs(cursor.lastrowid, 'personne', custom_form_data)
                        ajoutes += 1

            # Mode synchroniser : désactiver les absents (sauf prêts en cours)
            desactives = 0
            proteges = 0
            if mode == 'synchroniser' and ids_importes:
                placeholders = ','.join('?' * len(ids_importes))
                absentes = conn.execute(f'''
                    SELECT p.id, p.nom, p.prenom,
                           (SELECT COUNT(*) FROM prets
                            WHERE personne_id = p.id AND retour_confirme = 0) as prets_actifs
                    FROM personnes p
                    WHERE p.actif = 1 AND p.id NOT IN ({placeholders})
                ''', list(ids_importes)).fetchall()

                noms_proteges = []
                for absente in absentes:
                    if absente['prets_actifs'] > 0:
                        proteges += 1
                        noms_proteges.append(
                            f"{absente['prenom']} {absente['nom']} "
                            f"({absente['prets_actifs']} prêt(s))"
                        )
                    else:
                        conn.execute(
                            'UPDATE personnes SET actif = 0 WHERE id = ?',
                            (absente['id'],)
                        )
                        desactives += 1

                if noms_proteges:
                    flash(
                        f'⚠️ Personne(s) absente(s) du fichier mais conservée(s) '
                        f'car elles ont des prêts en cours : {", ".join(noms_proteges)}',
                        'warning'
                    )

            conn.commit()

            # Construire le message récapitulatif
            parts = []
            if ajoutes > 0:
                parts.append(f'{ajoutes} ajoutée(s)')
            if mis_a_jour > 0:
                parts.append(f'{mis_a_jour} mise(s) à jour')
            if ignores > 0:
                parts.append(f'{ignores} doublon(s) ignoré(s)')
            if desactives > 0:
                parts.append(f'{desactives} désactivée(s) car absente(s) du fichier')

            if parts:
                flash(f'Import terminé : {", ".join(parts)}.', 'success')
            else:
                flash('Aucune donnée importée. Vérifiez le format du fichier.', 'warning')

            return redirect(url_for('personnes.personnes'))

        except Exception as e:
            flash(f"Erreur lors de l'import : {str(e)}", 'danger')
            return redirect(request.url)

    return render_template('importer.html', champs_custom=champs_custom)



@bp.route('/telecharger-gabarit')
def telecharger_gabarit():
    """Générer un gabarit CSV dynamique basé sur les catégories de personnes configurées."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    champs_custom = [dict(ch) for ch in get_champs_personnalises('personne')]
    custom_columns = [f"custom_{ch['nom_champ']}" for ch in champs_custom]
    columns = ['nom', 'prenom', 'categorie', 'classe', 'email'] + custom_columns
    writer.writerow(columns)

    # Charger les catégories de personnes depuis la base
    cats = get_categories_personnes()

    # Exemples pré-remplis par catégorie (1 exemple + lignes vides)
    exemples = {
        'eleve':         [('DUPONT', 'Marie', '3A', 'marie.dupont@ecole.fr'), ('MARTIN', 'Lucas', '4B', 'lucas.martin@ecole.fr')],
        'enseignant':    [('DUBOIS', 'Sophie', '', 'sophie.dubois@ecole.fr'), ('LAURENT', 'Philippe', '', 'philippe.laurent@ecole.fr')],
        'agent':         [('GARCIA', 'Antonio', '', 'antonio.garcia@ecole.fr')],
        'non_enseignant':[('GIRARD', 'Marc', '', 'marc.girard@ecole.fr')],
    }

    custom_defaults = {}
    for champ in champs_custom:
        nom = champ['nom_champ']
        if champ['type_champ'] == 'choix':
            options = [o.strip() for o in (champ.get('options') or '').split(',') if o.strip()]
            custom_defaults[nom] = options[0] if options else ''
        elif champ['type_champ'] == 'case_a_cocher':
            custom_defaults[nom] = ''
        else:
            custom_defaults[nom] = ''

    def build_custom_values():
        return [custom_defaults.get(ch['nom_champ'], '') for ch in champs_custom]

    for cat in cats:
        cle = cat['cle']
        libelle = cat['libelle'].upper()
        writer.writerow([f'# ══════ {libelle} ══════'] + [''] * (len(columns) - 1))

        # Écrire les exemples connus ou un exemple générique
        lignes_exemple = exemples.get(cle, [])
        if lignes_exemple:
            for nom, prenom, classe, email in lignes_exemple:
                writer.writerow([nom, prenom, cle, classe, email] + build_custom_values())
        else:
            writer.writerow(['NOM', 'Prenom', cle, '', ''] + build_custom_values())

        # Lignes vides pré-remplies avec la catégorie
        nb_vides = 18 if cle == 'eleve' else 8 if cle == 'enseignant' else 5
        for _ in range(nb_vides):
            writer.writerow(['', '', cle, '', ''] + build_custom_values())

    output.seek(0)
    bom = '\ufeff'
    return Response(
        bom + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename=gabarit_personnes.csv'
        }
    )



@bp.route('/categories-personnes', methods=['GET', 'POST'])
@admin_required
def categories_personnes_admin():
    conn = get_app_db()

    if request.method == 'POST':
        libelle = request.form.get('libelle', '').strip()
        icone = request.form.get('icone', 'bi-person').strip()
        couleur_bg = request.form.get('couleur_bg', '#f1f3f4').strip()
        couleur_text = request.form.get('couleur_text', '#5f6368').strip()

        if not libelle:
            flash('Le libellé est obligatoire.', 'danger')
        else:
            # Générer une clé à partir du libellé
            cle = libelle.lower().strip()
            cle = unicodedata.normalize('NFD', cle)
            cle = cle.encode('ascii', 'ignore').decode('ascii')
            cle = cle.replace(' ', '_').replace("'", '').replace('-', '_')
            # Ne garder que alphanumériques et underscores
            cle = ''.join(c for c in cle if c.isalnum() or c == '_')

            # Déterminer l'ordre (après le dernier)
            max_ordre = conn.execute('SELECT MAX(ordre) FROM categories_personnes').fetchone()[0] or 0

            try:
                conn.execute(
                    '''INSERT INTO categories_personnes (cle, libelle, icone, couleur_bg, couleur_text, ordre)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (cle, libelle, icone, couleur_bg, couleur_text, max_ordre + 1)
                )
                conn.commit()
                flash(f'Catégorie « {libelle} » ajoutée !', 'success')
            except Exception:
                flash('Cette catégorie existe déjà.', 'warning')
        return redirect(url_for('personnes.categories_personnes_admin'))

    cats = conn.execute('SELECT * FROM categories_personnes ORDER BY ordre, libelle').fetchall()

    # Compter le nombre de personnes par catégorie
    comptages = {}
    for cat in cats:
        comptages[cat['cle']] = conn.execute(
            'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie = ?', (cat['cle'],)
        ).fetchone()[0]

    return render_template('categories_personnes.html', categories=cats, comptages=comptages)



@bp.route('/categories-personnes/modifier/<int:cat_id>', methods=['POST'])
@admin_required
def modifier_categorie_personne(cat_id):
    conn = get_app_db()
    libelle = request.form.get('libelle', '').strip()
    icone = request.form.get('icone', 'bi-person').strip()
    couleur_bg = request.form.get('couleur_bg', '#f1f3f4').strip()
    couleur_text = request.form.get('couleur_text', '#5f6368').strip()

    if libelle:
        conn.execute(
            '''UPDATE categories_personnes SET libelle=?, icone=?, couleur_bg=?, couleur_text=?
               WHERE id=?''',
            (libelle, icone, couleur_bg, couleur_text, cat_id)
        )
        conn.commit()
        flash('Catégorie modifiée.', 'success')
    return redirect(url_for('personnes.categories_personnes_admin'))



@bp.route('/categories-personnes/supprimer/<int:cat_id>', methods=['POST'])
@admin_required
def supprimer_categorie_personne(cat_id):
    conn = get_app_db()
    cat = conn.execute('SELECT cle, libelle FROM categories_personnes WHERE id = ?', (cat_id,)).fetchone()
    if not cat:
        flash('Catégorie introuvable.', 'danger')
        return redirect(url_for('personnes.categories_personnes_admin'))

    nb = conn.execute(
        'SELECT COUNT(*) FROM personnes WHERE actif = 1 AND categorie = ?', (cat['cle'],)
    ).fetchone()[0]

    if nb > 0:
        # Vérifier si une catégorie de remplacement est fournie
        remplacement_id = request.form.get('remplacement_id', '').strip()
        if not remplacement_id:
            flash(f'Impossible de supprimer « {cat["libelle"]} » : {nb} personne(s) utilisent cette catégorie.', 'danger')
            return redirect(url_for('personnes.categories_personnes_admin'))
        # Récupérer la clé de la catégorie de remplacement
        cat_rempl = conn.execute(
            'SELECT cle, libelle FROM categories_personnes WHERE id = ?', (remplacement_id,)
        ).fetchone()
        if not cat_rempl or cat_rempl['cle'] == cat['cle']:
            flash('Catégorie de remplacement invalide.', 'danger')
            return redirect(url_for('personnes.categories_personnes_admin'))
        # Réaffecter toutes les personnes
        conn.execute(
            'UPDATE personnes SET categorie = ? WHERE actif = 1 AND categorie = ?',
            (cat_rempl['cle'], cat['cle'])
        )
        flash(f'{nb} personne(s) réaffectée(s) vers « {cat_rempl["libelle"]} ».', 'info')

    conn.execute('DELETE FROM categories_personnes WHERE id = ?', (cat_id,))
    conn.commit()
    flash(f'Catégorie « {cat["libelle"]} » supprimée.', 'success')
    return redirect(url_for('personnes.categories_personnes_admin'))



@bp.route('/personnes/historique/<int:personne_id>')
def historique_personne(personne_id):
    """Affiche l'historique chronologique de tous les emprunts d'une personne."""
    conn = get_app_db()

    personne = conn.execute(
        'SELECT * FROM personnes WHERE id = ?', (personne_id,)
    ).fetchone()

    if not personne:
        flash('Personne non trouvée.', 'danger')
        return redirect(url_for('personnes.personnes'))

    # Tous les prêts de cette personne, du plus récent au plus ancien
    prets = conn.execute('''
        SELECT p.*, l.nom AS lieu_nom
        FROM prets p
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.personne_id = ?
        ORDER BY p.date_emprunt DESC
    ''', (personne_id,)).fetchall()

    # Pour chaque prêt, récupérer les items (pret_materiels)
    prets_data = []
    for pret in prets:
        items = conn.execute('''
            SELECT pm.description, pm.materiel_id, i.numero_inventaire, i.marque, i.modele
            FROM pret_materiels pm
            LEFT JOIN inventaire i ON pm.materiel_id = i.id
            WHERE pm.pret_id = ?
        ''', (pret['id'],)).fetchall()
        prets_data.append({
            'pret': pret,
            'materiels': items if items else [],
        })

    # Statistiques rapides
    stats = {
        'total': len(prets),
        'en_cours': sum(1 for p in prets if not p['retour_confirme']),
        'retournes': sum(1 for p in prets if p['retour_confirme']),
    }

    return render_template('historique_personne.html',
                           personne=personne, prets_data=prets_data, stats=stats)


