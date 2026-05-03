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


def _parse_hhmm(value):
    """Parse une heure HH:MM en minutes depuis minuit."""
    if not value:
        return None
    if not isinstance(value, str):
        value = str(value)
    parts = value.strip().split(':')
    if len(parts) != 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except (TypeError, ValueError):
        return None
    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        return None
    return (hours * 60) + minutes


def _is_truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _compute_pause_status(now, start_minutes, end_minutes):
    """Retourne (active, next_end_datetime) pour une plage d'indisponibilité quotidienne."""
    if start_minutes is None or end_minutes is None:
        return False, None
    if start_minutes == end_minutes:
        return False, None

    now_minutes = (now.hour * 60) + now.minute

    if start_minutes < end_minutes:
        active = start_minutes <= now_minutes < end_minutes
        if not active:
            return False, None
        end_dt = now.replace(hour=end_minutes // 60, minute=end_minutes % 60, second=0, microsecond=0)
        return True, end_dt

    # Plage traversant minuit (ex: 22:00 -> 06:00)
    active = (now_minutes >= start_minutes) or (now_minutes < end_minutes)
    if not active:
        return False, None

    end_dt = now.replace(hour=end_minutes // 60, minute=end_minutes % 60, second=0, microsecond=0)
    if now_minutes >= start_minutes:
        end_dt = end_dt + timedelta(days=1)
    return True, end_dt


def _default_pause_schedule():
    """Planning hebdomadaire par défaut : pause du lundi au vendredi, 12:00-13:00."""
    return {
        str(day): {
            'enabled': day <= 4,
            'start': '12:00',
            'end': '13:00',
        }
        for day in range(7)
    }


def _load_pause_schedule(raw_value):
    """Charge un planning hebdomadaire JSON et garantit une structure saine."""
    schedule = _default_pause_schedule()
    if not raw_value:
        return schedule

    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return schedule

    if not isinstance(parsed, dict):
        return schedule

    for day in range(7):
        key = str(day)
        slot = parsed.get(key)
        if not isinstance(slot, dict):
            continue
        schedule[key] = {
            'enabled': bool(slot.get('enabled', schedule[key]['enabled'])),
            'start': str(slot.get('start', schedule[key]['start'])),
            'end': str(slot.get('end', schedule[key]['end'])),
        }
    return schedule


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


@bp.route('/api/display/override-status')
def display_override_status():
    """Retourne l'état de l'écran prioritaire (indisponibilité manuelle ou pause hebdomadaire)."""
    db = get_db()
    try:
        rows = _rows_to_list(db.execute('SELECT cle, valeur FROM parametres').fetchall())
        params = {r['cle']: r['valeur'] for r in rows}

        manual_enabled = _is_truthy(params.get('manual_unavailable_enabled', params.get('display_override_enabled', '0')))
        manual_show_return = _is_truthy(params.get('manual_unavailable_show_return', '0'))
        manual_return_time = (params.get('manual_unavailable_return_time') or '').strip()

        mode = (params.get('manual_unavailable_mode') or params.get('display_override_mode') or 'text').strip().lower()
        if mode not in ('text', 'image'):
            mode = 'text'

        pause_enabled = _is_truthy(params.get('pause_schedule_enabled', '0'))
        pause_schedule = _load_pause_schedule(params.get('pause_weekly_schedule', ''))

        active = False
        next_end = None
        resume_label = None
        override_type = None
        source = 'none'

        now = datetime.now()

        if manual_enabled:
            source = 'manual_unavailable'
            active = True
            if manual_show_return and _parse_hhmm(manual_return_time) is not None:
                override_type = 'unavailable_timed'
                resume_label = manual_return_time
            else:
                override_type = 'unavailable'
        elif pause_enabled:
            day_slot = pause_schedule.get(str(now.weekday()), {})
            if day_slot.get('enabled'):
                slot_start = _parse_hhmm(day_slot.get('start'))
                slot_end = _parse_hhmm(day_slot.get('end'))
                active, next_end = _compute_pause_status(now, slot_start, slot_end)
            if active:
                source = 'scheduled_pause'
                override_type = 'pause'
                if next_end:
                    resume_label = next_end.strftime('%H:%M')

        if override_type is None:
            override_type = 'unavailable'

        if source == 'scheduled_pause':
            title = (params.get('pause_title') or 'Pause en cours').strip()
            message = (params.get('pause_message') or '').strip()
            image_url = ''
            bg_color = '#0b1120'
            text_color = '#f8fafc'
            text_scale = params.get('pause_text_scale', '100')
            mode = 'text'
        else:
            title = (params.get('manual_unavailable_title') or params.get('display_override_title') or 'FabLab indisponible').strip()
            message = (params.get('manual_unavailable_message') or params.get('display_override_message') or '').strip()
            image_url = (params.get('manual_unavailable_image_url') or params.get('display_override_image_url') or '').strip()
            bg_color = (params.get('manual_unavailable_bg_color') or params.get('display_override_bg_color') or '#0b1120').strip()
            text_color = (params.get('manual_unavailable_text_color') or params.get('display_override_text_color') or '#f8fafc').strip()
            text_scale = params.get('manual_unavailable_text_scale', '100')

        if source == 'manual_unavailable' and not active:
            title = ''
            message = ''
            image_url = ''

        if source == 'manual_unavailable' and not manual_show_return:
            resume_label = None
            next_end = None

        if source == 'manual_unavailable' and manual_show_return and _parse_hhmm(manual_return_time) is not None:
            next_end = None

        if source == 'scheduled_pause' and not active:
            resume_label = None
            next_end = None

        if source == 'none':
            bg_color = '#0b1120'
            text_color = '#f8fafc'
            title = ''
            message = ''
            image_url = ''

        payload = {
            'success': True,
            'enabled': manual_enabled or pause_enabled,
            'active': active,
            'source': source,
            'override_type': override_type,
            'mode': mode,
            'title': title,
            'message': message,
            'image_url': image_url,
            'bg_color': bg_color,
            'text_color': text_color,
            'text_scale': text_scale,
            'resume_at': next_end.isoformat() if next_end else None,
            'pause_schedule_enabled': pause_enabled,
            'manual_enabled': manual_enabled,
        }

        if resume_label:
            payload['resume_label'] = resume_label

        # Sécurité : fallback texte si mode image sans URL.
        if payload['mode'] == 'image' and not payload['image_url']:
            payload['mode'] = 'text'

        return jsonify(payload)
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
