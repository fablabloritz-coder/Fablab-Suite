"""
PretGo — Fonctions utilitaires partagées entre les blueprints.
"""

from flask import g, redirect, url_for, flash, session, request, Response
from database import get_db, get_setting, set_setting, DATABASE_PATH, BACKUP_DIR, DOCUMENTS_DIR, RECOVERY_CODE_PATH
from datetime import datetime, timedelta
from functools import wraps
import logging
import os
import secrets
import shutil
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
