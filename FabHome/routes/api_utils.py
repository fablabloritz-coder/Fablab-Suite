"""FabHome — Blueprint : API utilitaires (calendrier, favicon, health, status, météo)."""

import json
import logging
import re
import ssl
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from urllib.request import urlopen, Request

from flask import Blueprint, jsonify, request, session

import models
from routes import get_current_profile_id

try:
    import caldav
    CALDAV_AVAILABLE = True
except ImportError:
    CALDAV_AVAILABLE = False

bp = Blueprint('api_utils', __name__)
logger = logging.getLogger(__name__)

_cache = {}


# Calendar events (Nextcloud CalDAV)
@bp.route('/api/calendar/events')
def api_calendar_events():
    """Récupère les événements du calendrier via CalDAV (public ou authentifié)."""
    if not CALDAV_AVAILABLE:
        return jsonify(error="caldav non installé"), 503

    profile_id = get_current_profile_id()
    settings = models.get_settings(profile_id)
    caldav_url = (settings.get('caldav_url', '') or '').strip()
    caldav_username = (settings.get('caldav_username', '') or '').strip()
    caldav_password = (settings.get('caldav_password', '') or '').strip()

    if not caldav_url:
        return jsonify(events=[], message="URL CalDAV non configurée")

    try:
        start = datetime.now()
        end = start + timedelta(days=7)
        all_events = []

        is_public = 'public-calendars' in caldav_url or caldav_url.endswith('?export')

        if is_public:
            import requests as req
            resp = req.get(caldav_url, timeout=10)
            resp.raise_for_status()

            from icalendar import Calendar as iCalendar
            cal = iCalendar.from_ical(resp.text)

            for component in cal.walk():
                if component.name != 'VEVENT':
                    continue
                try:
                    summary = str(component.get('summary', 'Sans titre'))
                    dtstart = component.get('dtstart')
                    location = component.get('location', '')

                    if dtstart:
                        start_dt = dtstart.dt
                        if hasattr(start_dt, 'date'):
                            check_date = start_dt.date()
                        else:
                            check_date = start_dt
                        if check_date < start.date() or check_date > end.date():
                            continue
                        if isinstance(start_dt, datetime):
                            start_str = start_dt.strftime('%d/%m %H:%M')
                        else:
                            start_str = start_dt.strftime('%d/%m')
                    else:
                        start_str = ''

                    all_events.append({
                        'title': summary,
                        'start': start_str,
                        'location': str(location) if location else ''
                    })
                except Exception as e:
                    logging.warning(f"Erreur parsing événement public: {e}")
                    continue
        else:
            if not caldav_username or not caldav_password:
                return jsonify(events=[], message="Identifiants CalDAV manquants pour ce type d'URL")

            client = caldav.DAVClient(
                url=caldav_url,
                username=caldav_username,
                password=caldav_password
            )
            principal = client.principal()
            calendars = principal.calendars()

            if not calendars:
                return jsonify(events=[], message="Aucun calendrier trouvé")

            for calendar in calendars:
                try:
                    events = calendar.date_search(start=start, end=end)
                    for event in events:
                        try:
                            vevent = event.icalendar_component
                            summary = str(vevent.get('summary', 'Sans titre'))
                            dtstart = vevent.get('dtstart')
                            location = vevent.get('location', '')

                            if dtstart:
                                start_dt = dtstart.dt
                                if isinstance(start_dt, datetime):
                                    start_str = start_dt.strftime('%d/%m %H:%M')
                                else:
                                    start_str = start_dt.strftime('%d/%m')
                            else:
                                start_str = ''

                            all_events.append({
                                'title': summary,
                                'start': start_str,
                                'location': str(location) if location else ''
                            })
                        except Exception as e:
                            logging.warning(f"Erreur parsing événement: {e}")
                            continue
                except Exception as e:
                    logging.warning(f"Erreur récupération calendrier: {e}")
                    continue

        all_events.sort(key=lambda x: x['start'])
        return jsonify(events=all_events[:10])

    except Exception as e:
        logging.error(f"Erreur CalDAV: {e}")
        return jsonify(error=str(e)), 500


