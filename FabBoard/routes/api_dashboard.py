"""
FabBoard — API Dashboard / Core (blueprint)
Dashboard data, paramètres, thème, météo, GIF resolver, heure serveur
"""

from flask import Blueprint, request, jsonify
from models import get_db, get_theme, update_theme
from datetime import datetime, timedelta
import json
import re
import os
import time
import requests as http_requests
from html import unescape
from urllib.parse import urlparse, urljoin

bp = Blueprint('api_dashboard', __name__)

# Cache mémoire pour météo Open-Meteo
_meteo_cache = {}

# Cache mémoire pour la résolution des URLs GIF distantes
_gif_resolve_cache = {}


def _rows_to_list(rows):
    """Convertit une liste de Row SQLite en liste de dictionnaires."""
    return [dict(r) for r in rows]


# ============================================================
# HEURE SERVEUR
# ============================================================

@bp.route('/api/server-time')
def server_time():
    """Retourne le timestamp Unix du serveur (pour synchronisation horloge client)."""
    return jsonify(success=True, timestamp=time.time())


# ============================================================
# DASHBOARD DATA
# ============================================================

@bp.route('/api/dashboard/data')
def dashboard_data():
    """Retourne les données agrégées pour le dashboard TV."""
    from routes.api_sources import (
        _normalize_base_url, _resolve_fabtrack_base_url,
        _extract_fabtrack_payload, get_cached_source_data,
    )

    db = get_db()
    try:
        # ── Fabtrack : chercher dans le cache d'abord ──
        fabtrack_source = db.execute(
            'SELECT id, url FROM sources WHERE type = ? AND actif = 1 ORDER BY id LIMIT 1',
            ('fabtrack',)
        ).fetchone()

        fabtrack_data = None
        fabtrack_error = ''
        fabtrack_url = ''

        if fabtrack_source:
            fabtrack_url = _normalize_base_url(fabtrack_source['url'])
            cached = get_cached_source_data(fabtrack_source['id'])
            if cached:
                fabtrack_data = cached
            else:
                payload, err = _extract_fabtrack_payload(fabtrack_url)
                if payload:
                    fabtrack_data = {
                        'summary': payload.get('fabtrack_stats', {}),
                        'consommations': payload.get('activites', []),
                        'machines': payload.get('machines', []),
                        'missions': payload.get('missions', []),
                    }
                else:
                    fabtrack_error = err
        else:
            base_url = _resolve_fabtrack_base_url()
            fabtrack_url = base_url
            payload, err = _extract_fabtrack_payload(base_url)
            if payload:
                fabtrack_data = {
                    'summary': payload.get('fabtrack_stats', {}),
                    'consommations': payload.get('activites', []),
                    'machines': payload.get('machines', []),
                    'missions': payload.get('missions', []),
                }
            else:
                fabtrack_error = err

        summary = (fabtrack_data or {}).get('summary', {})
        activites = (fabtrack_data or {}).get('consommations', [])
        machines = (fabtrack_data or {}).get('machines', [])
        missions = (fabtrack_data or {}).get('missions', [])
        compteurs = {
            'interventions_total': summary.get('total_interventions', 0),
            'impression_3d_grammes': summary.get('total_3d_grammes', 0),
            'decoupe_m2': summary.get('total_decoupe_m2', 0),
            'papier_feuilles': summary.get('total_papier_feuilles', 0),
        }

        # ── Calendrier : depuis le cache CalDAV ──
        evenements = []
        caldav_source = db.execute(
            'SELECT id, url, credentials_json FROM sources WHERE type = ? AND actif = 1 ORDER BY id LIMIT 1',
            ('nextcloud_caldav',)
        ).fetchone()
        if caldav_source:
            cal_cached = get_cached_source_data(caldav_source['id'])
            if cal_cached and isinstance(cal_cached, dict):
                evenements = cal_cached.get('events', [])
            else:
                try:
                    from sync_worker import SyncWorker
                    creds = json.loads(caldav_source['credentials_json'] or '{}')
                    cal_data, cal_err = SyncWorker._fetch_caldav_static(
                        caldav_source['url'], creds
                    )
                    if cal_data:
                        evenements = cal_data.get('events', [])
                    elif cal_err:
                        print(f'[CalDAV fallback] {cal_err}')
                except Exception as e:
                    print(f'[CalDAV fallback] Erreur: {e}')

        # ── Imprimantes : depuis le cache Repetier/PrusaLink ──
        imprimantes = []
        for ptype in ('repetier', 'prusalink'):
            printer_source = db.execute(
                'SELECT id FROM sources WHERE type = ? AND actif = 1 ORDER BY id LIMIT 1',
                (ptype,)
            ).fetchone()
            if printer_source:
                pr_cached = get_cached_source_data(printer_source['id'])
                if pr_cached and isinstance(pr_cached, dict):
                    imprimantes.extend(pr_cached.get('printers', []))

        return jsonify({
            'activites': activites,
            'compteurs': compteurs,
            'evenements': evenements,
            'fabtrack_stats': summary,
            'imprimantes': imprimantes,
            'machines': machines,
            'missions': missions,
            'fabtrack_url': fabtrack_url,
            'fabtrack_error': fabtrack_error,
            'timestamp': datetime.now().isoformat(),
        })
    finally:
        db.close()


