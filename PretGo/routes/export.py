"""PretGo — Blueprint : export"""
from flask import Blueprint, render_template
from database import get_setting
from utils import get_app_db, admin_required, calcul_depassement_heures, csv_response, get_champs_personnalises
import csv
import io

bp = Blueprint('export', __name__)

@bp.route('/export')
@admin_required
def export_page():
    """Page de sélection des exports CSV."""
    conn = get_app_db()
    counts = {
        'personnes': conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0],
        'prets_en_cours': conn.execute('SELECT COUNT(*) FROM prets WHERE retour_confirme = 0').fetchone()[0],
        'historique': conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0],
        'inventaire': conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0],
    }
    # Compter alertes
    prets_actifs = conn.execute(
        'SELECT date_emprunt, duree_pret_jours, duree_pret_heures, date_retour_prevue FROM prets WHERE retour_confirme = 0'
    ).fetchall()
    nb_alertes = 0
    duree_def = float(get_setting('duree_alerte_defaut', '7'))
    unite_def = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')
    for p in prets_actifs:
        depasse, _ = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_def,
            date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
        )
        if depasse:
            nb_alertes += 1
    counts['alertes'] = nb_alertes
    return render_template('export.html', counts=counts)



@bp.route('/export-prets')
@admin_required
def export_prets():
    conn = get_app_db()
    prets = conn.execute('''
        SELECT pe.nom, pe.prenom, pe.categorie, pe.classe,
               p.descriptif_objets, p.date_emprunt, p.date_retour,
               CASE WHEN p.retour_confirme = 1 THEN 'Oui' ELSE 'Non' END as retourne,
               p.notes, l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        ORDER BY p.date_emprunt DESC
    ''').fetchall()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Objet(s)',
                     'Date emprunt', 'Date retour', 'Retourné', 'Lieu', 'Notes'])

    for pret in prets:
        writer.writerow([
            pret['nom'], pret['prenom'], pret['categorie'], pret['classe'],
            pret['descriptif_objets'], pret['date_emprunt'],
            pret['date_retour'] or '', pret['retourne'],
            pret['lieu_nom'] or '', pret['notes'] or ''
        ])

    return csv_response(output, 'export_historique_prets')



