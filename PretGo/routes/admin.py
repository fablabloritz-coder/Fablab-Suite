"""PretGo — Blueprint : admin"""
from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from database import init_db, reset_db, get_setting, set_setting, hash_password, verify_password, generate_recovery_code, DATABASE_PATH, DATA_DIR, DOCUMENTS_DIR, BACKUP_DIR, RECOVERY_CODE_PATH
from utils import get_app_db, admin_required, calculer_annee_scolaire, liberer_materiels_pret, calcul_depassement_heures, rate_limiter, UPLOAD_FOLDER, effectuer_backup, envoyer_rappels_alertes_email
from datetime import datetime, timedelta
import csv
import io
import logging
import os
import random
import re
import unicodedata
import zipfile
import urllib.request
import urllib.error
import urllib.parse

_audit = logging.getLogger('pretgo.audit')

bp = Blueprint('admin', __name__)


@bp.route('/admin/reglages/test-zebra-url', methods=['POST'])
@admin_required
def admin_test_zebra_url():
    """Teste une URL Zebra HTTP et propose un endpoint API probable."""
    payload = request.get_json(silent=True) or {}
    raw_url = (payload.get('url') or '').strip()
    if not raw_url:
        return jsonify({'success': False, 'error': 'Veuillez saisir une URL Zebra.'}), 400

    normalized = raw_url if '://' in raw_url else f'http://{raw_url}'
    normalized = normalized.rstrip('/')

    parsed = urllib.parse.urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return jsonify({'success': False, 'error': 'URL invalide.'}), 400

    base = f'{parsed.scheme}://{parsed.netloc}'

    candidates = [normalized]
    for suffix in ('print-code.php', 'zpl', 'api/zpl', 'print', 'api/print', 'send', 'api/send', 'zpl.html'):
        candidate = f'{base}/{suffix}'
        if candidate not in candidates:
            candidates.append(candidate)

    results = []

    for url in candidates[:10]:
        status = None
        content_type = ''
        preview = ''
        api_candidate = False
        is_web_z_print = url.lower().endswith('/print-code.php')

        try:
            if is_web_z_print:
                data = urllib.parse.urlencode({'qtt': 1, 'code': 'PRETGO-TEST'}).encode('utf-8')
                req = urllib.request.Request(url, data=data, method='POST')
                req.add_header('Content-Type', 'application/x-www-form-urlencoded; charset=utf-8')
            else:
                req = urllib.request.Request(url, data=b'PRETGO_ZPL_PROBE', method='POST')
                req.add_header('Content-Type', 'text/plain; charset=utf-8')

            with urllib.request.urlopen(req, timeout=4) as resp:
                status = getattr(resp, 'status', None) or resp.getcode()
                content_type = (resp.headers.get('Content-Type', '') or '').lower()
                preview = resp.read(220).decode('utf-8', errors='replace').strip().replace('\n', ' ')

        except urllib.error.HTTPError as e:
            status = e.code
            content_type = (e.headers.get('Content-Type', '') if e.headers else '').lower()
            try:
                preview = e.read(220).decode('utf-8', errors='replace').strip().replace('\n', ' ')
            except Exception:
                preview = str(e)
        except Exception as e:
            preview = str(e)

        preview_lower = (preview or '').lower()
        html_like = 'text/html' in (content_type or '') or '<!doctype html' in preview_lower or '<html' in preview_lower

        if status is not None:
            if is_web_z_print:
                api_candidate = 200 <= status < 300
            else:
                api_candidate = (200 <= status < 300 or status in (400, 401, 403, 405, 415)) and (not html_like)

        results.append({
            'url': url,
            'status': status,
            'content_type': content_type,
            'preview': preview[:160],
            'api_candidate': api_candidate,
            'mode': 'web-z-print' if is_web_z_print else 'zpl-http'
        })

    best = next((r for r in results if r['api_candidate'] and r['mode'] == 'web-z-print'), None)
    if not best:
        best = next((r for r in results if r['api_candidate']), None)
    if best:
        mode_msg = ' (mode Web-Z-Print)' if best.get('mode') == 'web-z-print' else ''
        return jsonify({
            'success': True,
            'message': f"Endpoint détecté{mode_msg} : {best['url']}",
            'suggested_url': best['url'],
            'results': results
        })

    return jsonify({
        'success': False,
        'error': 'Aucun endpoint API Zebra probable détecté automatiquement.',
        'results': results
    }), 404

@bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin.admin_dashboard'))

    if request.method == 'POST':
        action = request.form.get('action', 'login')

        if action == 'login':
            # Rate limiting : max 5 tentatives / 60 s
            client_ip = request.remote_addr or '0.0.0.0'
            if rate_limiter.is_limited(client_ip, max_hits=5, window=60):
                flash('Trop de tentatives. Réessayez dans une minute.', 'danger')
                return render_template('admin_login.html',
                                       password_changed=get_setting('password_changed', '0'))
            password = request.form.get('password', '')
            stored_hash = get_setting('admin_password')
            if stored_hash and verify_password(password, stored_hash):
                # Migration automatique : rehacher avec l'algorithme sécurisé
                if not stored_hash.startswith(('scrypt:', 'pbkdf2:')):
                    set_setting('admin_password', hash_password(password))
                # Régénérer la session pour éviter le session fixation
                session.clear()
                session['admin_logged_in'] = True
                _audit.info('LOGIN admin depuis %s', request.remote_addr)

                # Première connexion ? Forcer le changement de mot de passe
                if get_setting('password_changed', '0') == '0':
                    flash('Bienvenue ! Veuillez personnaliser votre mot de passe administrateur.', 'info')
                    return redirect(url_for('admin.admin_setup_password'))

                flash('Connexion administrateur réussie.', 'success')
                next_url = request.args.get('next') or url_for('admin.admin_dashboard')
                if not next_url.startswith('/') or next_url.startswith('//'):
                    next_url = url_for('admin.admin_dashboard')
                return redirect(next_url)
            else:
                flash('Mot de passe incorrect.', 'danger')

        elif action == 'recovery':
            code = request.form.get('recovery_code', '').strip().upper()
            stored_hash = get_setting('recovery_code_hash')
            if stored_hash and verify_password(code, stored_hash):
                # Code valide → permettre de définir un nouveau mot de passe
                session['recovery_validated'] = True
                flash('Code de récupération valide. Définissez votre nouveau mot de passe.', 'success')
                return redirect(url_for('admin.admin_reset_password'))
            else:
                flash('Code de récupération incorrect.', 'danger')

    return render_template('admin_login.html',
                           password_changed=get_setting('password_changed', '0'))



@bp.route('/admin/reset-password', methods=['GET', 'POST'])
def admin_reset_password():
    if not session.get('recovery_validated'):
        flash('Accès non autorisé.', 'danger')
        return redirect(url_for('admin.admin_login'))

    if request.method == 'POST':
        nouveau = request.form.get('nouveau_mdp', '')
        confirm = request.form.get('confirm_mdp', '')
        if len(nouveau) < 4:
            flash('Le nouveau mot de passe doit faire au moins 4 caractères.', 'danger')
        elif nouveau != confirm:
            flash('Les deux mots de passe ne correspondent pas.', 'danger')
        else:
            set_setting('admin_password', hash_password(nouveau))
            # Régénérer un nouveau code de récupération
            new_code = generate_recovery_code()
            set_setting('recovery_code_hash', hash_password(new_code))
            session.pop('recovery_validated', None)
            _audit.info('PASSWORD_RESET via recovery depuis %s', request.remote_addr)
            flash('Mot de passe réinitialisé. Un nouveau code de récupération a été généré dans le dossier data/.', 'success')
            return redirect(url_for('admin.admin_login'))

    return render_template('admin_reset_password.html')



@bp.route('/admin/setup-password', methods=['GET', 'POST'])
@admin_required
def admin_setup_password():
    """Page de première personnalisation du mot de passe (après connexion avec le MDP par défaut)."""
    # Si le mot de passe a déjà été changé, pas besoin d'être ici
    if get_setting('password_changed', '0') == '1':
        return redirect(url_for('admin.admin_dashboard'))

    if request.method == 'POST':
        nouveau = request.form.get('nouveau_mdp', '')
        confirm = request.form.get('confirm_mdp', '')

        if len(nouveau) < 4:
            flash('Le mot de passe doit faire au moins 4 caractères.', 'danger')
        elif nouveau != confirm:
            flash('Les deux mots de passe ne correspondent pas.', 'danger')
        else:
            # Enregistrer le nouveau mot de passe
            set_setting('admin_password', hash_password(nouveau))
            set_setting('password_changed', '1')
            # Générer le code de récupération unique pour cette installation
            new_code = generate_recovery_code()
            set_setting('recovery_code_hash', hash_password(new_code))
            _audit.info('PASSWORD_CHANGED (setup) depuis %s', request.remote_addr)
            flash('Mot de passe personnalisé avec succès ! Votre code de récupération unique a été généré dans le dossier data/.', 'success')
            return redirect(url_for('admin.admin_dashboard'))

    return render_template('admin_setup_password.html')



