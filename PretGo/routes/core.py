"""PretGo — Blueprint : core"""
from flask import Blueprint, render_template, request
from database import get_setting
from utils import get_app_db, calcul_depassement_heures

bp = Blueprint('core', __name__)

@bp.route('/')
def index():
    conn = get_app_db()

    page = request.args.get('page', 1, type=int)
    par_page = 50

    total_actifs = conn.execute(
        'SELECT COUNT(*) FROM prets WHERE retour_confirme = 0'
    ).fetchone()[0]
    total_pages = max(1, (total_actifs + par_page - 1) // par_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * par_page

    prets_actifs = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt DESC
        LIMIT ? OFFSET ?
    ''', (par_page, offset)).fetchall()

    stats = {
        'actifs': total_actifs,
        'retournes': conn.execute(
            'SELECT COUNT(*) FROM prets WHERE retour_confirme = 1'
        ).fetchone()[0],
        'personnes': conn.execute(
            'SELECT COUNT(*) FROM personnes WHERE actif = 1'
        ).fetchone()[0],
    }

    derniers_retours = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 1
        ORDER BY p.date_retour DESC
        LIMIT 5
    ''').fetchall()

    return render_template(
        'index.html',
        prets_actifs=prets_actifs,
        stats=stats,
        derniers_retours=derniers_retours,
        page=page,
        total_pages=total_pages
    )



@bp.route('/recherche')
def recherche():
    conn = get_app_db()
    q = request.args.get('q', '').strip()
    filtre_statut = request.args.get('statut', 'tous')
    resultats_prets = []
    resultats_personnes = []
    resultats_materiel = []

    if q:
        like = f'%{q}%'

        # ── Recherche dans les prêts ──
        query = '''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE (pe.nom LIKE ? OR pe.prenom LIKE ? OR p.descriptif_objets LIKE ? OR pe.classe LIKE ?)
        '''
        params = [like, like, like, like]

        if filtre_statut == 'actifs':
            query += ' AND p.retour_confirme = 0'
        elif filtre_statut == 'retournes':
            query += ' AND p.retour_confirme = 1'

        query += ' ORDER BY p.date_emprunt DESC LIMIT 100'
        resultats_prets = conn.execute(query, params).fetchall()

        # ── Recherche dans les personnes ──
        resultats_personnes = conn.execute('''
            SELECT id, nom, prenom, classe, categorie, email, actif
            FROM personnes
            WHERE actif = 1
              AND (nom LIKE ? OR prenom LIKE ? OR classe LIKE ? OR email LIKE ?)
            ORDER BY nom, prenom
            LIMIT 50
        ''', (like, like, like, like)).fetchall()

        # ── Recherche dans le matériel ──
        resultats_materiel = conn.execute('''
            SELECT id, type_materiel, marque, modele, numero_serie,
                   numero_inventaire, etat
            FROM inventaire
            WHERE (type_materiel LIKE ? OR marque LIKE ? OR modele LIKE ?
                   OR numero_serie LIKE ? OR numero_inventaire LIKE ?)
            ORDER BY type_materiel, marque
            LIMIT 50
        ''', (like, like, like, like, like)).fetchall()

    return render_template('recherche.html',
                           resultats_prets=resultats_prets,
                           resultats_personnes=resultats_personnes,
                           resultats_materiel=resultats_materiel,
                           q=q, filtre_statut=filtre_statut)



@bp.route('/historique')
def historique():
    conn = get_app_db()
    page = request.args.get('page', 1, type=int)
    annee = request.args.get('annee', '').strip()
    par_page = 25
    offset = (page - 1) * par_page

    # Récupérer les années scolaires disponibles pour le filtre
    annees_rows = conn.execute(
        "SELECT DISTINCT annee_scolaire FROM prets WHERE annee_scolaire != '' ORDER BY annee_scolaire DESC"
    ).fetchall()
    annees_disponibles = [r['annee_scolaire'] for r in annees_rows]

    # Requêtes filtrées par année si sélectionnée
    if annee:
        total = conn.execute('SELECT COUNT(*) FROM prets WHERE annee_scolaire = ?', (annee,)).fetchone()[0]
        prets = conn.execute('''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            WHERE p.annee_scolaire = ?
            ORDER BY p.date_emprunt DESC
            LIMIT ? OFFSET ?
        ''', (annee, par_page, offset)).fetchall()
    else:
        total = conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0]
        prets = conn.execute('''
            SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
            FROM prets p
            JOIN personnes pe ON p.personne_id = pe.id
            ORDER BY p.date_emprunt DESC
            LIMIT ? OFFSET ?
        ''', (par_page, offset)).fetchall()


    total_pages = max(1, (total + par_page - 1) // par_page)

    return render_template(
        'historique.html',
        prets=prets,
        page=page,
        total_pages=total_pages,
        total=total,
        annee=annee,
        annees_disponibles=annees_disponibles
    )



@bp.route('/alertes')
def alertes():
    conn = get_app_db()

    prets_actifs = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
    ''').fetchall()

    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    duree_def = float(duree_defaut)
    heure_fin = get_setting('heure_fin_journee', '17:45')

    alertes_list = []
    for pret in prets_actifs:
        depasse, heures_dep = calcul_depassement_heures(
            pret['date_emprunt'], pret['duree_pret_heures'], pret['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_defaut,
            date_retour_prevue=pret['date_retour_prevue'], _heure_fin=heure_fin
        )
        if depasse:
            # Formater le dépassement lisiblement
            if heures_dep < 24:
                dep_texte = f"{int(heures_dep)}h{int((heures_dep % 1) * 60):02d}"
            else:
                jours_dep = heures_dep / 24
                dep_texte = f"{int(jours_dep)} jour(s)"

            alertes_list.append({
                'pret': pret,
                'depassement_heures': heures_dep,
                'depassement_texte': dep_texte,
            })

    return render_template('alertes.html', alertes=alertes_list,
                           duree_defaut=duree_defaut, unite_defaut=unite_defaut)



@bp.route('/fiche-vierge')
def fiche_pret_vierge():
    """Fiche de prêt vierge avec champs à remplir manuellement."""
    nom_etablissement = get_setting('nom_etablissement', '')
    return render_template('fiche_pret_vierge.html', nom_etablissement=nom_etablissement)