@bp.route('/api/widget-data/<int:source_id>')
def widget_data(source_id):
    """Récupère les données cachées d'une source pour un widget."""
    from routes.api_sources import get_cached_source_data

    db = get_db()
    try:
        source = db.execute('SELECT id, type, nom, url FROM sources WHERE id = ?', (source_id,)).fetchone()
        if not source:
            return jsonify({'error': 'Source non trouvée'}), 404

        cached = get_cached_source_data(source_id)
        if cached is not None:
            return jsonify({
                'success': True,
                'data': cached,
                'source_type': source['type'],
                'source_nom': source['nom'],
            })

        return jsonify({
            'success': False,
            'error': 'Pas de données en cache. Vérifiez que la source est active et synchronisée.',
            'source_type': source['type'],
            'source_nom': source['nom'],
        }), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# ============================================================
# PARAMÈTRES
# ============================================================

@bp.route('/api/parametres')
def get_parametres():
    """Retourne tous les paramètres."""
    db = get_db()
    try:
        params = _rows_to_list(db.execute('SELECT * FROM parametres').fetchall())
        return jsonify({p['cle']: p['valeur'] for p in params})
    finally:
        db.close()


@bp.route('/api/parametres/<cle>', methods=['PUT'])
def update_parametre(cle):
    """Modifier un paramètre."""
    db = get_db()
    try:
        data = request.get_json()
        valeur = data.get('valeur', '')

        db.execute('''
            INSERT INTO parametres (cle, valeur) VALUES (?, ?)
            ON CONFLICT(cle) DO UPDATE SET valeur = ?
        ''', (cle, valeur, valeur))

        db.commit()
        return jsonify({'success': True, 'cle': cle, 'valeur': valeur})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# ============================================================
# THÈME
# ============================================================

@bp.route('/api/theme', methods=['GET'])
def get_theme_config():
    """Récupère la configuration du thème."""
    try:
        theme = get_theme()
        if not theme:
            return jsonify({'error': 'Thème non trouvé'}), 404
        return jsonify({'success': True, 'data': theme})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/theme', methods=['PUT'])
def update_theme_config():
    """Met à jour le thème."""
    try:
        data = request.get_json()
        update_theme(
            mode=data.get('mode'),
            couleur_primaire=data.get('couleur_primaire'),
            couleur_secondaire=data.get('couleur_secondaire'),
            transition_speed=data.get('transition_speed')
        )
        theme = get_theme()
        return jsonify({'success': True, 'data': theme})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# MÉTÉO (Open-Meteo, sans clé API)
# ============================================================

@bp.route('/api/meteo')
def meteo():
    """Retourne la météo pour une ville via Open-Meteo."""
    ville = request.args.get('ville', '').strip()
    lat = request.args.get('lat', '').strip()
    lon = request.args.get('lon', '').strip()

    if not ville and not (lat and lon):
        return jsonify({'error': 'Paramètre ville ou lat/lon requis'}), 400

    cache_key = ville or f"{lat},{lon}"
    now = datetime.now()

    cached = _meteo_cache.get(cache_key)
    if cached and cached['expires_at'] > now:
        return jsonify({'success': True, 'data': cached['data'], 'cached': True})

    try:
        if ville and not (lat and lon):
            city_name = ville.split(',')[0].strip()
            geo_resp = http_requests.get(
                'https://geocoding-api.open-meteo.com/v1/search',
                params={'name': city_name, 'count': 1, 'language': 'fr'},
                timeout=5
            )
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()
            results = geo_data.get('results', [])
            if not results:
                return jsonify({'error': f'Ville non trouvée: {ville}'}), 404
            lat = str(results[0]['latitude'])
            lon = str(results[0]['longitude'])
            resolved_name = results[0].get('name', city_name)
            country = results[0].get('country', '')
        else:
            resolved_name = ville or 'Position'
            country = ''

        weather_resp = http_requests.get(
            'https://api.open-meteo.com/v1/forecast',
            params={
                'latitude': lat,
                'longitude': lon,
                'current': 'temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m',
                'timezone': 'auto',
            },
            timeout=5
        )
        weather_resp.raise_for_status()
        weather = weather_resp.json()

        current = weather.get('current', {})
        wmo_code = current.get('weather_code', 0)
        desc, icon = _wmo_to_description(wmo_code)

        meteo_data = {
            'temperature': round(current.get('temperature_2m', 0)),
            'humidity': current.get('relative_humidity_2m', 0),
            'wind_speed': round(current.get('wind_speed_10m', 0)),
            'description': desc,
            'icon': icon,
            'ville': resolved_name,
            'pays': country,
            'weather_code': wmo_code,
        }

        _meteo_cache[cache_key] = {
            'data': meteo_data,
            'expires_at': now + timedelta(minutes=15),
        }

        return jsonify({'success': True, 'data': meteo_data})

    except http_requests.RequestException as e:
        return jsonify({'error': f'Erreur météo: {str(e)}'}), 502