@bp.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    _audit.info('LOGOUT admin depuis %s', request.remote_addr)
    flash('Déconnexion administrateur.', 'info')
    return redirect(url_for('core.index'))



@bp.route('/admin')
@admin_required
def admin_dashboard():
    conn = get_app_db()
    stats = {
        'personnes': conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0],
        'prets_actifs': conn.execute('SELECT COUNT(*) FROM prets WHERE retour_confirme = 0').fetchone()[0],
        'prets_total': conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0],
        'categories': conn.execute('SELECT COUNT(*) FROM categories_materiel').fetchone()[0],
        'inventaire': conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0],
    }
    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    return render_template('admin_dashboard.html', stats=stats,
                           duree_defaut=duree_defaut, unite_defaut=unite_defaut)



@bp.route('/admin/rentree')
@admin_required
def admin_rentree():
    """Assistant de rentrée scolaire : vérifier les prêts, importer la nouvelle liste."""
    conn = get_app_db()
    annee_courante = calculer_annee_scolaire()

    # Prêts non rendus
    prets_non_rendus = conn.execute('''
        SELECT p.*, pe.nom, pe.prenom, pe.classe, pe.categorie,
               p.classe_snapshot, p.annee_scolaire
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
    ''').fetchall()

    # Statistiques
    stats = {
        'personnes_actives': conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0],
        'personnes_inactives': conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 0').fetchone()[0],
        'prets_non_rendus': len(prets_non_rendus),
        'prets_total_annee': conn.execute(
            'SELECT COUNT(*) FROM prets WHERE annee_scolaire = ?', (annee_courante,)
        ).fetchone()[0],
    }

    # Années scolaires présentes dans la base
    annees = conn.execute(
        "SELECT DISTINCT annee_scolaire FROM prets WHERE annee_scolaire != '' ORDER BY annee_scolaire DESC"
    ).fetchall()
    annees_disponibles = [r['annee_scolaire'] for r in annees]

    return render_template('admin_rentree.html',
                           annee_courante=annee_courante,
                           prets_non_rendus=prets_non_rendus,
                           stats=stats,
                           annees_disponibles=annees_disponibles)



@bp.route('/admin/rentree/retour-groupe', methods=['POST'])
@admin_required
def rentree_retour_groupe():
    """Retourner en masse tous les prêts sélectionnés (assistant rentrée)."""
    pret_ids = request.form.getlist('pret_ids')
    if not pret_ids:
        flash('Aucun prêt sélectionné.', 'warning')
        return redirect(url_for('admin.admin_rentree'))

    conn = get_app_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    retournes = 0
    for pid in pret_ids:
        try:
            pid_int = int(pid)
            # Marquer le prêt comme retourné
            conn.execute(
                'UPDATE prets SET retour_confirme = 1, date_retour = ? WHERE id = ? AND retour_confirme = 0',
                (now, pid_int)
            )
            liberer_materiels_pret(conn, pid_int)
            retournes += 1
        except (ValueError, TypeError):
            pass

    conn.commit()
    flash(f'{retournes} prêt(s) marqué(s) comme retourné(s).', 'success')
    return redirect(url_for('admin.admin_rentree'))



