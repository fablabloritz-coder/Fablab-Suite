"""PretGo — Blueprint : api (endpoints internes)"""
from flask import Blueprint, jsonify, request, url_for
from werkzeug.utils import secure_filename
from database import get_setting
from utils import get_app_db, admin_required, allowed_file, get_categories_personnes, UPLOAD_FOLDER
import os
import string
import uuid

bp = Blueprint('api', __name__)
MAX_UPLOAD_SIZE_BYTES = 12 * 1024 * 1024


@bp.route('/api/personnes')
def api_personnes():
    conn = get_app_db()
    q = ' '.join(request.args.get('q', '').split())

    if q:
        like = f'%{q}%'
        prefix = f'{q}%'
        personnes = conn.execute('''
            SELECT id, nom, prenom, categorie, classe, email
            FROM personnes
            WHERE actif = 1 AND (
                nom LIKE ? OR prenom LIKE ? OR classe LIKE ? OR email LIKE ?
                OR (nom || ' ' || prenom) LIKE ?
                OR (prenom || ' ' || nom) LIKE ?
            )
            ORDER BY
                CASE
                    WHEN nom LIKE ? THEN 0
                    WHEN prenom LIKE ? THEN 1
                    WHEN (nom || ' ' || prenom) LIKE ? THEN 2
                    WHEN (prenom || ' ' || nom) LIKE ? THEN 3
                    ELSE 4
                END,
                nom, prenom
            LIMIT 20
        ''', (like, like, like, like, like, like, prefix, prefix, prefix, prefix)).fetchall()
    else:
        personnes = conn.execute('''
            SELECT id, nom, prenom, categorie, classe, email
            FROM personnes
            WHERE actif = 1
            ORDER BY nom, prenom
        ''').fetchall()


    # Enrichir avec le libellé de catégorie pour le front-end
    cats = {c['cle']: c['libelle'] for c in get_categories_personnes()}
    result = []
    for p in personnes:
        d = dict(p)
        d['categorie_label'] = cats.get(d['categorie'], d['categorie'].replace('_', ' ').title())
        result.append(d)
    return jsonify(result)