# ============================================================
# GIF RESOLVER
# ============================================================

def _extract_gif_url_from_html(html_text, base_url=''):
    """Extrait une URL GIF directe depuis du HTML."""
    if not html_text:
        return ''

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'https://media\.tenor\.com/[^"\'\s>]+?\.gif(?:\?[^"\'\s>]*)?',
        r'https://[^"\'\s>]+?\.gif(?:\?[^"\'\s>]*)?',
    ]

    for pattern in patterns:
        m = re.search(pattern, html_text, flags=re.IGNORECASE)
        if not m:
            continue
        raw_url = m.group(1) if m.lastindex else m.group(0)
        candidate = unescape(raw_url).strip()
        if not candidate:
            continue
        if base_url:
            candidate = urljoin(base_url, candidate)
        parsed = urlparse(candidate)
        if parsed.scheme in ('http', 'https') and '.gif' in candidate.lower():
            return candidate

    return ''


@bp.route('/api/gif/resolve')
def resolve_gif_url():
    """Résout une URL (page/shortlink) vers une URL GIF directe."""
    raw_url = (request.args.get('url') or '').strip()
    if not raw_url:
        return jsonify({'success': False, 'error': 'Paramètre url requis'}), 400

    parsed = urlparse(raw_url)
    if parsed.scheme not in ('http', 'https'):
        return jsonify({'success': False, 'error': 'URL invalide (http/https requis)'}), 400

    from datetime import datetime as dt
    now = dt.utcnow()
    cached = _gif_resolve_cache.get(raw_url)
    if cached and cached.get('expires_at') and cached['expires_at'] > now:
        return jsonify({'success': True, 'url': cached['resolved_url'], 'cached': True})

    headers = {'User-Agent': 'Mozilla/5.0 (FabBoard GIF Resolver)'}

    try:
        resp = http_requests.get(raw_url, headers=headers, timeout=8, allow_redirects=True)
        final_url = resp.url
        content_type = (resp.headers.get('Content-Type') or '').lower()

        if 'image/gif' in content_type or final_url.lower().endswith('.gif'):
            _gif_resolve_cache[raw_url] = {
                'resolved_url': final_url,
                'expires_at': now + timedelta(hours=24),
            }
            return jsonify({'success': True, 'url': final_url, 'resolved': final_url != raw_url})

        html_text = resp.text if 'text/html' in content_type else ''
        extracted = _extract_gif_url_from_html(html_text, final_url)
        if extracted:
            _gif_resolve_cache[raw_url] = {
                'resolved_url': extracted,
                'expires_at': now + timedelta(hours=24),
            }
            return jsonify({'success': True, 'url': extracted, 'resolved': True})

        head = http_requests.head(final_url, headers=headers, timeout=5, allow_redirects=True)
        head_type = (head.headers.get('Content-Type') or '').lower()
        if 'image/gif' in head_type or head.url.lower().endswith('.gif'):
            _gif_resolve_cache[raw_url] = {
                'resolved_url': head.url,
                'expires_at': now + timedelta(hours=24),
            }
            return jsonify({'success': True, 'url': head.url, 'resolved': True})

        return jsonify({
            'success': False,
            'error': 'URL non résolue en GIF direct.',
            'url': raw_url,
        }), 422

    except http_requests.RequestException as e:
        return jsonify({'success': False, 'error': f'Erreur réseau: {str(e)}', 'url': raw_url}), 502


@bp.route('/api/tenor/search')
def tenor_search():
    """Endpoint désactivé - Tenor n'est plus disponible."""
    return jsonify({'error': 'L\'API Tenor n\'est plus disponible.'}), 410


def _wmo_to_description(code):
    """Convertit un code météo WMO en description française et emoji."""
    mapping = {
        0: ('Ciel dégagé', '☀️'),
        1: ('Peu nuageux', '🌤️'),
        2: ('Partiellement nuageux', '⛅'),
        3: ('Couvert', '☁️'),
        45: ('Brouillard', '🌫️'),
        48: ('Brouillard givrant', '🌫️'),
        51: ('Bruine légère', '🌦️'),
        53: ('Bruine modérée', '🌦️'),
        55: ('Bruine forte', '🌧️'),
        61: ('Pluie légère', '🌦️'),
        63: ('Pluie modérée', '🌧️'),
        65: ('Pluie forte', '🌧️'),
        71: ('Neige légère', '🌨️'),
        73: ('Neige modérée', '❄️'),
        75: ('Neige forte', '❄️'),
        80: ('Averses légères', '🌦️'),
        81: ('Averses modérées', '🌧️'),
        82: ('Averses violentes', '🌧️'),
        85: ('Averses de neige', '🌨️'),
        95: ('Orage', '⛈️'),
        96: ('Orage avec grêle', '⛈️'),
        99: ('Orage violent avec grêle', '⛈️'),
    }
    return mapping.get(code, ('Inconnu', '🌤️'))
