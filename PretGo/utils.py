"""
PretGo — Fonctions utilitaires partagées entre les blueprints.
"""

from flask import g, redirect, url_for, flash, session, request, Response
from database import get_db, get_setting, set_setting, DATABASE_PATH, BACKUP_DIR, DOCUMENTS_DIR, RECOVERY_CODE_PATH
from datetime import datetime, timedelta
from functools import wraps
from email.message import EmailMessage
from html import escape as html_escape
import base64
import logging
import os
import secrets
import shutil
import smtplib
import ssl
import threading
import time as _time
import zipfile
from collections import defaultdict as _defaultdict

_log = logging.getLogger(__name__)

# ============================================================
#  CONSTANTES
# ============================================================

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'materiel')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
EMAIL_SIGNATURE_MAX_BYTES = 15 * 1024


# ============================================================
#  FONCTIONS UTILITAIRES
# ============================================================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def calculer_annee_scolaire(d=None):
    """Calcule l'année scolaire pour une date donnée.
    Septembre–Août = même année scolaire.
    Ex: 15/10/2025 → '2025-2026', 15/03/2026 → '2025-2026'
    """
    if d is None:
        d = datetime.now()
    elif isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], '%Y-%m-%d')
        except (ValueError, TypeError):
            d = datetime.now()
    if d.month >= 9:  # septembre–décembre
        return f'{d.year}-{d.year + 1}'
    else:  # janvier–août
        return f'{d.year - 1}-{d.year}'


# ============================================================
#  RATE LIMITER (anti brute-force, zéro dépendance)
# ============================================================

class _RateLimiter:
    """Limiteur de requêtes en mémoire, par IP."""
    def __init__(self):
        self._hits = _defaultdict(list)   # ip -> [timestamps]
        self._last_cleanup = _time.time()

    def is_limited(self, ip, max_hits=5, window=60):
        """True si l'IP a dépassé max_hits dans les <window> dernières secondes."""
        now = _time.time()
        hits = self._hits[ip]
        # Purger les entrées trop anciennes
        self._hits[ip] = [t for t in hits if now - t < window]
        if len(self._hits[ip]) >= max_hits:
            return True
        self._hits[ip].append(now)
        # Nettoyage global toutes les 10 minutes : supprimer les IPs inactives
        if now - self._last_cleanup > 600:
            self._last_cleanup = now
            stale = [k for k, v in self._hits.items() if not v or now - v[-1] > window]
            for k in stale:
                del self._hits[k]
        return False

rate_limiter = _RateLimiter()


# ============================================================
#  CONNEXION DB PARTAGÉE (via g)
# ============================================================

def get_app_db():
    """Obtenir la connexion DB partagée pour la requête courante (via g)."""
    if '_db' not in g:
        g._db = get_db()
    return g._db


# ============================================================
#  GÉNÉRATION DE NUMÉRO D'INVENTAIRE
# ============================================================

def get_next_inventory_number(conn, prefix):
    """
    Récupère le prochain numéro d'inventaire disponible pour un préfixe.
    Réutilise les numéros "libérés" (les plus bas manquants ou inactifs).
    Par ex: si PC-00001 et PC-00003 existent (actifs), retourne PC-00002.
    Si 1,2,3 existent, retourne 4.
    
    Args:
        conn: Connexion de base de données
        prefix: Préfixe (ex: 'PC', 'INV')
    Returns:
        Numéro formaté (ex: 'PC-00002')
    """
    prefix = prefix.upper()
    
    # Récupérer tous les numéros existants du préfixe (actifs ET inactifs)
    # On ne réutilise JAMAIS un numéro inactif pour éviter conflits d'historique
    rows = conn.execute(
        "SELECT numero_inventaire FROM inventaire "
        "WHERE numero_inventaire LIKE ? "
        "ORDER BY CAST(SUBSTR(numero_inventaire, ?, 5) AS INTEGER) ASC",
        (f'{prefix}-%', len(prefix) + 2)
    ).fetchall()
    
    if not rows:
        # Aucun numéro existant, commencer à 1
        return f'{prefix}-00001'
    
    # Extraire les numéros et trouver le premier gap
    used_numbers = set()
    for row in rows:
        try:
            num_str = row['numero_inventaire'].split('-', 1)[1]
            num = int(num_str)
            used_numbers.add(num)
        except (IndexError, ValueError):
            pass
    
    # Trouver le plus petit numéro manquant (ou suivant)
    next_num = 1
    while next_num in used_numbers:
        next_num += 1
    
    return f'{prefix}-{next_num:05d}'


# ============================================================
#  LIBÉRATION DES MATÉRIELS D'UN PRÊT
# ============================================================

def liberer_materiels_pret(conn, pret_id, pret_row=None):
    """Libère tous les matériels liés à un prêt (multi-matériel + rétrocompat legacy).

    Args:
        conn: connexion DB active
        pret_id: ID du prêt
        pret_row: (optionnel) row du prêt déjà chargée (évite un SELECT supplémentaire)
    """
    # Multi-matériel (table pret_materiels)
    mats = conn.execute(
        'SELECT materiel_id FROM pret_materiels WHERE pret_id = ? AND materiel_id IS NOT NULL',
        (pret_id,)
    ).fetchall()
    for m in mats:
        conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (m['materiel_id'],))
    # Rétrocompat : ancien champ materiel_id sur la table prets
    if pret_row is None:
        pret_row = conn.execute('SELECT materiel_id FROM prets WHERE id = ?', (pret_id,)).fetchone()
    if pret_row and pret_row['materiel_id']:
        conn.execute("UPDATE inventaire SET etat = 'disponible' WHERE id = ?", (pret_row['materiel_id'],))


# ============================================================
#  CATÉGORIES DE PERSONNES
# ============================================================

def get_categories_personnes():
    """Récupère les catégories de personnes depuis la base."""
    conn = get_app_db()
    cats = conn.execute(
        'SELECT * FROM categories_personnes WHERE actif = 1 ORDER BY ordre, libelle'
    ).fetchall()
    return cats


# ============================================================
#  CALCUL DE DÉPASSEMENT
# ============================================================

def calcul_depassement_heures(date_emprunt_str, duree_heures, duree_jours,
                              _duree_defaut=None, _unite_defaut=None,
                              date_retour_prevue=None, _heure_fin=None):
    """Calcule le dépassement en heures. Retourne (est_depasse, heures_depassement).

    Les paramètres optionnels _duree_defaut et _unite_defaut permettent d'éviter
    des appels répétés à get_setting() quand la fonction est appelée en boucle.
    date_retour_prevue : date précise au format 'YYYY-MM-DD'.
    _heure_fin : heure de fin de journée (ex: '17:45'), pour éviter des appels répétés.
    """
    try:
        dt = datetime.strptime(date_emprunt_str, '%Y-%m-%d %H:%M:%S')
        now = datetime.now()

        # Date de retour précise : dépassement à l'heure de fin de journée
        if date_retour_prevue:
            try:
                heure_fin = _heure_fin or get_setting('heure_fin_journee', '17:45')
                h_fin, m_fin = (int(x) for x in heure_fin.split(':'))
                retour_theorique = datetime.strptime(date_retour_prevue, '%Y-%m-%d').replace(
                    hour=h_fin, minute=m_fin, second=0)
            except Exception:
                retour_theorique = None
            if retour_theorique:
                if now > retour_theorique:
                    delta = now - retour_theorique
                    return True, delta.total_seconds() / 3600
                return False, 0

        duree_defaut = _duree_defaut if _duree_defaut is not None else float(get_setting('duree_alerte_defaut', '7'))
        unite_defaut = _unite_defaut if _unite_defaut is not None else get_setting('duree_alerte_unite', 'jours')

        if duree_heures is not None:
            retour_theorique = dt + timedelta(hours=duree_heures)
        elif duree_jours is not None:
            retour_theorique = dt + timedelta(days=duree_jours)
        else:
            if unite_defaut == 'heures':
                retour_theorique = dt + timedelta(hours=duree_defaut)
            else:
                retour_theorique = dt + timedelta(days=duree_defaut)

        if now > retour_theorique:
            delta = now - retour_theorique
            total_h = delta.total_seconds() / 3600
            return True, total_h
        return False, 0
    except Exception:
        return False, 0