@bp.route('/api/personnes/<int:personne_id>/prets-actifs')
def api_prets_actifs_personne(personne_id):
    """Retourne les emprunts actifs (non rendus) pour une personne donnée."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT id, descriptif_objets, date_emprunt, date_retour_prevue
        FROM prets
        WHERE personne_id = ? AND retour_confirme = 0
        ORDER BY date_emprunt DESC
    ''', (personne_id,)).fetchall()
    return jsonify([dict(p) for p in prets])



@admin_required
def api_liste_images():
    """Retourne la liste des images disponibles dans le dossier uploads."""
    images = []
    if os.path.exists(UPLOAD_FOLDER):
        for f in sorted(os.listdir(UPLOAD_FOLDER)):
            if allowed_file(f):
                images.append(f)
    return jsonify(images)



@bp.route('/api/upload-image-materiel', methods=['POST'])
@admin_required
def api_upload_image():
    """Upload une nouvelle image dans la bibliothèque."""
    if 'image' not in request.files:
        return jsonify({'error': 'Aucun fichier envoyé'}), 400
    fichier = request.files['image']
    if fichier.filename == '':
        return jsonify({'error': 'Nom de fichier vide'}), 400
    if not allowed_file(fichier.filename):
        return jsonify({'error': 'Format non autorisé (jpg, png, gif, webp uniquement)'}), 400

    # Vérifie la taille réelle du fichier quand le flux est seekable.
    try:
        pos = fichier.stream.tell()
        fichier.stream.seek(0, os.SEEK_END)
        taille = fichier.stream.tell()
        fichier.stream.seek(pos)
        if taille > MAX_UPLOAD_SIZE_BYTES:
            max_mb = MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
            return jsonify({'error': f'Image trop volumineuse (max {max_mb} Mo)'}), 413
    except Exception:
        # Certains flux ne sont pas seekable, on laisse alors le serveur gérer.
        pass

    # Garder le nom original nettoyé, ajouter un suffixe unique si doublon
    nom_original = secure_filename(fichier.filename)
    if not nom_original:
        return jsonify({'error': 'Nom de fichier invalide'}), 400
    nom_final = nom_original
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    chemin = os.path.join(UPLOAD_FOLDER, nom_final)
    if os.path.exists(chemin):
        base, ext = os.path.splitext(nom_original)
        nom_final = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
        chemin = os.path.join(UPLOAD_FOLDER, nom_final)

    try:
        fichier.save(chemin)
    except Exception as e:
        return jsonify({'error': f'Impossible de sauvegarder l\'image: {str(e)}'}), 500
    return jsonify({'filename': nom_final})



@bp.route('/api/supprimer-image-materiel', methods=['POST'])
@admin_required
def api_supprimer_image():
    """Supprime une image de la bibliothèque (si plus utilisée)."""
    filename = request.json.get('filename', '')
    if not filename or not allowed_file(filename):
        return jsonify({'error': 'Fichier invalide'}), 400
    chemin = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
    if os.path.exists(chemin):
        os.remove(chemin)
    return jsonify({'ok': True})



@bp.route('/api/inventaire')
def api_inventaire():
    """API JSON pour autocomplétion de matériel."""
    conn = get_app_db()
    q = request.args.get('q', '').strip()
    if q:
        items = conn.execute('''
            SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie, image, etat
            FROM inventaire WHERE actif = 1
            AND (numero_inventaire LIKE ? OR marque LIKE ? OR modele LIKE ?
                 OR type_materiel LIKE ? OR notes LIKE ? OR numero_serie LIKE ?)
            ORDER BY CASE WHEN etat = 'disponible' THEN 0 ELSE 1 END,
                     type_materiel, numero_inventaire LIMIT 20
        ''', (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
    else:
        items = conn.execute('''
            SELECT id, type_materiel, marque, modele, numero_inventaire, numero_serie, image, etat
            FROM inventaire WHERE actif = 1
            ORDER BY type_materiel, numero_inventaire
        ''').fetchall()
    return jsonify([dict(i) for i in items])


@bp.route('/api/inventaire/random-scan')
@admin_required
def api_inventaire_random_scan():
    """API JSON : retourne un code d'inventaire aléatoire pour simuler un scan douchette."""
    conn = get_app_db()

    # Priorité aux matériels disponibles pour simuler un flux de prêt réaliste.
    item = conn.execute('''
        SELECT id, numero_inventaire, etat
        FROM inventaire
        WHERE actif = 1 AND etat = 'disponible'
        ORDER BY RANDOM()
        LIMIT 1
    ''').fetchone()

    if not item:
        item = conn.execute('''
            SELECT id, numero_inventaire, etat
            FROM inventaire
            WHERE actif = 1
            ORDER BY RANDOM()
            LIMIT 1
        ''').fetchone()

    if not item:
        return jsonify({
            'ok': False,
            'message': 'Aucun matériel actif trouvé pour simuler un scan.'
        }), 404

    return jsonify({
        'ok': True,
        'id': item['id'],
        'code': item['numero_inventaire'],
        'numero_inventaire': item['numero_inventaire'],
        'etat': item['etat'],
    })