@bp.route('/export-prets-en-cours')
@admin_required
def export_prets_en_cours():
    """Exporter uniquement les prêts non retournés."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT pe.nom, pe.prenom, pe.categorie, pe.classe,
               p.descriptif_objets, p.date_emprunt, p.notes, l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt DESC
    ''').fetchall()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Objet(s)',
                     'Date emprunt', 'Lieu', 'Notes'])

    for pret in prets:
        writer.writerow([
            pret['nom'], pret['prenom'], pret['categorie'], pret['classe'],
            pret['descriptif_objets'], pret['date_emprunt'],
            pret['lieu_nom'] or '', pret['notes'] or ''
        ])

    return csv_response(output, 'export_prets_en_cours')



@bp.route('/export-alertes')
@admin_required
def export_alertes():
    """Exporter les prêts en dépassement (alertes)."""
    conn = get_app_db()
    prets_actifs = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
               l.nom AS lieu_nom
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
    ''').fetchall()

    duree_def = float(get_setting('duree_alerte_defaut', '7'))
    unite_def = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')
    alertes_list = []
    for pret in prets_actifs:
        depasse, heures_dep = calcul_depassement_heures(
            pret['date_emprunt'], pret['duree_pret_heures'], pret['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_def,
            date_retour_prevue=pret['date_retour_prevue'], _heure_fin=heure_fin
        )
        if depasse:
            if heures_dep < 24:
                dep_texte = f"{int(heures_dep)}h{int((heures_dep % 1) * 60):02d}"
            else:
                jours_dep = heures_dep / 24
                dep_texte = f"{int(jours_dep)} jour(s)"
            alertes_list.append({**dict(pret), 'depassement_texte': dep_texte})

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Objet(s)',
                     'Date emprunt', 'Dépassement', 'Lieu', 'Notes'])

    for a in alertes_list:
        writer.writerow([
            a['nom'], a['prenom'], a['categorie'], a['classe'],
            a['descriptif_objets'], a['date_emprunt'],
            a['depassement_texte'], a.get('lieu_nom', '') or '', a['notes'] or ''
        ])

    return csv_response(output, 'export_alertes')



@bp.route('/export-personnes')
@admin_required
def export_personnes():
    conn = get_app_db()
    personnes = conn.execute(
        'SELECT id, nom, prenom, categorie, classe, email FROM personnes WHERE actif = 1 ORDER BY nom, prenom'
    ).fetchall()

    champs_custom = [dict(ch) for ch in get_champs_personnalises('personne')]
    custom_headers = [f"custom_{ch['nom_champ']}" for ch in champs_custom]

    custom_map = {}
    if personnes and champs_custom:
        ids = [p['id'] for p in personnes]
        placeholders = ','.join(['?'] * len(ids))
        rows = conn.execute(f'''
            SELECT vcp.entite_id, cp.nom_champ, vcp.valeur
            FROM valeurs_champs_personnalises vcp
            JOIN champs_personnalises cp ON cp.id = vcp.champ_id
            WHERE cp.entite = 'personne' AND cp.actif = 1 AND vcp.entite_id IN ({placeholders})
        ''', ids).fetchall()
        for r in rows:
            entite_id = r['entite_id']
            if entite_id not in custom_map:
                custom_map[entite_id] = {}
            custom_map[entite_id][r['nom_champ']] = r['valeur']

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Nom', 'Prénom', 'Catégorie', 'Classe', 'Email'] + custom_headers)

    for p in personnes:
        row = [p['nom'], p['prenom'], p['categorie'], p['classe'], p['email'] or '']
        values = custom_map.get(p['id'], {})
        for ch in champs_custom:
            row.append(values.get(ch['nom_champ'], ''))
        writer.writerow(row)

    return csv_response(output, 'export_personnes')



@bp.route('/export-inventaire')
@admin_required
def export_inventaire():
    """Exporter l'inventaire matériel."""
    conn = get_app_db()
    items = conn.execute(
        'SELECT id, type_materiel, marque, modele, numero_serie, numero_inventaire, etat, notes '
        'FROM inventaire WHERE actif = 1 '
        'ORDER BY type_materiel, numero_inventaire'
    ).fetchall()

    champs_custom = [dict(ch) for ch in get_champs_personnalises('materiel')]
    custom_headers = [f"custom_{ch['nom_champ']}" for ch in champs_custom]

    custom_map = {}
    if items and champs_custom:
        ids = [i['id'] for i in items]
        placeholders = ','.join(['?'] * len(ids))
        rows = conn.execute(f'''
            SELECT vcp.entite_id, cp.nom_champ, vcp.valeur
            FROM valeurs_champs_personnalises vcp
            JOIN champs_personnalises cp ON cp.id = vcp.champ_id
            WHERE cp.entite = 'materiel' AND cp.actif = 1 AND vcp.entite_id IN ({placeholders})
        ''', ids).fetchall()
        for r in rows:
            entite_id = r['entite_id']
            if entite_id not in custom_map:
                custom_map[entite_id] = {}
            custom_map[entite_id][r['nom_champ']] = r['valeur']

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Type', 'Marque', 'Modèle', 'N° série', 'N° inventaire', 'État', 'Notes'] + custom_headers)

    for item in items:
        row = [
            item['type_materiel'], item['marque'], item['modele'],
            item['numero_serie'], item['numero_inventaire'], item['etat'], item['notes'] or ''
        ]
        values = custom_map.get(item['id'], {})
        for ch in champs_custom:
            row.append(values.get(ch['nom_champ'], ''))
        writer.writerow(row)

    return csv_response(output, 'export_inventaire')