def _format_depassement_texte(heures_dep):
    """Retourne un libellé lisible du dépassement."""
    if heures_dep is None:
        return ''
    if heures_dep < 24:
        h = int(heures_dep)
        m = int((heures_dep % 1) * 60)
        return f'{h}h{m:02d}'
    jours = heures_dep / 24
    return f'{int(jours)} jour(s)'


def valider_email(email):
    """Valide un format d'email basique (regex simple).
    
    Retourne True si le format semble valide, False sinon.
    """
    import re
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    # Regex simple : non-stricte mais pratique
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def obtenir_statistiques_rappels_email(conn):
    """Retourne les statistiques d'envoi des rappels email.
    
    Retourne un dict {total, sent, failed, ignored, last_send_at}
    """
    stats = {
        'total': 0,
        'sent': 0,
        'failed': 0,
        'ignored': 0,
        'last_send_at': None,
    }
    
    # Total envois/échecs
    row = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
        FROM rappels_email_log
    ''').fetchone()
    
    if row:
        stats['total'] = row['total'] or 0
        stats['sent'] = row['sent'] or 0
        stats['failed'] = row['failed'] or 0
        stats['ignored'] = 0  # Pas tracké dans la table pour l'instant
    
    # Dernier envoi
    row = conn.execute('''
        SELECT sent_at FROM rappels_email_log
        WHERE status = 'sent'
        ORDER BY sent_at DESC LIMIT 1
    ''').fetchone()
    if row and row['sent_at']:
        stats['last_send_at'] = row['sent_at']
    
    return stats


def compter_tentatives_pret(conn, pret_id):
    """Compte les tentatives d'envoi pour un prêt donné.
    
    Retourne (tentative_numero, total_tentatives)
    - tentative_numero : 1-indexed (1, 2, 3...)
    - total_tentatives : total des envois (sent + failed)
    """
    row = conn.execute('''
        SELECT COUNT(*) as total FROM rappels_email_log
        WHERE pret_id = ?
    ''', (pret_id,)).fetchone()
    total = row['total'] or 0
    tentative_numero = total + 1
    return tentative_numero, total


def verifier_max_tentatives_atteint(conn, pret_id, max_tentatives=3):
    """Vérifie si le nombre max de tentatives est atteint pour un prêt.
    
    Retourne True si le nombre total de tentatives >= max_tentatives
    """
    row = conn.execute('''
        SELECT COUNT(*) as total FROM rappels_email_log
        WHERE pret_id = ?
    ''', (pret_id,)).fetchone()
    total = row['total'] or 0
    return total >= max_tentatives


def _render_email_template(template, **variables):
    """Remplace les variables {nom}, {prenom}, etc. dans le template.
    
    Variables disponibles:
    - nom, prenom, objets, date_emprunt, depassement
    """
    result = template
    for key, value in variables.items():
        result = result.replace('{' + key + '}', str(value or ''))
    return result


def _render_html_email(text_body, signature_cid=None, signature_src=None):
    """Construit un HTML simple, robuste et compatible à partir du texte."""
    safe_body = html_escape(text_body or '').replace('\n', '<br>')
    signature_html = ''
    if signature_src:
        signature_html = (
            '<div style="margin-top:12px;">'
            f'<img src="{signature_src}" alt="Signature" style="max-width:280px;height:auto;opacity:0.92;">'
            '</div>'
        )
    elif signature_cid:
        signature_html = (
            '<div style="margin-top:12px;">'
            f'<img src="cid:{signature_cid}" alt="Signature" style="max-width:280px;height:auto;opacity:0.92;">'
            '</div>'
        )
    return (
        '<!DOCTYPE html><html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.5;">'
        f'<div>{safe_body}</div>'
        f'{signature_html}'
        '</body></html>'
    )


def _get_signature_image_info(conn):
    """Retourne (bytes, subtype, cid) de la signature si valide, sinon (None, None, None)."""
    rel_path = (get_setting('rappel_email_signature_image', '', conn=conn) or '').strip()
    if not rel_path:
        return None, None, None

    data_dir = os.path.dirname(DATABASE_PATH)
    abs_path = os.path.normpath(os.path.join(data_dir, rel_path))
    if not abs_path.startswith(os.path.normpath(data_dir)):
        return None, None, None
    if not os.path.exists(abs_path):
        return None, None, None

    try:
        with open(abs_path, 'rb') as f:
            raw = f.read()
    except Exception:
        return None, None, None

    if not raw or len(raw) > EMAIL_SIGNATURE_MAX_BYTES:
        return None, None, None

    ext = os.path.splitext(abs_path)[1].lower()
    subtype = {
        '.png': 'png',
        '.jpg': 'jpeg',
        '.jpeg': 'jpeg',
        '.webp': 'webp',
    }.get(ext)
    if not subtype:
        return None, None, None

    return raw, subtype, 'pretgo-signature'


def _label_reminder_kind(reminder_kind):
    return 'Retour prévu sous 24h' if reminder_kind == 'upcoming_24h' else 'Prêt en retard'


def _build_reference_report(conn, sent_details, now_dt, title_prefix='Rapport envoi rappels'):
    total = len(sent_details)
    total_retards = sum(1 for item in sent_details if item.get('reminder_kind') == 'overdue')
    total_24h = sum(1 for item in sent_details if item.get('reminder_kind') == 'upcoming_24h')

    subject = f"[PretGo] {title_prefix} - {now_dt.strftime('%Y-%m-%d %H:%M')}"
    lines = [
        f"{title_prefix} PretGo",
        "",
        f"Total listés: {total}",
        f"Retards: {total_retards}",
        f"Retours <24h: {total_24h}",
        "",
        "Liste des emails:",
    ]

    if not sent_details:
        lines.append("(Aucun élément)")
    else:
        for idx, item in enumerate(sent_details, start=1):
            lines.append(
                f"{idx}. {item.get('email', '')} | {_label_reminder_kind(item.get('reminder_kind'))} | prêt #{item.get('pret_id', '?')}"
            )

    template_overdue = get_setting('rappel_email_template', '', conn=conn) or ''
    template_24h = get_setting('rappel_email_template_retour_24h', '', conn=conn) or ''

    if not template_overdue:
        template_overdue = (
            "Bonjour {nom} {prenom},\n\n"
            "Ceci est un rappel de restitution de matériel PretGo.\n\n"
            "Objet(s): {objets}\n"
            "Date d'emprunt: {date_emprunt}\n"
            "Dépassement: {depassement}\n"
            "Tentative: {tentative_numero}/{tentative_total}\n\n"
            "Merci de procéder au retour du matériel dès que possible.\n\n"
            "Message automatique PretGo."
        )

    if not template_24h:
        template_24h = (
            "Bonjour {nom} {prenom},\n\n"
            "Votre prêt PretGo arrive bientôt à échéance.\n\n"
            "Objet(s): {objets}\n"
            "Date d'emprunt: {date_emprunt}\n"
            "Retour prévu: {depassement}\n"
            "Tentative: {tentative_numero}/{tentative_total}\n\n"
            "Merci d'anticiper le retour dans les délais prévus.\n\n"
            "Message automatique PretGo."
        )

    preview_vars = {
        'nom': 'Dupont',
        'prenom': 'Jean',
        'objets': 'Scie à métaux + Équerre de précision',
        'date_emprunt': now_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'tentative_numero': '1',
        'tentative_total': '3',
    }
    preview_overdue = _render_email_template(
        template_overdue,
        depassement='2j3h',
        type_rappel='Prêt en retard',
        **preview_vars,
    )
    preview_24h = _render_email_template(
        template_24h,
        depassement='dans 12h',
        type_rappel='Retour prévu sous 24h',
        **preview_vars,
    )

    lines.extend([
        "",
        "----------------------------------------",
        "APERÇU TEMPLATE - PRÊT EN RETARD",
        "----------------------------------------",
        preview_overdue,
        "",
        "----------------------------------------",
        "APERÇU TEMPLATE - RETOUR <24H",
        "----------------------------------------",
        preview_24h,
    ])

    text_body = "\n".join(lines)

    html_list = "".join(
        f"<li><strong>{html_escape(item.get('email', ''))}</strong> - "
        f"{html_escape(_label_reminder_kind(item.get('reminder_kind')))} - prêt #{item.get('pret_id', '?')}</li>"
        for item in sent_details
    )
    if not html_list:
        html_list = "<li>(Aucun élément)</li>"

    html_body = (
        "<!DOCTYPE html><html><body style='font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;'>"
        f"<h3>{html_escape(title_prefix)} PretGo</h3>"
        f"<p><strong>Total listés:</strong> {total}<br>"
        f"<strong>Retards:</strong> {total_retards}<br>"
        f"<strong>Retours &lt;24h:</strong> {total_24h}</p>"
        "<p><strong>Liste des emails:</strong></p>"
        f"<ol>{html_list}</ol>"
        "<hr>"
        "<p><strong>Aperçu template - prêt en retard</strong></p>"
        f"<pre style='white-space:pre-wrap;background:#f8f9fa;border:1px solid #dee2e6;padding:10px;border-radius:6px;'>{html_escape(preview_overdue)}</pre>"
        "<p><strong>Aperçu template - retour &lt;24h</strong></p>"
        f"<pre style='white-space:pre-wrap;background:#f8f9fa;border:1px solid #dee2e6;padding:10px;border-radius:6px;'>{html_escape(preview_24h)}</pre>"
        "</body></html>"
    )

    return subject, text_body, html_body


def envoyer_email_reference_manuel(conn, smtp_factory=None, now=None):
    """Envoie manuellement un mail de contrôle à l'email de référence."""
    now_dt = now or datetime.now()

    smtp_host = (get_setting('rappel_email_smtp_host', '', conn=conn) or '').strip()
    smtp_port = int(get_setting('rappel_email_smtp_port', '587', conn=conn) or '587')
    smtp_user = (get_setting('rappel_email_smtp_user', '', conn=conn) or '').strip()
    smtp_password = get_setting('rappel_email_smtp_password', '', conn=conn) or ''
    use_tls = get_setting('rappel_email_use_tls', '1', conn=conn) == '1'
    use_ssl = get_setting('rappel_email_use_ssl', '0', conn=conn) == '1'
    if use_tls and use_ssl:
        use_tls = False

    from_email = (get_setting('rappel_email_from', '', conn=conn) or '').strip()
    reply_to = (get_setting('rappel_email_reply_to', '', conn=conn) or '').strip()
    reference_email = (get_setting('rappel_email_reference_email', '', conn=conn) or '').strip()
    inclure_retour_24h = get_setting('rappel_email_inclure_retour_24h', '1', conn=conn) == '1'
    html_active = get_setting('rappel_email_html_active', '1', conn=conn) == '1'

    if not reference_email or not valider_email(reference_email):
        return {'success': False, 'message': "Email de référence invalide ou non configuré."}
    if not smtp_host or not from_email:
        return {'success': False, 'message': "Configuration SMTP incomplète (hôte ou expéditeur manquant)."}

    candidats = lister_prets_pour_rappel_mail(conn, now=now_dt, inclure_retour_24h=inclure_retour_24h)
    details = [
        {
            'email': item.get('email', ''),
            'reminder_kind': item.get('reminder_kind', 'overdue'),
            'pret_id': item.get('pret_id'),
        }
        for item in candidats if item.get('email')
    ]
    subject, text_body, html_report = _build_reference_report(
        conn,
        details,
        now_dt,
        title_prefix='Test manuel monitoring rappels'
    )

    smtp_cls = smtp_factory
    if smtp_cls is None:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP

    signature_raw, signature_subtype, signature_cid = _get_signature_image_info(conn)
    server = None
    try:
        if use_ssl:
            server = smtp_cls(smtp_host, smtp_port, context=ssl.create_default_context(), timeout=10)
        else:
            server = smtp_cls(smtp_host, smtp_port, timeout=10)
            if use_tls:
                server.starttls(context=ssl.create_default_context())
        if smtp_user:
            server.login(smtp_user, smtp_password)

        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = reference_email
        if reply_to:
            msg['Reply-To'] = reply_to
        msg.set_content(text_body)

        if html_active:
            msg.add_alternative(html_report, subtype='html')
            if signature_raw:
                try:
                    msg.get_payload()[-1].add_related(
                        signature_raw,
                        maintype='image',
                        subtype=signature_subtype,
                        cid=f'<{signature_cid}>'
                    )
                except Exception:
                    pass

        server.send_message(msg)
        return {'success': True, 'message': f"Email de référence envoyé à {reference_email}."}
    except Exception as e:
        return {'success': False, 'message': f"Échec envoi email de référence: {str(e)[:200]}"}
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass


def generer_preview_email(conn, reminder_kind='overdue'):
    """Génère un aperçu de l'email avec des valeurs de test.
    
    Retourne un dict {subject, body}
    """
    if reminder_kind not in ('overdue', 'upcoming_24h'):
        reminder_kind = 'overdue'

    from_email = (get_setting('rappel_email_from', '', conn=conn) or '').strip()
    subject_tpl = (get_setting('rappel_email_subject', '[PretGo] Rappel de retour de matériel', conn=conn) or '').strip()
    if reminder_kind == 'upcoming_24h':
        template_body = get_setting('rappel_email_template_retour_24h', '', conn=conn) or ''
        if not template_body:
            template_body = (
                "Bonjour {nom} {prenom},\n\n"
                "Votre prêt PretGo arrive bientôt à échéance.\n\n"
                "Objet(s): {objets}\n"
                "Date d'emprunt: {date_emprunt}\n"
                "Retour prévu: {depassement}\n"
                "Tentative: {tentative_numero}/{tentative_total}\n\n"
                "Merci d'anticiper le retour dans les délais prévus.\n\n"
                "Message automatique PretGo."
            )
        depassement_preview = 'dans 12h'
        type_rappel_preview = 'Retour prévu sous 24h'
    else:
        template_body = get_setting('rappel_email_template', '', conn=conn) or ''
        if not template_body:
            template_body = (
                "Bonjour {nom} {prenom},\n\n"
                "Ceci est un rappel de restitution de matériel PretGo.\n\n"
                "Objet(s): {objets}\n"
                "Date d'emprunt: {date_emprunt}\n"
                "Dépassement: {depassement}\n\n"
                "Merci de procéder au retour du matériel dès que possible.\n\n"
                "Message automatique PretGo."
            )
        depassement_preview = '2j3h'
        type_rappel_preview = 'Prêt en retard'
    
    # Valeurs de test
    body = _render_email_template(
        template_body,
        nom='Dupont',
        prenom='Jean',
        objets='Scie à métaux + Équerre de précision',
        date_emprunt=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        depassement=depassement_preview,
        type_rappel=type_rappel_preview,
        tentative_numero='2',
        tentative_total='3'
    )

    html_active = get_setting('rappel_email_html_active', '1', conn=conn) == '1'
    html_body = ''
    if html_active:
        signature_raw, signature_subtype, _signature_cid = _get_signature_image_info(conn)
        signature_src = None
        if signature_raw and signature_subtype:
            encoded = base64.b64encode(signature_raw).decode('ascii')
            signature_src = f'data:image/{signature_subtype};base64,{encoded}'
        html_body = _render_html_email(body, signature_src=signature_src)
    
    return {
        'subject': subject_tpl or '[PretGo] Rappel de retour de matériel',
        'body': body,
        'html_body': html_body,
        'html_active': html_active,
        'preview_kind': reminder_kind,
        'from': from_email,
    }


def tester_connexion_smtp(conn):
    """Teste la connexion SMTP avec les paramètres actuels.
    
    Retourne un dict {success, message}
    """
    smtp_host = (get_setting('rappel_email_smtp_host', '', conn=conn) or '').strip()
    smtp_port = int(get_setting('rappel_email_smtp_port', '587', conn=conn) or '587')
    smtp_user = (get_setting('rappel_email_smtp_user', '', conn=conn) or '').strip()
    smtp_password = get_setting('rappel_email_smtp_password', '', conn=conn) or ''
    use_tls = get_setting('rappel_email_use_tls', '1', conn=conn) == '1'
    use_ssl = get_setting('rappel_email_use_ssl', '0', conn=conn) == '1'
    if use_tls and use_ssl:
        use_tls = False
    
    if not smtp_host:
        return {'success': False, 'message': 'Hôte SMTP manquant'}
    
    try:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        if use_ssl:
            server = smtp_cls(smtp_host, smtp_port, context=ssl.create_default_context(), timeout=10)
        else:
            server = smtp_cls(smtp_host, smtp_port, timeout=10)
            if use_tls:
                server.starttls(context=ssl.create_default_context())
        
        if smtp_user:
            server.login(smtp_user, smtp_password)
        
        server.quit()
        return {'success': True, 'message': 'Connexion SMTP réussie ✓'}
    except smtplib.SMTPAuthenticationError:
        return {'success': False, 'message': 'Erreur d\'authentification SMTP (utilisateur/mot de passe)'}
    except smtplib.SMTPException as e:
        return {'success': False, 'message': f'Erreur SMTP : {str(e)[:100]}'}
    except Exception as e:
        return {'success': False, 'message': f'Erreur de connexion : {str(e)[:100]}'}


def _calculer_retour_theorique_datetime(date_emprunt_str, duree_heures, duree_jours,
                                        date_retour_prevue=None, _duree_defaut=None,
                                        _unite_defaut=None, _heure_fin='17:45'):
    """Calcule la date de retour théorique d'un prêt (datetime) ou None."""
    try:
        dt = datetime.strptime(date_emprunt_str, '%Y-%m-%d %H:%M:%S')
    except Exception:
        return None

    if date_retour_prevue:
        try:
            h_fin, m_fin = (int(x) for x in str(_heure_fin or '17:45').split(':'))
            return datetime.strptime(date_retour_prevue, '%Y-%m-%d').replace(hour=h_fin, minute=m_fin, second=0)
        except Exception:
            return None

    if duree_heures is not None:
        try:
            return dt + timedelta(hours=float(duree_heures))
        except Exception:
            return None
    if duree_jours is not None:
        try:
            return dt + timedelta(days=float(duree_jours))
        except Exception:
            return None

    duree_defaut = float(_duree_defaut if _duree_defaut is not None else 7)
    unite_defaut = _unite_defaut if _unite_defaut is not None else 'jours'
    if unite_defaut == 'heures':
        return dt + timedelta(hours=duree_defaut)
    return dt + timedelta(days=duree_defaut)


def lister_prets_pour_rappel_mail(conn, now=None, pret_ids=None, inclure_retour_24h=None):
    """Liste les prêts candidats à un rappel mail (retard + retour dans les 24h)."""
    now_dt = now or datetime.now()
    include_24h = inclure_retour_24h
    if include_24h is None:
        include_24h = get_setting('rappel_email_inclure_retour_24h', '1', conn=conn) == '1'

    ids_filtre = None
    if pret_ids is not None:
        ids_filtre = {int(pid) for pid in pret_ids if str(pid).isdigit()}
        if not ids_filtre:
            return []

    prets = conn.execute('''
        SELECT p.id, p.personne_id, p.descriptif_objets, p.date_emprunt,
               p.duree_pret_jours, p.duree_pret_heures, p.date_retour_prevue,
               pe.nom, pe.prenom, pe.email
        FROM prets p
        JOIN personnes pe ON p.personne_id = pe.id
        WHERE p.retour_confirme = 0
        ORDER BY p.date_emprunt ASC
    ''').fetchall()

    duree_def = float(get_setting('duree_alerte_defaut', '7', conn=conn) or '7')
    unite_def = get_setting('duree_alerte_unite', 'jours', conn=conn)
    heure_fin = get_setting('heure_fin_journee', '17:45', conn=conn)
    cooldown_h = float(get_setting('rappel_email_cooldown_heures', '24', conn=conn) or '24')
    max_tentatives = int(get_setting('rappel_email_max_tentatives', '3', conn=conn) or '3')

    result = []
    for pret in prets:
        if ids_filtre is not None and pret['id'] not in ids_filtre:
            continue

        retour_theorique = _calculer_retour_theorique_datetime(
            pret['date_emprunt'], pret['duree_pret_heures'], pret['duree_pret_jours'],
            date_retour_prevue=pret['date_retour_prevue'], _duree_defaut=duree_def,
            _unite_defaut=unite_def, _heure_fin=heure_fin
        )
        if not retour_theorique:
            continue

        delta_h = (now_dt - retour_theorique).total_seconds() / 3600.0
        if delta_h > 0:
            reminder_kind = 'overdue'
            reminder_label = 'En retard'
        elif include_24h and -24 <= delta_h < 0:
            reminder_kind = 'upcoming_24h'
            reminder_label = 'Retour < 24h'
        else:
            continue

        email_dest = (pret['email'] or '').strip()
        tentative_numero, tentative_total = compter_tentatives_pret(conn, pret['id'])
        max_atteint = tentative_total >= max_tentatives

        last_sent = conn.execute(
            """
            SELECT sent_at FROM rappels_email_log
            WHERE pret_id = ? AND status = 'sent'
            ORDER BY sent_at DESC LIMIT 1
            """,
            (pret['id'],)
        ).fetchone()
        cooldown_ok = True
        cooldown_restant_h = 0.0
        if last_sent and last_sent['sent_at']:
            try:
                dt_last = datetime.strptime(last_sent['sent_at'], '%Y-%m-%d %H:%M:%S')
                elapsed_h = (now_dt - dt_last).total_seconds() / 3600.0
                if elapsed_h < cooldown_h:
                    cooldown_ok = False
                    cooldown_restant_h = max(0.0, cooldown_h - elapsed_h)
            except Exception:
                pass

        email_ok = bool(email_dest) and valider_email(email_dest)
        can_send = email_ok and cooldown_ok and (not max_atteint)

        if reminder_kind == 'overdue':
            delta_label = _format_depassement_texte(delta_h)
        else:
            reste_h = abs(delta_h)
            delta_label = f"{int(reste_h)}h" if reste_h < 24 else f"{int(reste_h // 24)}j"

        result.append({
            'pret_id': pret['id'],
            'personne_id': pret['personne_id'],
            'nom': pret['nom'],
            'prenom': pret['prenom'],
            'email': email_dest,
            'descriptif_objets': pret['descriptif_objets'],
            'date_emprunt': pret['date_emprunt'],
            'retour_theorique': retour_theorique,
            'reminder_kind': reminder_kind,
            'reminder_label': reminder_label,
            'delta_heures': delta_h,
            'delta_label': delta_label,
            'tentative_numero': tentative_numero,
            'tentative_total': tentative_total,
            'cooldown_ok': cooldown_ok,
            'cooldown_restant_h': cooldown_restant_h,
            'max_atteint': max_atteint,
            'email_ok': email_ok,
            'can_send': can_send,
        })

    # Tri: retards d'abord (plus anciens en premier), puis retours <24h (plus urgents d'abord)
    def _sort_key(item):
        if item['reminder_kind'] == 'overdue':
            return (0, -item['delta_heures'])
        return (1, abs(item['delta_heures']))

    result.sort(key=_sort_key)
    return result


def envoyer_rappels_alertes_email(conn, smtp_factory=None, now=None, pret_ids=None, inclure_retour_24h=None):
    """Envoie des rappels email (retards + retours dans 24h)."""
    now_dt = now or datetime.now()
    now_str = now_dt.strftime('%Y-%m-%d %H:%M:%S')

    # Réglages email
    email_active = get_setting('rappel_email_active', '0', conn=conn) == '1'
    smtp_host = (get_setting('rappel_email_smtp_host', '', conn=conn) or '').strip()
    smtp_port = int(get_setting('rappel_email_smtp_port', '587', conn=conn) or '587')
    smtp_user = (get_setting('rappel_email_smtp_user', '', conn=conn) or '').strip()
    smtp_password = get_setting('rappel_email_smtp_password', '', conn=conn) or ''
    use_tls = get_setting('rappel_email_use_tls', '1', conn=conn) == '1'
    use_ssl = get_setting('rappel_email_use_ssl', '0', conn=conn) == '1'
    if use_tls and use_ssl:
        use_tls = False
    from_email = (get_setting('rappel_email_from', '', conn=conn) or '').strip()
    reply_to = (get_setting('rappel_email_reply_to', '', conn=conn) or '').strip()
    subject_tpl = (get_setting('rappel_email_subject', '[PretGo] Rappel de retour de matériel', conn=conn) or '').strip()
    max_tentatives = int(get_setting('rappel_email_max_tentatives', '3', conn=conn) or '3')
    reference_active = get_setting('rappel_email_reference_active', '0', conn=conn) == '1'
    reference_email = (get_setting('rappel_email_reference_email', '', conn=conn) or '').strip()

    stats = {
        'total_alertes': 0,
        'eligibles': 0,
        'envoyes': 0,
        'echecs': 0,
        'ignores_sans_email': 0,
        'ignores_cooldown': 0,
        'bloques': 0,
        'total_retards': 0,
        'total_retours_24h': 0,
        'reference_notified': 0,
    }

    try:
        log_columns = conn.execute("PRAGMA table_info(rappels_email_log)").fetchall()
        has_reminder_kind = any((col['name'] if isinstance(col, sqlite3.Row) else col[1]) == 'reminder_kind' for col in log_columns)
    except Exception:
        has_reminder_kind = False

    def _insert_log(pret_id, personne_id, email, status, reminder_kind, error_message, depassement_heures):
        if has_reminder_kind:
            conn.execute(
                """
                INSERT INTO rappels_email_log
                (pret_id, personne_id, email, sent_at, status, reminder_kind, error_message, depassement_heures)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pret_id, personne_id, email, now_str, status, reminder_kind, error_message, depassement_heures)
            )
        else:
            conn.execute(
                """
                INSERT INTO rappels_email_log
                (pret_id, personne_id, email, sent_at, status, error_message, depassement_heures)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (pret_id, personne_id, email, now_str, status, error_message, depassement_heures)
            )

    if not email_active:
        stats['error'] = 'Rappels email désactivés dans les réglages.'
        return stats

    if not smtp_host or not from_email:
        stats['error'] = 'Configuration SMTP incomplète (hôte ou expéditeur manquant).'
        return stats

    candidats = lister_prets_pour_rappel_mail(
        conn,
        now=now_dt,
        pret_ids=pret_ids,
        inclure_retour_24h=inclure_retour_24h,
    )
    stats['total_alertes'] = len(candidats)
    stats['total_retards'] = sum(1 for c in candidats if c['reminder_kind'] == 'overdue')
    stats['total_retours_24h'] = sum(1 for c in candidats if c['reminder_kind'] == 'upcoming_24h')

    a_envoyer = []
    for c in candidats:
        if not c['email_ok']:
            stats['ignores_sans_email'] += 1
            continue
        if c['max_atteint']:
            _insert_log(
                c['pret_id'],
                c['personne_id'],
                c['email'],
                'blocked',
                c['reminder_kind'],
                f'Maximum de tentatives atteint ({max_tentatives})',
                float(max(0.0, c['delta_heures']))
            )
            stats['bloques'] += 1
            continue
        if not c['cooldown_ok']:
            stats['ignores_cooldown'] += 1
            continue

        stats['eligibles'] += 1
        a_envoyer.append(c)

    if not a_envoyer:
        return stats

    html_active = get_setting('rappel_email_html_active', '1', conn=conn) == '1'
    signature_raw, signature_subtype, signature_cid = _get_signature_image_info(conn)

    template_overdue = get_setting('rappel_email_template', '', conn=conn) or ''
    template_upcoming_24h = get_setting('rappel_email_template_retour_24h', '', conn=conn) or ''

    if not template_overdue:
        template_overdue = (
            "Bonjour {nom} {prenom},\n\n"
            "Ceci est un rappel de restitution de matériel PretGo.\n\n"
            "Objet(s): {objets}\n"
            "Date d'emprunt: {date_emprunt}\n"
            "Statut: {type_rappel}\n"
            "Détail: {depassement}\n"
            "Tentative: {tentative_numero}/{tentative_total}\n\n"
            "Merci de procéder au retour du matériel dès que possible.\n\n"
            "Message automatique PretGo."
        )

    if not template_upcoming_24h:
        template_upcoming_24h = (
            "Bonjour {nom} {prenom},\n\n"
            "Votre prêt PretGo arrive bientôt à échéance.\n\n"
            "Objet(s): {objets}\n"
            "Date d'emprunt: {date_emprunt}\n"
            "Retour prévu: {depassement}\n"
            "Tentative: {tentative_numero}/{tentative_total}\n\n"
            "Merci d'anticiper le retour dans les délais prévus.\n\n"
            "Message automatique PretGo."
        )

    smtp_cls = smtp_factory
    if smtp_cls is None:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP

    server = None
    try:
        if use_ssl:
            server = smtp_cls(smtp_host, smtp_port, context=ssl.create_default_context(), timeout=10)
        else:
            server = smtp_cls(smtp_host, smtp_port, timeout=10)
            if use_tls:
                server.starttls(context=ssl.create_default_context())

        if smtp_user:
            server.login(smtp_user, smtp_password)

        sent_details = []
        for c in a_envoyer:
            if c['reminder_kind'] == 'upcoming_24h':
                type_rappel = _label_reminder_kind('upcoming_24h')
                dep_texte = f"Retour prévu dans {c['delta_label']}"
                active_template = template_upcoming_24h
            else:
                type_rappel = _label_reminder_kind('overdue')
                dep_texte = _format_depassement_texte(c['delta_heures'])
                active_template = template_overdue

            body = _render_email_template(
                active_template,
                nom=c['nom'],
                prenom=c['prenom'],
                objets=c['descriptif_objets'],
                date_emprunt=c['date_emprunt'],
                depassement=dep_texte,
                type_rappel=type_rappel,
                tentative_numero=str(c['tentative_numero']),
                tentative_total=str(c['tentative_total'])
            )

            msg = EmailMessage()
            msg['Subject'] = subject_tpl
            msg['From'] = from_email
            msg['To'] = c['email']
            if reply_to:
                msg['Reply-To'] = reply_to
            msg.set_content(body)

            if html_active:
                html_body = _render_html_email(body, signature_cid=signature_cid if signature_raw else None)
                msg.add_alternative(html_body, subtype='html')
                if signature_raw:
                    try:
                        msg.get_payload()[-1].add_related(
                            signature_raw,
                            maintype='image',
                            subtype=signature_subtype,
                            cid=f'<{signature_cid}>'
                        )
                    except Exception:
                        # En cas d'échec inline image, on garde le fallback texte+HTML sans image.
                        pass

            try:
                server.send_message(msg)
                _insert_log(
                    c['pret_id'],
                    c['personne_id'],
                    c['email'],
                    'sent',
                    c['reminder_kind'],
                    '',
                    float(max(0.0, c['delta_heures']))
                )
                stats['envoyes'] += 1
                sent_details.append({
                    'email': c['email'],
                    'type_rappel': type_rappel,
                    'reminder_kind': c['reminder_kind'],
                    'pret_id': c['pret_id'],
                })
            except Exception as e:
                _insert_log(
                    c['pret_id'],
                    c['personne_id'],
                    c['email'],
                    'failed',
                    c['reminder_kind'],
                    str(e)[:500],
                    float(max(0.0, c['delta_heures']))
                )
                stats['echecs'] += 1

        # Email de référence (monitoring): récapitulatif des envois réellement partis.
        if reference_active and reference_email and valider_email(reference_email) and sent_details:
            recap_subject, recap_text, recap_html = _build_reference_report(
                conn,
                sent_details,
                now_dt,
                title_prefix='Rapport envoi rappels'
            )

            recap_msg = EmailMessage()
            recap_msg['Subject'] = recap_subject
            recap_msg['From'] = from_email
            recap_msg['To'] = reference_email
            if reply_to:
                recap_msg['Reply-To'] = reply_to
            recap_msg.set_content(recap_text)

            if html_active:
                recap_msg.add_alternative(recap_html, subtype='html')

            try:
                server.send_message(recap_msg)
                stats['reference_notified'] = 1
            except Exception as e:
                _log.warning('Échec envoi email de référence: %s', str(e)[:200])
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

    return stats


# ============================================================
#  HELPER CSV
# ============================================================

def csv_response(output, filename_prefix):
    """Helper : crée une Response CSV avec BOM UTF-8 pour Excel."""
    output.seek(0)
    bom = '\ufeff'
    return Response(
        bom + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition':
                f'attachment; filename={filename_prefix}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        }
    )


# ============================================================
#  HELPER REQUÊTE INVENTAIRE
# ============================================================

def query_inventaire(
    filtre_type='tous',
    recherche='',
    etat_only=None,
    page=None,
    par_page=50,
    tri='type',
    filtre_types=None,
    ids_only=None
):
    """Helper : interroge l'inventaire avec filtres et pagination optionnelle.

    Renvoie (items, types, comptages) ou
    (items, types, comptages, total, total_pages, page) si page est fourni.
    """
    conn = get_app_db()
    query = 'SELECT * FROM inventaire WHERE actif = 1'
    count_query = 'SELECT COUNT(*) FROM inventaire WHERE actif = 1'
    params = []
    count_params = []

    if etat_only:
        query += ' AND etat = ?'
        count_query += ' AND etat = ?'
        params.append(etat_only)
        count_params.append(etat_only)

    selected_types = []
    if filtre_types:
        for raw_type in filtre_types:
            value = str(raw_type or '').strip()
            if value and value.lower() != 'tous' and value not in selected_types:
                selected_types.append(value)
    if not selected_types and filtre_type != 'tous':
        selected_types.append(filtre_type)

    if selected_types:
        placeholders = ','.join(['?'] * len(selected_types))
        query += f' AND type_materiel IN ({placeholders})'
        count_query += f' AND type_materiel IN ({placeholders})'
        params.extend(selected_types)
        count_params.extend(selected_types)

    if ids_only is not None:
        selected_ids = []
        for raw_id in ids_only:
            if str(raw_id).isdigit():
                selected_ids.append(int(raw_id))
        if selected_ids:
            placeholders = ','.join(['?'] * len(selected_ids))
            query += f' AND id IN ({placeholders})'
            count_query += f' AND id IN ({placeholders})'
            params.extend(selected_ids)
            count_params.extend(selected_ids)
        else:
            query += ' AND 1 = 0'
            count_query += ' AND 1 = 0'

    if recherche:
        like_clause = ' AND (numero_inventaire LIKE ? OR marque LIKE ? OR modele LIKE ? OR numero_serie LIKE ?)'
        query += like_clause
        count_query += like_clause
        params.extend([f'%{recherche}%'] * 4)
        count_params.extend([f'%{recherche}%'] * 4)

    order_clauses = {
        'type': 'type_materiel ASC, numero_inventaire ASC',
        'inventaire_asc': 'numero_inventaire ASC, id ASC',
        'inventaire_desc': 'numero_inventaire DESC, id DESC',
        'marque_modele_asc': "COALESCE(marque, '') ASC, COALESCE(modele, '') ASC, numero_inventaire ASC",
        'marque_modele_desc': "COALESCE(marque, '') DESC, COALESCE(modele, '') DESC, numero_inventaire ASC",
        'etat_asc': (
            "CASE etat "
            "WHEN 'disponible' THEN 0 "
            "WHEN 'prete' THEN 1 "
            "WHEN 'en_panne' THEN 2 "
            "WHEN 'reforme' THEN 3 "
            "ELSE 4 END ASC, numero_inventaire ASC"
        ),
        'etat_desc': (
            "CASE etat "
            "WHEN 'disponible' THEN 0 "
            "WHEN 'prete' THEN 1 "
            "WHEN 'en_panne' THEN 2 "
            "WHEN 'reforme' THEN 3 "
            "ELSE 4 END DESC, numero_inventaire ASC"
        ),
        'date_asc': 'date_creation ASC, id ASC',
        'date_desc': 'date_creation DESC, id DESC',
    }
    query += ' ORDER BY ' + order_clauses.get(tri, order_clauses['type'])

    if page is not None:
        total = conn.execute(count_query, count_params).fetchone()[0]
        total_pages = max(1, (total + par_page - 1) // par_page)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * par_page
        query += ' LIMIT ? OFFSET ?'
        params.extend([par_page, offset])
        items = conn.execute(query, params).fetchall()
    else:
        items = conn.execute(query, params).fetchall()
        total = len(items)
        total_pages = 1

    types = conn.execute(
        'SELECT DISTINCT type_materiel FROM inventaire WHERE actif = 1 ORDER BY type_materiel'
    ).fetchall()

    comptages = {}
    rows = conn.execute(
        'SELECT type_materiel, COUNT(*) as cnt FROM inventaire WHERE actif = 1 GROUP BY type_materiel ORDER BY type_materiel'
    ).fetchall()
    total_count = 0
    for r in rows:
        comptages[r['type_materiel']] = r['cnt']
        total_count += r['cnt']
    comptages['total'] = total_count

    if page is not None:
        return items, types, comptages, total, total_pages, page
    return items, types, comptages


# ============================================================
#  CHAMPS PERSONNALISÉS
# ============================================================

def get_champs_personnalises(entite):
    """Récupérer les champs personnalisés actifs pour une entité."""
    conn = get_app_db()
    champs = conn.execute(
        'SELECT * FROM champs_personnalises WHERE entite = ? AND actif = 1 ORDER BY ordre, id',
        (entite,)
    ).fetchall()
    return champs


def get_valeurs_champs(entite_id, entite_type):
    """Récupérer les valeurs des champs personnalisés pour une entité."""
    conn = get_app_db()
    valeurs = conn.execute('''
        SELECT cp.nom_champ, vcp.valeur
        FROM valeurs_champs_personnalises vcp
        JOIN champs_personnalises cp ON vcp.champ_id = cp.id
        WHERE vcp.entite_id = ? AND cp.entite = ?
    ''', (entite_id, entite_type)).fetchall()
    return {v['nom_champ']: v['valeur'] for v in valeurs}


def sauver_valeurs_champs(entite_id, entite_type, form_data):
    """Sauvegarder les valeurs des champs personnalisés."""
    champs = get_champs_personnalises(entite_type)
    conn = get_app_db()
    for champ in champs:
        valeur = form_data.get(f'custom_{champ["nom_champ"]}', '').strip()
        # Upsert : supprimer puis insérer
        conn.execute(
            'DELETE FROM valeurs_champs_personnalises WHERE champ_id = ? AND entite_id = ?',
            (champ['id'], entite_id)
        )
        if valeur:
            conn.execute(
                'INSERT INTO valeurs_champs_personnalises (champ_id, entite_id, valeur) VALUES (?, ?, ?)',
                (champ['id'], entite_id, valeur)
            )
    conn.commit()


# ============================================================
#  DÉCORATEUR ADMIN
# ============================================================

def admin_required(f):
    """Décorateur pour protéger les routes administrateur."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Accès réservé à l\'administrateur. Veuillez vous connecter.', 'warning')
            return redirect(url_for('admin.admin_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


# ============================================================
#  FILTRES JINJA & CONTEXT PROCESSORS
# ============================================================

def register_filters(app):
    """Enregistre tous les filtres Jinja personnalisés."""

    @app.template_filter('format_date')
    def format_date(value):
        """Formate une date en format français lisible."""
        if not value:
            return ''
        try:
            dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            return dt.strftime('%d/%m/%Y à %H:%M')
        except Exception:
            return value

    @app.template_filter('format_date_court')
    def format_date_court(value):
        """Formate une date en format français court (JJ/MM/AAAA)."""
        if not value:
            return '—'
        try:
            dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
            return dt.strftime('%d/%m/%Y')
        except Exception:
            return str(value)[:10]

    @app.template_filter('format_heure')
    def format_heure(value):
        """Extrait l'heure d'une date (HH:MM)."""
        if not value:
            return ''
        try:
            dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
            return dt.strftime('%H:%M')
        except Exception:
            return str(value)[11:16]

    @app.template_filter('jours_ecoules')
    def jours_ecoules(date_str):
        """Calcule le nombre de jours écoulés depuis une date."""
        if not date_str:
            return 0
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            delta = datetime.now() - dt
            return delta.days
        except Exception:
            return 0

    @app.template_filter('label_categorie')
    def label_categorie(value):
        """Renvoie le libellé lisible d'une catégorie (via le cache du context_processor)."""
        cats = getattr(g, '_cats_personnes_cache', None)
        if cats is None:
            try:
                cats_list = get_categories_personnes()
                cats = {c['cle']: dict(c) for c in cats_list}
            except Exception:
                cats = {}
            g._cats_personnes_cache = cats
        cat = cats.get(value)
        if cat:
            return cat['libelle']
        return value.replace('_', ' ').title() if value else value

    @app.template_filter('style_categorie')
    def style_categorie(value):
        """Renvoie le style CSS inline pour un badge de catégorie."""
        cats = getattr(g, '_cats_personnes_cache', None)
        if cats is None:
            try:
                cats_list = get_categories_personnes()
                cats = {c['cle']: dict(c) for c in cats_list}
            except Exception:
                cats = {}
            g._cats_personnes_cache = cats
        cat = cats.get(value)
        if cat:
            return f"background-color:{cat['couleur_bg']};color:{cat['couleur_text']}"
        return 'background-color:#f1f3f4;color:#5f6368'

    @app.template_filter('format_duree')
    def format_duree(pret):
        """Formate la durée d'un prêt en texte lisible."""
        if not pret:
            return 'Durée par défaut'
        type_duree = pret['type_duree'] if pret['type_duree'] else None
        if type_duree == 'date_precise':
            try:
                drp = pret['date_retour_prevue']
            except (KeyError, IndexError):
                drp = None
            if drp:
                try:
                    dt_retour = datetime.strptime(drp, '%Y-%m-%d')
                    cache = getattr(g, '_settings_cache', {})
                    heure_fin = cache.get('heure_fin_journee') or get_setting('heure_fin_journee', '17:45')
                    return f"Le {dt_retour.strftime('%d/%m/%Y')} (à {heure_fin.replace(':', 'h')})"
                except Exception:
                    pass
        if type_duree == 'fin_journee':
            cache = getattr(g, '_settings_cache', {})
            heure_fin = cache.get('heure_fin_journee') or get_setting('heure_fin_journee', '17:45')
            return f'Fin de journée ({heure_fin.replace(":", "h")})'
        heures = pret['duree_pret_heures'] if pret['duree_pret_heures'] else None
        jours = pret['duree_pret_jours'] if pret['duree_pret_jours'] else None
        if heures is not None:
            if heures < 24:
                h = int(heures)
                m = int((heures - h) * 60)
                if m > 0:
                    return f'{h}h{m:02d}'
                return f'{h} heure(s)'
            else:
                j = heures / 24
                if j == int(j):
                    return f'{int(j)} jour(s)'
                return f'{j:.1f} jour(s)'
        elif jours is not None:
            return f'{jours} jour(s)'
        return 'Durée par défaut'

    @app.template_filter('retour_theorique')
    def retour_theorique_filter(pret):
        """Calcule la date de retour théorique."""
        if not pret or not pret['date_emprunt']:
            return ''
        type_duree = pret['type_duree'] if pret['type_duree'] else None
        if type_duree in ('aucune',):
            return '—'
        cache = getattr(g, '_settings_cache', {})
        if type_duree == 'date_precise':
            try:
                drp = pret['date_retour_prevue']
            except (KeyError, IndexError):
                drp = None
            if drp:
                try:
                    dt_retour = datetime.strptime(drp, '%Y-%m-%d')
                    heure_fin = cache.get('heure_fin_journee') or get_setting('heure_fin_journee', '17:45')
                    return dt_retour.strftime('%d/%m/%Y') + f" à {heure_fin}"
                except Exception:
                    return ''
        try:
            dt = datetime.strptime(pret['date_emprunt'], '%Y-%m-%d %H:%M:%S')
            heures = pret['duree_pret_heures'] if pret['duree_pret_heures'] else None
            jours = pret['duree_pret_jours'] if pret['duree_pret_jours'] else None
            if heures is not None:
                retour = dt + timedelta(hours=heures)
            elif jours is not None:
                retour = dt + timedelta(days=jours)
            else:
                duree_defaut = cache.get('duree_alerte_defaut')
                if duree_defaut is None:
                    duree_defaut = float(get_setting('duree_alerte_defaut', '7'))
                unite = cache.get('duree_alerte_unite') or get_setting('duree_alerte_unite', 'jours')
                if unite == 'heures':
                    retour = dt + timedelta(hours=duree_defaut)
                else:
                    retour = dt + timedelta(days=duree_defaut)
            return retour.strftime('%d/%m/%Y à %H:%M')
        except Exception:
            return ''


def register_context_processors(app):
    """Enregistre les context processors Flask."""

    @app.context_processor
    def utility_processor():
        """Variables disponibles dans tous les templates."""
        nb_alertes = 0
        conn = None
        try:
            conn = get_app_db()
            # Précharger les settings utilisés partout (1 seule requête chacun)
            duree_def = float(get_setting('duree_alerte_defaut', '7', conn=conn))
            unite_def = get_setting('duree_alerte_unite', 'jours', conn=conn)
            heure_fin = get_setting('heure_fin_journee', '17:45', conn=conn)
            # Stocker dans g pour les filtres Jinja (évite des appels répétés)
            g._settings_cache = {
                'duree_alerte_defaut': duree_def,
                'duree_alerte_unite': unite_def,
                'heure_fin_journee': heure_fin,
            }
            # Compter les alertes directement en SQL pour les dates précises
            # et ne charger que les prêts nécessitant un calcul Python
            prets_actifs = conn.execute(
                'SELECT date_emprunt, duree_pret_jours, duree_pret_heures, date_retour_prevue, type_duree FROM prets WHERE retour_confirme = 0'
            ).fetchall()
            for p in prets_actifs:
                depasse, _ = calcul_depassement_heures(
                    p['date_emprunt'], p['duree_pret_heures'], p['duree_pret_jours'],
                    _duree_defaut=duree_def, _unite_defaut=unite_def,
                    date_retour_prevue=p['date_retour_prevue'], _heure_fin=heure_fin
                )
                if depasse:
                    nb_alertes += 1
        except Exception:
            pass

        # Vérifier si admin connecté
        is_admin = bool(session.get('admin_logged_in'))

        # Charger les catégories de personnes pour tous les templates
        # et alimenter le cache g pour les filtres Jinja
        try:
            if conn is None:
                conn = get_app_db()
            cats = conn.execute(
                'SELECT * FROM categories_personnes WHERE actif = 1 ORDER BY ordre, libelle'
            ).fetchall()
            cats_personnes = {c['cle']: dict(c) for c in cats}
        except Exception:
            cats_personnes = {}
        g._cats_personnes_cache = cats_personnes

        # Charger le thème personnalisé (toutes les requêtes via la même connexion)
        if conn is None:
            conn = get_app_db()
        theme = {
            'couleur_primaire': get_setting('theme_couleur_primaire', '#1a73e8', conn=conn),
            'couleur_navbar': get_setting('theme_couleur_navbar', '#1a56db', conn=conn),
            'logo': get_setting('theme_logo', '', conn=conn),
            'nom_application': get_setting('theme_nom_application', 'PretGo', conn=conn),
            'mode_sombre': get_setting('theme_mode_sombre', '0', conn=conn) == '1',
        }

        return {
            'now': datetime.now,
            'nb_alertes': nb_alertes,
            'is_admin': is_admin,
            'cats_personnes': cats_personnes,
            'mode_scanner': get_setting('mode_scanner', 'les_deux', conn=conn),
            'calcul_depassement_heures': calcul_depassement_heures,
            'theme': theme,
            'backup_alerte': _check_backup_alerte(conn),
        }


# ============================================================
#  BACKUP AUTOMATIQUE
# ============================================================

_backup_lock = threading.Lock()
_last_backup_check = 0  # timestamp du dernier check (évite de checker à chaque requête)


def effectuer_backup(chemin_dest=None):
    """Effectue une sauvegarde complète (.pretgo = zip).
    Retourne (success: bool, message: str, filepath: str|None)."""
    with _backup_lock:
        try:
            dest_dir = chemin_dest or BACKUP_DIR
            os.makedirs(dest_dir, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'PretGo_auto_{timestamp}.pretgo'
            zip_path = os.path.join(dest_dir, filename)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Base de données SQLite
                if os.path.exists(DATABASE_PATH):
                    zf.write(DATABASE_PATH, 'gestion_prets.db')
                # Images matériel
                if os.path.exists(UPLOAD_FOLDER):
                    for f in os.listdir(UPLOAD_FOLDER):
                        fpath = os.path.join(UPLOAD_FOLDER, f)
                        if os.path.isfile(fpath):
                            zf.write(fpath, f'uploads/materiel/{f}')
                # Documents
                if os.path.exists(DOCUMENTS_DIR):
                    for f in os.listdir(DOCUMENTS_DIR):
                        fpath = os.path.join(DOCUMENTS_DIR, f)
                        if os.path.isfile(fpath):
                            zf.write(fpath, f'documents/{f}')
                # Code de récupération
                if os.path.exists(RECOVERY_CODE_PATH):
                    zf.write(RECOVERY_CODE_PATH, 'code_recuperation.txt')

            # Rotation : supprimer les anciens fichiers auto
            _rotation_backups(dest_dir)

            return True, f'Sauvegarde effectuée : {filename}', zip_path

        except Exception as e:
            _log.error(f'Erreur backup automatique : {e}')
            return False, str(e), None


def _rotation_backups(dest_dir, max_backups=None):
    """Supprime les sauvegardes automatiques les plus anciennes."""
    if max_backups is None:
        try:
            max_backups = int(get_setting('backup_auto_nombre_max', '5'))
        except (ValueError, TypeError):
            max_backups = 5
    if max_backups <= 0:
        return

    # Lister uniquement les fichiers auto
    files = sorted(
        [f for f in os.listdir(dest_dir) if f.startswith('PretGo_auto_') and f.endswith('.pretgo')],
        reverse=True
    )
    for old_file in files[max_backups:]:
        try:
            os.remove(os.path.join(dest_dir, old_file))
        except Exception:
            pass


def check_and_run_backup(app):
    """Vérifie si un backup automatique est dû et l'exécute en arrière-plan.
    Appelé via before_request, limité à un check toutes les 5 minutes."""
    global _last_backup_check

    now = _time.time()
    if now - _last_backup_check < 300:  # 5 min entre chaque vérification
        return
    _last_backup_check = now

    try:
        actif = get_setting('backup_auto_active', '0')
        if actif != '1':
            return

        frequence = get_setting('backup_auto_frequence', 'quotidien')
        derniere = get_setting('backup_auto_derniere', '')

        # Calculer l'intervalle
        if frequence == 'hebdomadaire':
            intervalle = timedelta(days=7)
        elif frequence == 'mensuel':
            intervalle = timedelta(days=30)
        else:  # quotidien
            intervalle = timedelta(days=1)

        # Vérifier si le backup est dû
        if derniere:
            try:
                dt_derniere = datetime.strptime(derniere, '%Y-%m-%d %H:%M:%S')
                if datetime.now() - dt_derniere < intervalle:
                    return  # Pas encore dû
            except (ValueError, TypeError):
                pass  # Date invalide, on fait le backup

        # Lancer le backup en arrière-plan
        chemin = get_setting('backup_auto_chemin', '').strip()
        if not chemin:
            chemin = None  # Utilise BACKUP_DIR par défaut

        def _run_backup():
            with app.app_context():
                success, message, _ = effectuer_backup(chemin)
                conn = get_db()
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if success:
                    set_setting('backup_auto_derniere', now_str, conn=conn)
                    set_setting('backup_auto_erreur', '', conn=conn)
                else:
                    set_setting('backup_auto_erreur', f'{now_str} — {message}', conn=conn)
                conn.commit()
                conn.close()

        t = threading.Thread(target=_run_backup, daemon=True)
        t.start()

    except Exception as e:
        _log.error(f'Erreur check backup auto : {e}')


def _check_backup_alerte(conn=None):
    """Retourne un dict d'alerte backup si nécessaire (pour le context processor)."""
    try:
        actif = get_setting('backup_auto_active', '0', conn=conn)
        if actif != '1':
            return None

        erreur = get_setting('backup_auto_erreur', '', conn=conn)
        if erreur:
            return {'type': 'danger', 'message': f'Échec de la dernière sauvegarde automatique : {erreur}'}

        derniere = get_setting('backup_auto_derniere', '', conn=conn)
        if not derniere:
            return {'type': 'warning', 'message': 'Aucune sauvegarde automatique n\'a encore été effectuée.'}

        try:
            dt_derniere = datetime.strptime(derniere, '%Y-%m-%d %H:%M:%S')
            frequence = get_setting('backup_auto_frequence', 'quotidien', conn=conn)
            if frequence == 'hebdomadaire':
                seuil = timedelta(days=8)
            elif frequence == 'mensuel':
                seuil = timedelta(days=32)
            else:
                seuil = timedelta(days=2)

            if datetime.now() - dt_derniere > seuil:
                return {'type': 'warning', 'message': f'Dernière sauvegarde automatique le {dt_derniere.strftime("%d/%m/%Y à %H:%M")} — en retard.'}
        except (ValueError, TypeError):
            pass

        return None
    except Exception:
        return None