@bp.route('/api/scan')
def api_scan():
    """API JSON : recherche un code scanné (numéro inventaire ou série) et renvoie l'URL de redirection.
    Supporte le nettoyage de préfixe/suffixe configuré dans les réglages."""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'found': False, 'message': 'Aucun code fourni.'})

    # Nettoyage préfixe/suffixe configuré
    prefixe = get_setting('scanner_prefixe', '').strip()
    suffixe = get_setting('scanner_suffixe', '').strip()
    code_clean = code
    if prefixe and code_clean.startswith(prefixe):
        code_clean = code_clean[len(prefixe):]
    if suffixe and code_clean.endswith(suffixe):
        code_clean = code_clean[:-len(suffixe)]
    code_clean = code_clean.strip()

    if not code_clean:
        return jsonify({'found': False, 'message': f'Code vide après nettoyage (préfixe/suffixe).'})

    conn = get_app_db()
    # 1) Chercher dans l'inventaire par numéro d'inventaire ou numéro de série
    mat = conn.execute(
        'SELECT id, type_materiel, marque, modele, numero_inventaire, etat FROM inventaire '
        'WHERE actif = 1 AND (numero_inventaire = ? OR numero_serie = ?)',
        (code_clean, code_clean)
    ).fetchone()

    # Si pas trouvé avec le code nettoyé, essayer avec le code brut
    if not mat and code_clean != code:
        mat = conn.execute(
            'SELECT id, type_materiel, marque, modele, numero_inventaire, etat FROM inventaire '
            'WHERE actif = 1 AND (numero_inventaire = ? OR numero_serie = ?)',
            (code, code)
        ).fetchone()

    if not mat:
        return jsonify({
            'found': False,
            'message': f'Aucun matériel trouvé pour « {code_clean} ».',
            'code_original': code,
            'code_nettoye': code_clean
        })

    # 2) Vérifier l'état du matériel
    if mat['etat'] == 'hors_service':
        label = mat['type_materiel']
        if mat['marque']:
            label += f" {mat['marque']}"
        if mat['modele']:
            label += f" {mat['modele']}"
        return jsonify({
            'found': True,
            'type': 'hors_service',
            'message': f'{label} — Matériel hors service',
            'url': url_for('inventaire.modifier_materiel', mat_id=mat['id']),
        })

    # 3) Vérifier s'il y a un prêt actif pour ce matériel
    pret = conn.execute('''
        SELECT p.id, pe.nom, pe.prenom FROM prets p
        JOIN pret_materiels pm ON pm.pret_id = p.id
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE pm.materiel_id = ? AND p.retour_confirme = 0
        LIMIT 1
    ''', (mat['id'],)).fetchone()

    # Rétrocompat legacy materiel_id
    if not pret:
        pret = conn.execute(
            '''SELECT p.id, pe.nom, pe.prenom FROM prets p
               JOIN personnes pe ON p.personne_id = pe.id
               WHERE p.materiel_id = ? AND p.retour_confirme = 0 LIMIT 1''',
            (mat['id'],)
        ).fetchone()


    label = mat['type_materiel']
    if mat['marque']:
        label += f" {mat['marque']}"
    if mat['modele']:
        label += f" {mat['modele']}"

    if pret:
        return jsonify({
            'found': True,
            'type': 'pret_actif',
            'message': f'{label} — Prêté à {pret["prenom"]} {pret["nom"]}',
            'url': url_for('prets.detail_pret', pret_id=pret['id']),
        })
    else:
        return jsonify({
            'found': True,
            'type': 'materiel',
            'message': f'{label} — {mat["etat"].replace("_", " ").title()}',
            'url': url_for('inventaire.modifier_materiel', mat_id=mat['id']),
        })


@bp.route('/api/parcourir-dossiers')
@admin_required
def api_parcourir_dossiers():
    """API JSON : liste les sous-dossiers d'un chemin donné pour l'explorateur de fichiers."""
    chemin = request.args.get('path', '').strip()

    # Si pas de chemin fourni : lister les lecteurs (Windows) ou /
    if not chemin:
        if os.name == 'nt':
            # Lister les lecteurs disponibles sur Windows
            drives = []
            for letter in string.ascii_uppercase:
                drive = f'{letter}:\\';
                if os.path.exists(drive):
                    drives.append({'name': f'{letter}:', 'path': drive})
            return jsonify({'current': '', 'parent': '', 'folders': drives, 'is_root': True})
        else:
            chemin = '/'

    # Normaliser le chemin
    chemin = os.path.normpath(chemin)

    # Vérifier que le chemin existe et est un dossier
    if not os.path.isdir(chemin):
        return jsonify({'error': f'Le dossier « {chemin} » n\'existe pas.'}), 404

    # Calculer le parent
    parent = os.path.dirname(chemin)
    # Sur Windows, si on est à la racine d'un lecteur (ex. C:\), parent = vide pour revenir aux lecteurs
    is_drive_root = (os.name == 'nt' and os.path.splitdrive(chemin)[1] in ('\\', '/', ''))
    if is_drive_root:
        parent = ''  # Retour à la liste des lecteurs

    # Lister les sous-dossiers
    folders = []
    try:
        for entry in sorted(os.scandir(chemin), key=lambda e: e.name.lower()):
            if entry.is_dir():
                try:
                    # Vérifier qu'on a accès en lecture
                    os.listdir(entry.path)
                    folders.append({'name': entry.name, 'path': entry.path})
                except PermissionError:
                    # Dossier inaccessible, l'afficher grisé
                    folders.append({'name': entry.name, 'path': entry.path, 'locked': True})
    except PermissionError:
        return jsonify({'error': f'Accès refusé au dossier « {chemin} ».'}), 403

    return jsonify({
        'current': chemin,
        'parent': parent,
        'folders': folders,
        'is_root': False
    })