@bp.route('/admin/reglages', methods=['GET', 'POST'])
@admin_required
def admin_reglages():
    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'duree_alerte':
            duree = request.form.get('duree_alerte_defaut', '7').strip()
            unite = request.form.get('duree_alerte_unite', 'jours')
            try:
                val = float(duree)
                if val > 0:
                    set_setting('duree_alerte_defaut', duree)
                    set_setting('duree_alerte_unite', unite)
                    label = 'heure(s)' if unite == 'heures' else 'jour(s)'
                    flash(f'Durée d\'alerte par défaut : {duree} {label}.', 'success')
                else:
                    flash('Veuillez entrer une valeur positive.', 'danger')
            except ValueError:
                flash('Veuillez entrer une valeur numérique valide.', 'danger')

        elif action == 'changer_mdp':
            ancien = request.form.get('ancien_mdp', '')
            nouveau = request.form.get('nouveau_mdp', '')
            confirm = request.form.get('confirm_mdp', '')

            stored_hash = get_setting('admin_password')
            if not verify_password(ancien, stored_hash):
                flash('L\'ancien mot de passe est incorrect.', 'danger')
            elif len(nouveau) < 4:
                flash('Le nouveau mot de passe doit faire au moins 4 caractères.', 'danger')
            elif nouveau != confirm:
                flash('Les deux mots de passe ne correspondent pas.', 'danger')
            else:
                set_setting('admin_password', hash_password(nouveau))
                # Régénérer le code de récupération à chaque changement de MDP
                new_code = generate_recovery_code()
                set_setting('recovery_code_hash', hash_password(new_code))
                flash('Mot de passe modifié. Un nouveau code de récupération a été généré dans data/.', 'success')

        elif action == 'impression':
            set_setting('impression_zebra_active', '1' if request.form.get('impression_zebra_active') else '0')
            set_setting('impression_zebra_methode', request.form.get('impression_zebra_methode', 'serial'))
            set_setting('impression_port', request.form.get('impression_port', 'COM3').strip())
            set_setting('impression_baud', request.form.get('impression_baud', '38400'))
            set_setting('impression_tearoff', request.form.get('impression_tearoff', '018').strip())
            set_setting('impression_zebra_url', request.form.get('impression_zebra_url', '').strip())
            set_setting('impression_zpl_template', request.form.get('impression_zpl_template', '').strip())
            set_setting('impression_etiquette_largeur', request.form.get('impression_etiquette_largeur', '51'))
            set_setting('impression_etiquette_hauteur', request.form.get('impression_etiquette_hauteur', '25'))
            set_setting('impression_colonnes', request.form.get('impression_colonnes', '4'))
            set_setting('impression_lignes', request.form.get('impression_lignes', '11'))
            set_setting('impression_police', request.form.get('impression_police', 'Arial'))
            set_setting('impression_taille_barcode', request.form.get('impression_taille_barcode', '60'))
            set_setting('impression_taille_texte', request.form.get('impression_taille_texte', '8'))
            set_setting('impression_taille_sous_texte', request.form.get('impression_taille_sous_texte', '6'))
            set_setting('impression_texte_libre', request.form.get('impression_texte_libre', '').strip())
            flash('Paramètres d\'impression enregistrés.', 'success')

        elif action == 'nom_etablissement':
            nom_etab = request.form.get('nom_etablissement', '').strip()
            set_setting('nom_etablissement', nom_etab)
            flash('Nom de l\'établissement enregistré.', 'success')

        elif action == 'mode_scanner':
            mode = request.form.get('mode_scanner', 'les_deux')
            if mode in ('webcam', 'douchette', 'les_deux'):
                set_setting('mode_scanner', mode)
                labels = {'webcam': 'Webcam uniquement', 'douchette': 'Douchette USB uniquement', 'les_deux': 'Webcam + Douchette'}
                flash(f'Mode de scan : {labels[mode]}.', 'success')
            else:
                flash('Mode de scanner invalide.', 'danger')

        elif action == 'heure_fin_journee':
            heure = request.form.get('heure_fin_journee', '17:45').strip()
            # Valider le format HH:MM
            if re.match(r'^\d{1,2}:\d{2}$', heure):
                h, m = heure.split(':')
                if 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
                    set_setting('heure_fin_journee', heure)
                    flash(f'Heure de fin de journée : {heure.replace(":", "h")}.', 'success')
                else:
                    flash('Heure invalide.', 'danger')
            else:
                flash('Format d\'heure invalide (attendu HH:MM).', 'danger')

        elif action == 'scanner_prefixe_suffixe':
            prefixe = request.form.get('scanner_prefixe', '').strip()
            suffixe = request.form.get('scanner_suffixe', '').strip()
            set_setting('scanner_prefixe', prefixe)
            set_setting('scanner_suffixe', suffixe)
            msg = 'Préfixe/suffixe de douchette enregistrés.'
            if prefixe:
                msg += f' Préfixe : « {prefixe} »'
            if suffixe:
                msg += f' Suffixe : « {suffixe} »'
            flash(msg, 'success')

        elif action == 'theme_reset':
            # Réinitialiser le thème aux valeurs par défaut
            set_setting('theme_couleur_primaire', '#1a73e8')
            set_setting('theme_couleur_navbar', '#1a56db')
            set_setting('theme_nom_application', 'PretGo')
            set_setting('theme_logo', '')
            set_setting('theme_mode_sombre', '0')
            flash('Thème réinitialisé aux valeurs par défaut.', 'success')

        elif action == 'theme':
            couleur_primaire = request.form.get('theme_couleur_primaire', '#1a73e8').strip()
            couleur_navbar = request.form.get('theme_couleur_navbar', '#1a56db').strip()
            # Valider le format des couleurs (prévenir injection CSS)
            import re as _re
            _color_re = _re.compile(r'^#[0-9a-fA-F]{6}$')
            if not _color_re.match(couleur_primaire):
                couleur_primaire = '#1a73e8'
            if not _color_re.match(couleur_navbar):
                couleur_navbar = '#1a56db'
            mode_sombre = '1' if request.form.get('theme_mode_sombre') else '0'
            set_setting('theme_couleur_primaire', couleur_primaire)
            set_setting('theme_couleur_navbar', couleur_navbar)
            set_setting('theme_mode_sombre', mode_sombre)

            flash('Thème personnalisé enregistré.', 'success')

        elif action == 'backup_auto':
            set_setting('backup_auto_active', '1' if request.form.get('backup_auto_active') else '0')
            set_setting('backup_auto_frequence', request.form.get('backup_auto_frequence', 'quotidien'))
            nombre_max = request.form.get('backup_auto_nombre_max', '5').strip()
            try:
                nombre_max = str(max(1, int(nombre_max)))
            except (ValueError, TypeError):
                nombre_max = '5'
            set_setting('backup_auto_nombre_max', nombre_max)
            chemin = request.form.get('backup_auto_chemin', '').strip()
            set_setting('backup_auto_chemin', chemin)
            flash('Paramètres de sauvegarde automatique enregistrés.', 'success')

        elif action == 'backup_auto_maintenant':
            chemin = get_setting('backup_auto_chemin', '').strip() or None
            success, message, _ = effectuer_backup(chemin)
            if success:
                set_setting('backup_auto_derniere', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                set_setting('backup_auto_erreur', '')
                flash(f'Sauvegarde effectuée avec succès ! {message}', 'success')
            else:
                set_setting('backup_auto_erreur', f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} — {message}')
                flash(f'Erreur lors de la sauvegarde : {message}', 'danger')

        elif action == 'email_rappels_settings':
            set_setting('rappel_email_active', '1' if request.form.get('rappel_email_active') else '0')
            set_setting('rappel_email_smtp_host', request.form.get('rappel_email_smtp_host', '').strip())
            set_setting('rappel_email_smtp_port', request.form.get('rappel_email_smtp_port', '587').strip() or '587')
            set_setting('rappel_email_smtp_user', request.form.get('rappel_email_smtp_user', '').strip())
            # Ne jamais écraser le mot de passe si le champ est vide
            smtp_password = request.form.get('rappel_email_smtp_password', '')
            if smtp_password:
                set_setting('rappel_email_smtp_password', smtp_password)
            set_setting('rappel_email_use_tls', '1' if request.form.get('rappel_email_use_tls') else '0')
            set_setting('rappel_email_use_ssl', '1' if request.form.get('rappel_email_use_ssl') else '0')
            set_setting('rappel_email_from', request.form.get('rappel_email_from', '').strip())
            set_setting('rappel_email_reply_to', request.form.get('rappel_email_reply_to', '').strip())
            set_setting('rappel_email_subject', request.form.get('rappel_email_subject', '').strip() or '[PretGo] Rappel de retour de matériel')

            cooldown = request.form.get('rappel_email_cooldown_heures', '24').strip()
            try:
                cooldown_val = float(cooldown)
                if cooldown_val < 0:
                    cooldown_val = 0
                set_setting('rappel_email_cooldown_heures', str(cooldown_val))
                flash('Paramètres email des rappels enregistrés.', 'success')
            except ValueError:
                flash('Cooldown invalide. Valeur numérique attendue.', 'danger')

        elif action == 'email_rappels_send_now':
            conn = get_app_db()
            stats = envoyer_rappels_alertes_email(conn)
            conn.commit()
            if stats.get('error'):
                flash(stats['error'], 'danger')
            else:
                flash(
                    f"Rappels envoyés: {stats['envoyes']} | échecs: {stats['echecs']} | "
                    f"sans email: {stats['ignores_sans_email']} | cooldown: {stats['ignores_cooldown']}",
                    'success' if stats['echecs'] == 0 else 'warning'
                )

        return redirect(url_for('admin.admin_reglages'))

    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')
    legacy_zpl = '^XA^CI27^FO15,20^BY2^BCN,80,N^FD{numero_inventaire}^FS^FO25,130^A0,50,28^FD{numero_inventaire}^FS^XZ'
    previous_default_zpl = '^XA^CI27^FO20,15^BY2^BCN,65,N,N,N^FD{numero_inventaire}^FS^FO20,90^A0N,26,24^FD{numero_inventaire}^FS^FO20,120^A0N,20,18^FD{type} {marque} {modele}^FS^FO20,145^A0N,16,16^FD{texte_libre}^FS^XZ'
    zpl_default = '^XA^CI27^FO20,15^BY2^BCN,{barcode_height},N,N,N^FD{numero_inventaire}^FS^FO20,{y_num}^A0N,{text_height},{text_width}^FD{numero_inventaire}^FS^FO20,{y_sub}^A0N,{sub_height},{sub_width}^FD{type} {marque} {modele}^FS^FO20,{y_free}^A0N,{free_height},{free_width}^FD{texte_libre}^FS^XZ'
    zpl_template = get_setting('impression_zpl_template', zpl_default)
    if zpl_template in (legacy_zpl, previous_default_zpl):
        zpl_template = zpl_default
    return render_template('admin_reglages.html',
                           duree_defaut=duree_defaut, unite_defaut=unite_defaut,
                           nom_etablissement=get_setting('nom_etablissement', ''),
                           imp_zebra_active=get_setting('impression_zebra_active', '0'),
                           imp_zebra_methode=get_setting('impression_zebra_methode', 'serial'),
                           imp_port=get_setting('impression_port', 'COM3'),
                           imp_baud=get_setting('impression_baud', '38400'),
                           imp_tearoff=get_setting('impression_tearoff', '018'),
                           imp_zebra_url=get_setting('impression_zebra_url', 'http://localhost:9100'),
                           imp_zpl_template=zpl_template,
                           imp_largeur=get_setting('impression_etiquette_largeur', '51'),
                           imp_hauteur=get_setting('impression_etiquette_hauteur', '25'),
                           imp_colonnes=get_setting('impression_colonnes', '4'),
                           imp_lignes=get_setting('impression_lignes', '11'),
                           imp_police=get_setting('impression_police', 'Arial'),
                           imp_taille_barcode=get_setting('impression_taille_barcode', '60'),
                           imp_taille_texte=get_setting('impression_taille_texte', '8'),
                           imp_taille_sous_texte=get_setting('impression_taille_sous_texte', '6'),
                           imp_texte_libre=get_setting('impression_texte_libre', ''),
                           mode_scanner=get_setting('mode_scanner', 'les_deux'),
                           heure_fin_journee=get_setting('heure_fin_journee', '17:45'),
                           scanner_prefixe=get_setting('scanner_prefixe', ''),
                           scanner_suffixe=get_setting('scanner_suffixe', ''),
                           theme_couleur_primaire=get_setting('theme_couleur_primaire', '#1a73e8'),
                           theme_couleur_navbar=get_setting('theme_couleur_navbar', '#1a56db'),
                           theme_logo=get_setting('theme_logo', ''),
                           theme_nom_application=get_setting('theme_nom_application', 'PretGo'),
                           theme_mode_sombre=get_setting('theme_mode_sombre', '0'),
                           backup_auto_active=get_setting('backup_auto_active', '0'),
                           backup_auto_frequence=get_setting('backup_auto_frequence', 'quotidien'),
                           backup_auto_nombre_max=get_setting('backup_auto_nombre_max', '5'),
                           backup_auto_chemin=get_setting('backup_auto_chemin', ''),
                           backup_auto_derniere=get_setting('backup_auto_derniere', ''),
                           backup_auto_erreur=get_setting('backup_auto_erreur', ''),
                           rappel_email_active=get_setting('rappel_email_active', '0'),
                           rappel_email_smtp_host=get_setting('rappel_email_smtp_host', ''),
                           rappel_email_smtp_port=get_setting('rappel_email_smtp_port', '587'),
                           rappel_email_smtp_user=get_setting('rappel_email_smtp_user', ''),
                           rappel_email_use_tls=get_setting('rappel_email_use_tls', '1'),
                           rappel_email_use_ssl=get_setting('rappel_email_use_ssl', '0'),
                           rappel_email_from=get_setting('rappel_email_from', ''),
                           rappel_email_reply_to=get_setting('rappel_email_reply_to', ''),
                           rappel_email_subject=get_setting('rappel_email_subject', '[PretGo] Rappel de retour de matériel'),
                           rappel_email_cooldown_heures=get_setting('rappel_email_cooldown_heures', '24'))



@bp.route('/admin/reset-db', methods=['POST'])
@admin_required
def admin_reset_db():
    confirmation = request.form.get('confirmation', '')
    if confirmation == 'REINITIALISER':
        reset_db()
        _audit.info('RESET_DB depuis %s', request.remote_addr)
        session.pop('admin_logged_in', None)
        flash('Base de données réinitialisée avec succès. Toutes les données ont été supprimées.', 'success')
        return redirect(url_for('core.index'))
    else:
        flash('La confirmation est incorrecte. Tapez REINITIALISER en majuscules.', 'danger')
        return redirect(url_for('admin.admin_reglages'))


@bp.route('/admin/reset-db-partiel', methods=['POST'])
@admin_required
def admin_reset_db_partiel():
    """Réinitialisation sélective de certaines données métier."""
    selected = set(request.form.getlist('targets'))
    confirmation = request.form.get('confirmation_partielle', '').strip()

    labels = {
        'materiel': 'Matériel',
        'personnes': 'Personnes',
        'prets': 'Prêts / Historique',
        'cat_materiel': 'Catégories matériel',
        'cat_personnes': 'Catégories personnes',
        'lieux': 'Lieux',
        'champs_materiel': 'Champs personnalisés matériel',
        'champs_personnes': 'Champs personnalisés personnes',
    }

    selected = {s for s in selected if s in labels}
    if not selected:
        flash('Sélectionnez au moins un élément à réinitialiser.', 'warning')
        return redirect(url_for('admin.admin_reglages'))

    if confirmation != 'REINITIALISER_SELECTION':
        flash('Confirmation invalide. Tapez REINITIALISER_SELECTION.', 'danger')
        return redirect(url_for('admin.admin_reglages'))

    conn = get_app_db()

    # Garde-fous de cohérence
    if 'personnes' in selected and 'prets' not in selected:
        nb_prets = conn.execute(
            'SELECT COUNT(*) FROM prets WHERE personne_id IS NOT NULL'
        ).fetchone()[0]
        if nb_prets > 0:
            flash('Impossible de réinitialiser "Personnes" sans "Prêts / Historique" : l\'historique des prêts référence encore des personnes.', 'danger')
            return redirect(url_for('admin.admin_reglages'))

    if 'materiel' in selected and 'prets' not in selected:
        nb_prets_legacy = conn.execute(
            'SELECT COUNT(*) FROM prets WHERE materiel_id IS NOT NULL AND retour_confirme = 0'
        ).fetchone()[0]
        nb_prets_multi = conn.execute('''
            SELECT COUNT(*)
            FROM pret_materiels pm
            JOIN prets p ON p.id = pm.pret_id
            WHERE pm.materiel_id IS NOT NULL AND p.retour_confirme = 0
        ''').fetchone()[0]
        if nb_prets_legacy + nb_prets_multi > 0:
            flash('Impossible de réinitialiser "Matériel" sans "Prêts / Historique" : des prêts actifs sont liés à du matériel.', 'danger')
            return redirect(url_for('admin.admin_reglages'))

    if 'cat_materiel' in selected and 'materiel' not in selected:
        nb_mat = conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0]
        if nb_mat > 0:
            flash('Impossible de réinitialiser "Catégories matériel" sans "Matériel" tant que des matériels existent.', 'danger')
            return redirect(url_for('admin.admin_reglages'))

    if 'cat_personnes' in selected and 'personnes' not in selected:
        nb_pers = conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0]
        if nb_pers > 0:
            flash('Impossible de réinitialiser "Catégories personnes" sans "Personnes" tant que des personnes existent.', 'danger')
            return redirect(url_for('admin.admin_reglages'))

    try:
        # 1) Prêts / historique
        if 'prets' in selected:
            conn.execute('DELETE FROM pret_materiels')
            conn.execute('DELETE FROM prets')
            conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE actif = 1")

        # 2) Personnes
        if 'personnes' in selected:
            conn.execute('DELETE FROM valeurs_champs_personnalises WHERE champ_id IN (SELECT id FROM champs_personnalises WHERE entite = ?)', ('personne',))
            conn.execute('DELETE FROM personnes')

        # 3) Matériel
        if 'materiel' in selected:
            if 'prets' not in selected:
                # Conserver l'historique tout en détachant les références matériel
                conn.execute('UPDATE prets SET materiel_id = NULL WHERE retour_confirme = 1')
                conn.execute('''
                    UPDATE pret_materiels
                    SET materiel_id = NULL
                    WHERE pret_id IN (SELECT id FROM prets WHERE retour_confirme = 1)
                ''')

            conn.execute('DELETE FROM valeurs_champs_personnalises WHERE champ_id IN (SELECT id FROM champs_personnalises WHERE entite = ?)', ('materiel',))
            conn.execute('DELETE FROM inventaire')

        # 4) Catégories matériel
        if 'cat_materiel' in selected:
            conn.execute('DELETE FROM categories_materiel')
            categories_defaut = [
                ('Informatique', 'PC'), ('Audio/Vidéo', 'AV'), ('Sport', 'SPT'),
                ('Livres', 'LIV'), ('Outils', 'OUT'), ('Fournitures', 'FRN'),
                ('Réseau', 'NET'), ('Autre', 'DIV')
            ]
            for nom, prefixe in categories_defaut:
                conn.execute('INSERT INTO categories_materiel (nom, prefixe_inventaire) VALUES (?, ?)', (nom, prefixe))

        # 5) Catégories personnes
        if 'cat_personnes' in selected:
            conn.execute('DELETE FROM categories_personnes')
            categories_personnes_defaut = [
                ('eleve', 'Élève', 'bi-mortarboard', '#e8f0fe', '#1a73e8', 1),
                ('enseignant', 'Enseignant', 'bi-person-workspace', '#e6f4ea', '#0d904f', 2),
                ('agent', 'Agent', 'bi-person-badge', '#fef7e0', '#ea8600', 3),
                ('non_enseignant', 'Non enseignant', 'bi-person', '#f1f3f4', '#5f6368', 4),
            ]
            for row in categories_personnes_defaut:
                conn.execute(
                    '''INSERT INTO categories_personnes (cle, libelle, icone, couleur_bg, couleur_text, ordre)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    row
                )

        # 6) Lieux
        if 'lieux' in selected:
            if 'prets' not in selected:
                conn.execute('UPDATE prets SET lieu_id = NULL')
            conn.execute('DELETE FROM lieux')
            for nom_lieu in ['Salle informatique', 'CDI', 'Salle de réunion', 'Bureau administratif', 'Atelier', 'Gymnase']:
                conn.execute('INSERT INTO lieux (nom) VALUES (?)', (nom_lieu,))

        # 7) Champs personnalisés
        if 'champs_materiel' in selected:
            conn.execute("DELETE FROM champs_personnalises WHERE entite = 'materiel'")
        if 'champs_personnes' in selected:
            conn.execute("DELETE FROM champs_personnalises WHERE entite = 'personne'")

        conn.commit()

        resume = ', '.join(labels[k] for k in labels if k in selected)
        _audit.info('RESET_DB_PARTIEL (%s) depuis %s', ','.join(sorted(selected)), request.remote_addr)
        flash(f'Réinitialisation sélective effectuée : {resume}.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Erreur lors de la réinitialisation sélective : {str(e)}', 'danger')

    return redirect(url_for('admin.admin_reglages'))



@bp.route('/admin/generer-demo', methods=['POST'])
@admin_required
def admin_generer_demo():
    """Génère des données de démonstration dynamiques, adaptées aux catégories configurées."""
    conn = get_app_db()

    # ══════════════════════════════════════════════════════════
    #  1. LECTURE DYNAMIQUE DES CATÉGORIES EXISTANTES
    # ══════════════════════════════════════════════════════════

    # ── Catégories de personnes ──
    cats_personnes = conn.execute(
        'SELECT cle, libelle FROM categories_personnes WHERE actif = 1 ORDER BY ordre'
    ).fetchall()
    cats_personnes_cles = [c['cle'] for c in cats_personnes]

    # ── Catégories de matériel (avec préfixes) ──
    cats_materiel = conn.execute(
        'SELECT id, nom, prefixe_inventaire FROM categories_materiel ORDER BY nom'
    ).fetchall()

    if not cats_personnes_cles:
        flash("Aucune catégorie de personnes active. Créez-en au moins une avant de générer la démo.", 'warning')
        return redirect(url_for('admin.admin_dashboard'))
    if not cats_materiel:
        flash("Aucune catégorie de matériel. Créez-en au moins une avant de générer la démo.", 'warning')
        return redirect(url_for('admin.admin_dashboard'))

    # ══════════════════════════════════════════════════════════
    #  2. PERSONNES DE DÉMONSTRATION (dynamique)
    # ══════════════════════════════════════════════════════════

    # Banque de noms réutilisable
    noms_banque = [
        ('Martin', 'Lucas'), ('Dubois', 'Emma'), ('Bernard', 'Léo'),
        ('Petit', 'Chloé'), ('Robert', 'Nathan'), ('Richard', 'Jade'),
        ('Moreau', 'Hugo'), ('Simon', 'Manon'), ('Laurent', 'Thomas'),
        ('Leroy', 'Camille'), ('Roux', 'Enzo'), ('David', 'Léa'),
        ('Bertrand', 'Mathis'), ('Morel', 'Sarah'), ('Dupont', 'Marie'),
        ('Lefebvre', 'Pierre'), ('Garcia', 'Sophie'), ('Fournier', 'Jean'),
        ('Girard', 'François'), ('Bonnet', 'Catherine'), ('Mercier', 'Julie'),
        ('Boyer', 'Antoine'), ('Blanc', 'Clara'), ('Guérin', 'Maxime'),
        ('Faure', 'Alice'), ('Rousseau', 'Arthur'), ('Fontaine', 'Inès'),
        ('Chevalier', 'Raphaël'), ('Robin', 'Louise'), ('Masson', 'Gabriel'),
    ]
    classes_demo = [
        '2nde 1', '2nde 3', '2nde 5', '1ère STI2D', '1ère G1', '1ère G2',
        'Tle S1', 'Tle S2', 'Tle STMG', 'Tle L', 'BTS SIO 1', 'BTS SIO 2',
    ]

    # Données spécifiques par clé de catégorie connue (avec classe ou non)
    cats_avec_classe = {'eleve', 'etudiant', 'stagiaire', 'apprenti'}

    # Répartition : première catégorie = 50 %, les autres se partagent le reste
    nb_total_personnes = min(len(noms_banque), 24)
    nb_par_cat = {}
    if len(cats_personnes_cles) == 1:
        nb_par_cat[cats_personnes_cles[0]] = nb_total_personnes
    else:
        nb_premiere = max(4, nb_total_personnes // 2)
        reste = nb_total_personnes - nb_premiere
        nb_par_cat[cats_personnes_cles[0]] = nb_premiere
        nb_autres = max(1, len(cats_personnes_cles) - 1)
        par_autre = max(2, reste // nb_autres)
        for cle in cats_personnes_cles[1:]:
            nb_par_cat[cle] = par_autre

    idx_nom = 0
    personnes_ids = []
    for cle in cats_personnes_cles:
        nb = nb_par_cat.get(cle, 2)
        a_classe = cle in cats_avec_classe
        for i in range(nb):
            if idx_nom >= len(noms_banque):
                break
            nom, prenom = noms_banque[idx_nom]
            classe = random.choice(classes_demo) if a_classe else ''
            # Email fictif pour ~60 % des personnes (plus réaliste)
            if random.random() < 0.6:
                domaines = ['ecole.fr', 'lycee-exemple.fr', 'college-demo.net', 'etablissement.edu']
                email_base = f'{prenom.lower()}.{nom.lower()}'.replace('é', 'e').replace('è', 'e').replace('ë', 'e').replace('ê', 'e').replace('à', 'a').replace('ç', 'c').replace('ï', 'i').replace('î', 'i').replace('ô', 'o').replace('ü', 'u').replace('û', 'u')
                email = f'{email_base}@{random.choice(domaines)}'
            else:
                email = ''
            try:
                cursor = conn.execute(
                    'INSERT INTO personnes (nom, prenom, categorie, classe, email, actif) VALUES (?, ?, ?, ?, ?, 1)',
                    (nom, prenom, cle, classe, email)
                )
                personnes_ids.append(cursor.lastrowid)
            except Exception:
                personnes_ids.append(None)
            idx_nom += 1

    # Quelques personnes inactives (simulation rentrée / départs)
    personnes_inactives = [
        ('ANCIEN', 'Paul', cats_personnes_cles[0], '3A', ''),
        ('PARTIE', 'Lucie', cats_personnes_cles[0], '2nde 3', ''),
    ]
    for nom, prenom, cat, classe, email in personnes_inactives:
        try:
            conn.execute(
                'INSERT INTO personnes (nom, prenom, categorie, classe, email, actif) VALUES (?, ?, ?, ?, ?, 0)',
                (nom, prenom, cat, classe, email)
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════
    #  2b. LIEUX DE DÉMONSTRATION
    # ══════════════════════════════════════════════════════════
    lieux_demo = ['Salle 101', 'Salle 202', 'CDI', 'Atelier', 'Gymnase', 'Salle des profs']
    for lieu_nom in lieux_demo:
        try:
            conn.execute('INSERT INTO lieux (nom, actif) VALUES (?, 1)', (lieu_nom,))
        except Exception:
            pass  # déjà existant

    # ══════════════════════════════════════════════════════════
    #  3. MATÉRIELS DE DÉMONSTRATION (dynamique)
    # ══════════════════════════════════════════════════════════

    # Banque d'exemples de matériels par nom de catégorie connu
    exemples_materiels = {
        'informatique': [
            ('Dell', 'Latitude 5530', 'Windows 11'),
            ('Dell', 'Latitude 3520', 'Windows 10'),
            ('HP', 'ProBook 450 G9', 'Windows 11'),
            ('Lenovo', 'ThinkPad T14', 'Linux Ubuntu'),
            ('Apple', 'iPad 10e gen', 'iPadOS 17'),
            ('Samsung', 'Galaxy Tab S9', 'Android 14'),
        ],
        'audio/vidéo': [
            ('Epson', 'EB-992F', ''),
            ('BenQ', 'MH560', ''),
            ('Jabra', 'Evolve2 75', ''),
            ('Logitech', 'C920 HD Pro', ''),
        ],
        'réseau': [
            ('Cisco', 'Catalyst 2960', 'IOS 15'),
            ('TP-Link', 'Archer AX73', ''),
        ],
        'sport': [
            ('Decathlon', 'Chronomètre W500', ''),
            ('Garmin', 'Forerunner 55', ''),
        ],
        'livres': [
            ('Hachette', 'Manuel Maths Tle', ''),
            ('Nathan', 'Physique-Chimie 1ère', ''),
        ],
        'outils': [
            ('Fluke', 'T6-600', ''),
            ('Bosch', 'GSR 18V-28', ''),
        ],
        'fournitures': [
            ('Brother', 'HL-L2350DW', ''),
            ('Canon', 'PIXMA TS3350', ''),
        ],
    }

    # Exemples génériques pour catégories inconnues
    exemples_generiques = [
        ('Modèle A', 'Standard', ''),
        ('Modèle B', 'Premium', ''),
        ('Modèle C', 'Basique', ''),
    ]

    materiels_ids = []
    materiels_info = []  # (id, type_mat, marque, modele) pour les descriptifs de prêts

    for cat in cats_materiel:
        nom_cat = cat['nom']
        prefixe = (cat['prefixe_inventaire'] or 'INV').upper()

        # Trouver le prochain numéro d'inventaire pour ce préfixe (réutilise les gaps)
        from utils import get_next_inventory_number

        # Choisir les exemples adaptés à cette catégorie
        nom_lower = nom_cat.lower().strip()
        exemples = exemples_materiels.get(nom_lower, exemples_generiques)

        # Générer 2 à 4 matériels par catégorie (proportionnel)
        nb_items = min(len(exemples), max(2, 6 if nom_lower == 'informatique' else 3))
        items_choisis = exemples[:nb_items]

        sn_counter = 1
        for marque, modele, os_val in items_choisis:
            numero_inv = get_next_inventory_number(conn, prefixe)
            numero_serie = f'SN-{prefixe}-{sn_counter:03d}'
            try:
                cursor = conn.execute(
                    'INSERT INTO inventaire (type_materiel, marque, modele, numero_serie, '
                    'numero_inventaire, systeme_exploitation, etat, actif) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
                    (nom_cat, marque, modele, numero_serie, numero_inv, os_val, 'disponible')
                )
                mid = cursor.lastrowid
                materiels_ids.append(mid)
                materiels_info.append((mid, nom_cat, marque, modele))
            except Exception:
                materiels_ids.append(None)
            sn_counter += 1

    # ══════════════════════════════════════════════════════════
    #  4. PRÊTS DE DÉMONSTRATION (dynamique)
    # ══════════════════════════════════════════════════════════

    valid_personnes = [pid for pid in personnes_ids if pid is not None]
    valid_materiels = [(mid, t, m, mo) for mid, t, m, mo in materiels_info if mid is not None]

    now = datetime.now()
    notes_prets = [
        'Projet SNT', 'TP en salle', 'Stage en entreprise', 'Présentation orale',
        'Journée portes ouvertes', 'Formation à distance', 'Examens',
        'Cours de rattrapage', 'Atelier pratique', 'Semaine de projet',
        'TP réseau', 'Sortie scolaire', 'Intervention extérieure', '',
    ]

    # Pré-charger les classes des personnes pour les snapshots
    classes_personnes = {}
    for pid in valid_personnes:
        row = conn.execute('SELECT classe FROM personnes WHERE id = ?', (pid,)).fetchone()
        classes_personnes[pid] = row['classe'] if row else ''

    if valid_personnes and valid_materiels:
        # ── Lieux existants pour affectation aléatoire ──
        lieux_ids = [row['id'] for row in conn.execute(
            'SELECT id FROM lieux WHERE actif = 1'
        ).fetchall()]

        def random_lieu():
            """Retourne un lieu_id aléatoire ou None (30 % sans lieu)."""
            if lieux_ids and random.random() < 0.7:
                return random.choice(lieux_ids)
            return None

        # Prêts en cours (environ 30 % des matériels)
        nb_prets_en_cours = max(2, len(valid_materiels) // 3)
        indices_mat = list(range(len(valid_materiels)))
        random.shuffle(indices_mat)

        for i in range(min(nb_prets_en_cours, len(indices_mat), len(valid_personnes))):
            mid, type_mat, marque, modele = valid_materiels[indices_mat[i]]
            pid = valid_personnes[i % len(valid_personnes)]
            descriptif = f'{marque} {modele}'
            jours_ago = random.randint(0, 10)
            duree = random.choice([1, 3, 5, 7, 14])
            note = random.choice(notes_prets)
            date_emprunt_dt = now - timedelta(days=jours_ago)
            date_emprunt = date_emprunt_dt.strftime('%Y-%m-%d %H:%M:%S')
            date_retour_prevue = (date_emprunt_dt + timedelta(days=duree)).strftime('%Y-%m-%d 23:59:00')
            classe_snap = classes_personnes.get(pid, '')
            annee_scol = calculer_annee_scolaire(date_emprunt_dt)
            lieu = random_lieu()

            cursor = conn.execute(
                'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                'retour_confirme, duree_pret_jours, materiel_id, notes, lieu_id, '
                'classe_snapshot, annee_scolaire, date_retour_prevue, type_duree) '
                'VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)',
                (pid, descriptif, date_emprunt, duree, mid, note, lieu,
                 classe_snap, annee_scol, date_retour_prevue, 'jours')
            )
            pret_id = cursor.lastrowid
            # Créer l'entrée pret_materiels correspondante
            conn.execute(
                'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                (pret_id, mid, descriptif)
            )
            conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (mid,))

        # Prêts retournés (historique, sur les matériels restants)
        indices_retournes = indices_mat[nb_prets_en_cours:]
        nb_prets_retournes = min(len(indices_retournes), max(3, len(valid_materiels) // 2))

        for i in range(nb_prets_retournes):
            mid, type_mat, marque, modele = valid_materiels[indices_retournes[i]]
            pid = valid_personnes[(nb_prets_en_cours + i) % len(valid_personnes)]
            descriptif = f'{marque} {modele}'
            jours_ago = random.randint(10, 60)
            duree = random.choice([3, 7, 14, 30])
            retour_jours_ago = max(1, jours_ago - random.randint(1, duree))
            note = random.choice(notes_prets)
            date_emprunt_dt = now - timedelta(days=jours_ago)
            date_emprunt = date_emprunt_dt.strftime('%Y-%m-%d %H:%M:%S')
            date_retour = (now - timedelta(days=retour_jours_ago)).strftime('%Y-%m-%d %H:%M:%S')
            date_retour_prevue = (date_emprunt_dt + timedelta(days=duree)).strftime('%Y-%m-%d 23:59:00')
            classe_snap = classes_personnes.get(pid, '')
            annee_scol = calculer_annee_scolaire(date_emprunt_dt)
            lieu = random_lieu()

            cursor = conn.execute(
                'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                'date_retour, retour_confirme, duree_pret_jours, materiel_id, notes, lieu_id, '
                'classe_snapshot, annee_scolaire, date_retour_prevue, type_duree) '
                'VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)',
                (pid, descriptif, date_emprunt, date_retour, duree, mid, note, lieu,
                 classe_snap, annee_scol, date_retour_prevue, 'jours')
            )
            pret_id = cursor.lastrowid
            conn.execute(
                'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                (pret_id, mid, descriptif)
            )

        # ── Historique enrichi : plusieurs prêts passés sur certains équipements ──
        indices_historique = random.sample(
            list(range(len(valid_materiels))),
            k=min(max(3, len(valid_materiels) * 2 // 5), len(valid_materiels))
        )
        for idx in indices_historique:
            mid, type_mat, marque, modele = valid_materiels[idx]
            descriptif = f'{marque} {modele}'
            nb_anciens = random.randint(2, 4)
            base_jours = 60

            for j in range(nb_anciens):
                pid = random.choice(valid_personnes)
                jours_debut = base_jours + random.randint(5, 30)
                duree = random.choice([1, 3, 5, 7, 14])
                jours_retour = max(base_jours, jours_debut - duree)
                note = random.choice(notes_prets)
                date_emprunt_dt = now - timedelta(days=jours_debut)
                date_emprunt = date_emprunt_dt.strftime('%Y-%m-%d %H:%M:%S')
                date_retour = (now - timedelta(days=jours_retour)).strftime('%Y-%m-%d %H:%M:%S')
                date_retour_prevue = (date_emprunt_dt + timedelta(days=duree)).strftime('%Y-%m-%d 23:59:00')
                classe_snap = classes_personnes.get(pid, '')
                annee_scol = calculer_annee_scolaire(date_emprunt_dt)
                lieu = random_lieu()

                cursor = conn.execute(
                    'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                    'date_retour, retour_confirme, duree_pret_jours, materiel_id, notes, lieu_id, '
                    'classe_snapshot, annee_scolaire, date_retour_prevue, type_duree) '
                    'VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (pid, descriptif, date_emprunt, date_retour, duree, mid, note, lieu,
                     classe_snap, annee_scol, date_retour_prevue, 'jours')
                )
                pret_id = cursor.lastrowid
                conn.execute(
                    'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                    (pret_id, mid, descriptif)
                )
                base_jours = jours_debut + random.randint(5, 20)

        # ── Quelques prêts multi-matériels pour la démo ──
        if len(valid_materiels) >= 3 and len(valid_personnes) >= 2:
            accessoires = ['Câble HDMI', 'Souris sans fil', 'Chargeur', 'Sacoche', 'Adaptateur USB-C', 'Rallonge']
            for _ in range(min(3, len(valid_personnes) // 4 + 1)):
                pid = random.choice(valid_personnes)
                nb_items = random.randint(2, 4)
                items_desc = random.sample(accessoires, min(nb_items - 1, len(accessoires)))
                # Premier item = un matériel de l'inventaire (libre)
                libre = conn.execute(
                    "SELECT id, marque, modele FROM inventaire WHERE etat = 'disponible' AND actif = 1 LIMIT 1"
                ).fetchone()
                if libre:
                    desc_principal = f"{libre['marque']} {libre['modele']}"
                    all_desc = [desc_principal] + items_desc
                    descriptif_combine = ' + '.join(all_desc)
                    jours_ago_multi = random.randint(0, 5)
                    date_emprunt_dt = now - timedelta(days=jours_ago_multi)
                    date_emprunt = date_emprunt_dt.strftime('%Y-%m-%d %H:%M:%S')
                    duree_multi = random.choice([3, 7, 14])
                    date_retour_prevue = (date_emprunt_dt + timedelta(days=duree_multi)).strftime('%Y-%m-%d 23:59:00')
                    classe_snap = classes_personnes.get(pid, '')
                    annee_scol = calculer_annee_scolaire(date_emprunt_dt)
                    lieu = random_lieu()

                    cursor = conn.execute(
                        'INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, '
                        'retour_confirme, duree_pret_jours, notes, lieu_id, '
                        'classe_snapshot, annee_scolaire, date_retour_prevue, type_duree) '
                        'VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)',
                        (pid, descriptif_combine, date_emprunt, duree_multi,
                         random.choice(notes_prets), lieu,
                         classe_snap, annee_scol, date_retour_prevue, 'jours')
                    )
                    pret_id = cursor.lastrowid
                    # Premier item lié à l'inventaire
                    conn.execute(
                        'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                        (pret_id, libre['id'], desc_principal)
                    )
                    conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (libre['id'],))
                    # Items supplémentaires (texte libre)
                    for desc in items_desc:
                        conn.execute(
                            'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, NULL, ?)',
                            (pret_id, desc)
                        )

    conn.commit()

    nb_pers = len([p for p in personnes_ids if p is not None])
    nb_mat = len([m for m in materiels_ids if m is not None])
    nb_lieux = conn.execute('SELECT COUNT(*) FROM lieux WHERE actif = 1').fetchone()[0]
    _audit.info('DEMO_GENERATED depuis %s', request.remote_addr)
    flash(f'Base de démonstration générée : {nb_pers} personnes, {nb_mat} matériels, '
          f'{nb_lieux} lieux et des prêts de test.', 'success')
    return redirect(url_for('admin.admin_dashboard'))



@bp.route('/admin/sauvegarder')
@admin_required
def admin_sauvegarder():
    """Exporter toute la base + uploads dans un fichier .pretgo (zip)."""
    success, message, zip_path = effectuer_backup()
    if not success or not zip_path:
        flash(f'Erreur lors de la sauvegarde : {message}', 'danger')
        return redirect(url_for('admin.admin_reglages'))
    filename = os.path.basename(zip_path)
    return send_file(zip_path, as_attachment=True, download_name=filename,
                     mimetype='application/zip')



@bp.route('/admin/restaurer', methods=['POST'])
@admin_required
def admin_restaurer():
    """Restaurer depuis un fichier .pretgo."""
    if 'fichier_pretgo' not in request.files:
        flash('Aucun fichier sélectionné.', 'danger')
        return redirect(url_for('admin.admin_reglages'))

    fichier = request.files['fichier_pretgo']
    if not fichier.filename.lower().endswith('.pretgo'):
        flash('Veuillez sélectionner un fichier .pretgo valide.', 'danger')
        return redirect(url_for('admin.admin_reglages'))

    try:
        # Sauvegarder dans un temp
        temp_path = os.path.join(BACKUP_DIR, 'restauration_temp.zip')
        fichier.save(temp_path)

        with zipfile.ZipFile(temp_path, 'r') as zf:
            names = zf.namelist()
            if 'gestion_prets.db' not in names:
                flash('Fichier .pretgo invalide (base de données manquante).', 'danger')
                os.remove(temp_path)
                return redirect(url_for('admin.admin_reglages'))

            # Restaurer la base
            zf.extract('gestion_prets.db', DATA_DIR)

            # Restaurer les images
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            for name in names:
                if name.startswith('uploads/materiel/'):
                    fname = name.split('/')[-1]
                    if fname:
                        with zf.open(name) as src, open(os.path.join(UPLOAD_FOLDER, fname), 'wb') as dst:
                            dst.write(src.read())
                elif name.startswith('documents/'):
                    fname = name.split('/')[-1]
                    if fname:
                        with zf.open(name) as src, open(os.path.join(DOCUMENTS_DIR, fname), 'wb') as dst:
                            dst.write(src.read())
                elif name == 'code_recuperation.txt':
                    zf.extract(name, DATA_DIR)

        os.remove(temp_path)
        # Réinitialiser les migrations (s'assurer que les nouvelles colonnes existent)
        init_db()
        _audit.info('RESTORE_DB depuis %s', request.remote_addr)
        session.pop('admin_logged_in', None)
        flash('Base restaurée avec succès ! Veuillez vous reconnecter.', 'success')
        return redirect(url_for('admin.admin_login'))

    except Exception as e:
        flash(f'Erreur lors de la restauration : {str(e)}', 'danger')
        return redirect(url_for('admin.admin_reglages'))



@bp.route('/statistiques')
@admin_required
def statistiques():
    """Tableau de bord statistique avec graphiques et export."""
    conn = get_app_db()

    # ── Stats générales ──
    total_prets = conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0]
    prets_actifs = conn.execute('SELECT COUNT(*) FROM prets WHERE retour_confirme = 0').fetchone()[0]
    prets_retournes = conn.execute('SELECT COUNT(*) FROM prets WHERE retour_confirme = 1').fetchone()[0]
    total_personnes = conn.execute('SELECT COUNT(*) FROM personnes WHERE actif = 1').fetchone()[0]
    total_materiel = conn.execute('SELECT COUNT(*) FROM inventaire WHERE actif = 1').fetchone()[0]

    # ── Prêts par mois (12 derniers mois) ──
    prets_par_mois = conn.execute('''
        SELECT strftime('%Y-%m', date_emprunt) AS mois, COUNT(*) AS nb
        FROM prets
        WHERE date_emprunt >= date('now', '-12 months')
        GROUP BY mois
        ORDER BY mois
    ''').fetchall()

    # ── Top 10 matériels les plus empruntés ──
    top_materiels = conn.execute('''
        SELECT pm.description, COUNT(*) AS nb
        FROM pret_materiels pm
        JOIN prets p ON pm.pret_id = p.id
        GROUP BY pm.description
        ORDER BY nb DESC
        LIMIT 10
    ''').fetchall()

    # ── Répartition par catégorie de personne ──
    prets_par_categorie = conn.execute('''
        SELECT pe.categorie, COUNT(*) AS nb
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        GROUP BY pe.categorie
        ORDER BY nb DESC
    ''').fetchall()

    # ── Top 10 emprunteurs ──
    top_emprunteurs = conn.execute('''
        SELECT pe.nom, pe.prenom, pe.categorie, COUNT(*) AS nb
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        GROUP BY p.personne_id
        ORDER BY nb DESC
        LIMIT 10
    ''').fetchall()

    # ── Durée moyenne des prêts (en heures) ──
    duree_moyenne = conn.execute('''
        SELECT AVG(
            CAST((julianday(date_retour) - julianday(date_emprunt)) * 24 AS REAL)
        ) AS duree_moy_h
        FROM prets
        WHERE retour_confirme = 1 AND date_retour IS NOT NULL
    ''').fetchone()
    duree_moy_heures = round(duree_moyenne['duree_moy_h'] or 0, 1)

    # ── Taux de retour à l'heure ──
    duree_def = float(get_setting('duree_alerte_defaut', '7'))
    unite_def = get_setting('duree_alerte_unite', 'jours')
    heure_fin = get_setting('heure_fin_journee', '17:45')
    prets_retournes_list = conn.execute('''
        SELECT date_emprunt, date_retour, duree_pret_heures, duree_pret_jours, date_retour_prevue
        FROM prets WHERE retour_confirme = 1 AND date_retour IS NOT NULL
    ''').fetchall()
    retours_a_lheure = 0
    for p in prets_retournes_list:
        depasse, _ = calcul_depassement_heures(
            p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
            _duree_defaut=duree_def, _unite_defaut=unite_def,
            date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
        )
        if not depasse:
            retours_a_lheure += 1
    taux_ponctualite = round((retours_a_lheure / len(prets_retournes_list) * 100) if prets_retournes_list else 0, 1)

    # ── État du parc matériel ──
    etats_materiel = conn.execute('''
        SELECT etat, COUNT(*) AS nb FROM inventaire WHERE actif = 1 GROUP BY etat
    ''').fetchall()

    # ── Prêts par jour de la semaine ──
    prets_par_jour = conn.execute('''
        SELECT CAST(strftime('%w', date_emprunt) AS INTEGER) AS jour, COUNT(*) AS nb
        FROM prets
        GROUP BY jour
        ORDER BY jour
    ''').fetchall()


    # Préparer les données JSON pour Chart.js
    jours_semaine = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi']

    return render_template('statistiques.html',
                           total_prets=total_prets,
                           prets_actifs=prets_actifs,
                           prets_retournes=prets_retournes,
                           total_personnes=total_personnes,
                           total_materiel=total_materiel,
                           prets_par_mois=[dict(r) for r in prets_par_mois],
                           top_materiels=[dict(r) for r in top_materiels],
                           prets_par_categorie=[dict(r) for r in prets_par_categorie],
                           top_emprunteurs=[dict(r) for r in top_emprunteurs],
                           duree_moy_heures=duree_moy_heures,
                           taux_ponctualite=taux_ponctualite,
                           etats_materiel=[dict(r) for r in etats_materiel],
                           prets_par_jour=[dict(r) for r in prets_par_jour],
                           jours_semaine=jours_semaine)



@bp.route('/statistiques/export')
@admin_required
def export_statistiques():
    """Export CSV des données de prêts."""
    conn = get_app_db()
    prets = conn.execute('''
        SELECT p.id, pe.nom, pe.prenom, pe.categorie, pe.classe,
               p.descriptif_objets, p.date_emprunt, p.date_retour,
               p.retour_confirme, p.notes, p.type_duree,
               p.duree_pret_jours, p.duree_pret_heures,
               l.nom AS lieu
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        LEFT JOIN lieux l ON p.lieu_id = l.id
        ORDER BY p.date_emprunt DESC
    ''').fetchall()

    # Récupérer la durée d'alerte par défaut pour l'afficher dans le CSV
    duree_defaut = get_setting('duree_alerte_defaut', '7')
    unite_defaut = get_setting('duree_alerte_unite', 'jours')

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID', 'Nom', 'Prénom', 'Catégorie', 'Classe',
                     'Objets', 'Date emprunt', 'Date retour',
                     'Retourné', 'Notes', 'Type durée', 'Jours', 'Heures', 'Lieu'])
    for p in prets:
        # Afficher la durée réelle au lieu de "défaut"
        type_duree = p['type_duree']
        if not type_duree or type_duree == 'aucune':
            type_duree = f'Défaut ({duree_defaut} {unite_defaut})'
        elif type_duree == 'fin_journee':
            heure_fin = get_setting('heure_fin_journee', '17:45')
            type_duree = f'Fin de journée ({heure_fin})'
        elif type_duree == 'heures':
            type_duree = f'{p["duree_pret_heures"] or ""} heure(s)'
        elif type_duree == 'jours':
            type_duree = f'{p["duree_pret_jours"] or ""} jour(s)'

        writer.writerow([
            p['id'], p['nom'], p['prenom'], p['categorie'], p['classe'],
            p['descriptif_objets'], p['date_emprunt'], p['date_retour'] or '',
            'Oui' if p['retour_confirme'] else 'Non', p['notes'],
            type_duree, p['duree_pret_jours'] or '', p['duree_pret_heures'] or '',
            p['lieu'] or ''
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=pretgo_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )



@bp.route('/admin/champs-personnalises')
@admin_required
def champs_personnalises():
    """Gestion des champs personnalisés pour personnes et matériel."""
    conn = get_app_db()
    champs_personnes = conn.execute(
        'SELECT * FROM champs_personnalises WHERE entite = ? ORDER BY ordre, id',
        ('personne',)
    ).fetchall()
    champs_materiel = conn.execute(
        'SELECT * FROM champs_personnalises WHERE entite = ? ORDER BY ordre, id',
        ('materiel',)
    ).fetchall()
    return render_template('champs_personnalises.html',
                           champs_personnes=champs_personnes,
                           champs_materiel=champs_materiel)



@bp.route('/admin/champs-personnalises/ajouter', methods=['POST'])
@admin_required
def ajouter_champ_personnalise():
    """Ajouter un champ personnalisé."""
    entite = request.form.get('entite', 'personne')
    label = request.form.get('label', '').strip()
    type_champ = request.form.get('type_champ', 'texte')
    options = request.form.get('options', '').strip()
    obligatoire = 1 if request.form.get('obligatoire') else 0

    if not label:
        flash('Le libellé du champ est obligatoire.', 'danger')
        return redirect(url_for('admin.champs_personnalises'))

    # Générer un nom de champ normalisé
    nom_champ = re.sub(r'[^a-z0-9_]', '_', label.lower().strip())
    nom_champ = re.sub(r'_+', '_', nom_champ).strip('_')

    # Normaliser les accents
    nom_champ = unicodedata.normalize('NFKD', nom_champ).encode('ascii', 'ignore').decode('ascii')

    conn = get_app_db()
    # Trouver le prochain ordre
    max_ordre = conn.execute(
        'SELECT MAX(ordre) FROM champs_personnalises WHERE entite = ?', (entite,)
    ).fetchone()[0] or 0

    conn.execute(
        '''INSERT INTO champs_personnalises (entite, nom_champ, label, type_champ, options, obligatoire, ordre)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (entite, nom_champ, label, type_champ, options, obligatoire, max_ordre + 1)
    )
    conn.commit()

    type_label = 'personne' if entite == 'personne' else 'matériel'
    flash(f'Champ « {label} » ajouté pour les fiches {type_label}.', 'success')
    return redirect(url_for('admin.champs_personnalises'))