# Proxy favicon
@bp.route('/api/favicon')
def api_favicon():
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify(error='URL manquante'), 400
    try:
        parsed = urlparse(url if '://' in url else 'https://' + url)
        domain = parsed.hostname
        if not domain:
            return jsonify(error='Domaine invalide'), 400

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        base = f"{parsed.scheme}://{domain}"

        # 1. Parser la page HTML pour trouver la meilleure icône
        try:
            page_url = url if '://' in url else 'https://' + url
            req = Request(page_url)
            for k, v in headers.items():
                req.add_header(k, v)
            response = urlopen(req, timeout=5, context=ctx)
            html = response.read(100000).decode('utf-8', errors='ignore')

            best_icon = None
            best_size = 0

            link_tags = re.findall(r'<link\s+[^>]*?>', html, re.IGNORECASE | re.DOTALL)
            for tag in link_tags:
                rel_match = re.search(r'rel=["\']([^"\']*)["\']', tag, re.IGNORECASE)
                if not rel_match:
                    continue
                rel_val = rel_match.group(1).lower()
                if 'icon' not in rel_val and 'apple-touch' not in rel_val:
                    continue
                href_match = re.search(r'href=["\']([^"\']*)["\']', tag, re.IGNORECASE)
                if not href_match:
                    continue
                href = href_match.group(1).strip()
                if not href or href.startswith('data:'):
                    continue

                size = 0
                sizes_match = re.search(r'sizes=["\'](\d+)x\d+["\']', tag, re.IGNORECASE)
                if sizes_match:
                    size = int(sizes_match.group(1))
                elif 'apple-touch' in rel_val:
                    size = 180
                elif href.endswith('.svg'):
                    size = 512

                if href.startswith('http'):
                    full_url = href
                elif href.startswith('//'):
                    full_url = f"{parsed.scheme}:{href}"
                elif href.startswith('/'):
                    full_url = f"{base}{href}"
                else:
                    full_url = f"{base}/{href}"

                if size > best_size or best_icon is None:
                    best_icon = full_url
                    best_size = size

            if best_icon:
                return jsonify(icon=best_icon)

        except Exception:
            pass

        # 2. Essayer /favicon.ico direct
        try:
            favicon_url = f"{base}/favicon.ico"
            req = Request(favicon_url)
            for k, v in headers.items():
                req.add_header(k, v)
            response = urlopen(req, timeout=3, context=ctx)
            if response.getcode() == 200:
                ct = response.headers.get('Content-Type', '')
                if 'image' in ct or 'octet' in ct or ct == '':
                    return jsonify(icon=favicon_url)
        except Exception:
            pass

        # 3. Essayer /apple-touch-icon.png
        try:
            apple_url = f"{base}/apple-touch-icon.png"
            req = Request(apple_url)
            for k, v in headers.items():
                req.add_header(k, v)
            response = urlopen(req, timeout=3, context=ctx)
            if response.getcode() == 200:
                return jsonify(icon=apple_url)
        except Exception:
            pass

        # 4. Fallback: Google favicon service
        return jsonify(icon=f'https://www.google.com/s2/favicons?domain={domain}&sz=64')

    except Exception as e:
        logger.debug(f"Erreur récupération favicon: {e}")
        return jsonify(error='Erreur'), 400


# Santé serveur
@bp.route('/api/health')
def api_health():
    try:
        import psutil
        return jsonify(
            cpu=psutil.cpu_percent(interval=0.5),
            ram=psutil.virtual_memory().percent,
            disk=psutil.disk_usage('/').percent
        )
    except ImportError:
        return jsonify(error='psutil non installé'), 501


@bp.route('/api/status')
def api_status():
    groups = models.get_groups()
    results = {}
    now = time.time()
    for g in groups:
        for lnk in g['links']:
            if lnk['check_status']:
                ck = f"status:{lnk['id']}"
                cached = _cache.get(ck)
                if cached and now - cached['ts'] < 120:
                    results[lnk['id']] = cached['val']
                else:
                    val = _ping(lnk['url'])
                    _cache[ck] = {'val': val, 'ts': now}
                    results[lnk['id']] = val
    return jsonify(results)


def _ping(url):
    """Vérifie si une URL est accessible. Retourne 'up', 'down' ou 'unknown'."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return 'unknown'

        try:
            req = Request(url, method='HEAD')
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FabHome/1.0')
            req.add_header('Accept', '*/*')
            response = urlopen(req, timeout=8, context=ctx)
            if 200 <= response.getcode() < 400:
                return 'up'
        except Exception:
            pass

        try:
            req = Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) FabHome/1.0')
            req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
            response = urlopen(req, timeout=8, context=ctx)
            response.read(512)
            if 200 <= response.getcode() < 400:
                return 'up'
        except Exception as e:
            if 'timed out' in str(e).lower() or 'Connection refused' in str(e):
                return 'down'
            return 'unknown'

        return 'down'
    except Exception as e:
        logger.debug(f"Erreur ping {url}: {e}")
        return 'unknown'


# ── API : Météo ───────────────────────────────────────────

@bp.route('/api/weather')
def api_weather():
    profile_id = get_current_profile_id()
    widgets = {w['type']: w for w in models.get_widgets(profile_id)}
    ww = widgets.get('weather')
    if not ww or not ww['enabled']:
        return jsonify(error='Widget météo désactivé'), 404
    cfg = ww['config']
    lat = float(cfg.get('latitude', 48.69))
    lon = float(cfg.get('longitude', 6.18))
    cache_key = f"weather:{lat}:{lon}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached['ts'] < 1800:
        return jsonify(cached['val'])
    try:
        api_url = (f"https://api.open-meteo.com/v1/forecast?"
                   f"latitude={lat}&longitude={lon}"
                   f"&current=temperature_2m,weather_code&timezone=auto")
        req = Request(api_url)
        req.add_header('User-Agent', 'FabHome/1.0')
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        result = {
            'temperature': data['current']['temperature_2m'],
            'weather_code': data['current']['weather_code'],
            'city': cfg.get('city', ''),
        }
        _cache[cache_key] = {'val': result, 'ts': now}
        return jsonify(result)
    except Exception as e:
        logger.warning("Erreur météo : %s", e)
        return jsonify(error='Erreur API météo'), 502