@bp.route('/admin/champs-personnalises/supprimer/<int:champ_id>', methods=['POST'])
@admin_required
def supprimer_champ_personnalise(champ_id):
    """Supprimer un champ personnalisé et ses valeurs."""
    conn = get_app_db()
    champ = conn.execute('SELECT label, entite FROM champs_personnalises WHERE id = ?', (champ_id,)).fetchone()
    if champ:
        conn.execute('DELETE FROM valeurs_champs_personnalises WHERE champ_id = ?', (champ_id,))
        conn.execute('DELETE FROM champs_personnalises WHERE id = ?', (champ_id,))
        conn.commit()
        flash(f'Champ « {champ["label"]} » supprimé.', 'success')
    return redirect(url_for('admin.champs_personnalises'))



@bp.route('/admin/champs-personnalises/modifier/<int:champ_id>', methods=['POST'])
@admin_required
def modifier_champ_personnalise(champ_id):
    """Modifier un champ personnalisé."""
    label = request.form.get('label', '').strip()
    type_champ = request.form.get('type_champ', 'texte')
    options = request.form.get('options', '').strip()
    obligatoire = 1 if request.form.get('obligatoire') else 0

    if not label:
        flash('Le libellé est obligatoire.', 'danger')
        return redirect(url_for('admin.champs_personnalises'))

    conn = get_app_db()
    conn.execute(
        '''UPDATE champs_personnalises
           SET label = ?, type_champ = ?, options = ?, obligatoire = ?
           WHERE id = ?''',
        (label, type_champ, options, obligatoire, champ_id)
    )
    conn.commit()
    flash(f'Champ « {label} » modifié.', 'success')
    return redirect(url_for('admin.champs_personnalises'))



@bp.route('/admin/champs-personnalises/ordre', methods=['POST'])
@admin_required
def reordonner_champs():
    """Réordonner les champs via AJAX."""
    data = request.get_json()
    if not data or 'ordre' not in data:
        return jsonify({'error': 'Données manquantes'}), 400

    conn = get_app_db()
    for i, champ_id in enumerate(data['ordre']):
        conn.execute('UPDATE champs_personnalises SET ordre = ? WHERE id = ?', (i, champ_id))
    conn.commit()
    return jsonify({'success': True})


