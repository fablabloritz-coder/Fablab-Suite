"""FabHome — Blueprint : API FabLab Suite hub (enregistrement, proxy, dashboard)."""

import json
import logging
import ssl
from urllib.request import urlopen, Request

from flask import Blueprint, jsonify, request

import models

bp = Blueprint('api_suite', __name__)
logger = logging.getLogger(__name__)


def _fetch_manifest(base_url):
    """Récupère le manifest FabSuite d'une app distante."""
    url = base_url.rstrip('/') + '/api/fabsuite/manifest'
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url, headers={'Accept': 'application/json'})
    resp = urlopen(req, timeout=5, context=ctx)
    return json.loads(resp.read().decode('utf-8'))


def _check_health(base_url):
    """Vérifie le health check d'une app suite."""
    url = base_url.rstrip('/') + '/api/fabsuite/health'
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url, headers={'Accept': 'application/json'})
    resp = urlopen(req, timeout=3, context=ctx)
    data = json.loads(resp.read().decode('utf-8'))
    return data.get('status') == 'ok'


def _fetch_widget_data(base_url, endpoint):
    """Récupère les données d'un widget depuis une app suite."""
    url = base_url.rstrip('/') + endpoint
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url, headers={'Accept': 'application/json'})
    resp = urlopen(req, timeout=5, context=ctx)
    return json.loads(resp.read().decode('utf-8'))


def _extract_notifications(payload):
    """Normalise le format des notifications retournées par les apps."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get('notifications'), list):
        return payload.get('notifications')
    if isinstance(payload.get('items'), list):
        return payload.get('items')
    if isinstance(payload.get('data'), list):
        return payload.get('data')
    return []


def _browser_safe_url(base_url):
    """URL utilisable dans un navigateur local (Windows/Linux/Mac)."""
    return (base_url or '').replace('host.docker.internal', 'localhost')


def _check_health_endpoint(base_url, endpoint):
    """Teste un endpoint health JSON et retourne (ok, payload)."""
    url = base_url.rstrip('/') + endpoint
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = Request(url, headers={'Accept': 'application/json'})
    resp = urlopen(req, timeout=4, context=ctx)
    payload = json.loads(resp.read().decode('utf-8'))
    status = str(payload.get('status', '')).lower()
    ok = status in ('ok', 'healthy', 'up')
    return ok, payload


@bp.route('/api/suite/apps')
def api_suite_list():
    """Liste toutes les apps FabLab Suite enregistrées."""
    return jsonify(models.get_suite_apps())


@bp.route('/api/suite/apps', methods=['POST'])
def api_suite_register():
    """Enregistre une app par son URL (fetch le manifest)."""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify(error='URL requise'), 400
    if not url.startswith('http'):
        url = 'http://' + url
    try:
        manifest = _fetch_manifest(url)
    except Exception as e:
        return jsonify(error=f'Impossible de contacter {url} : {e}'), 400
    if not manifest.get('app'):
        return jsonify(error='Manifest invalide (champ "app" manquant)'), 400
    aid = models.create_suite_app(url, manifest)
    return jsonify(ok=True, id=aid, app=manifest), 201


@bp.route('/api/suite/apps/<int:app_id>', methods=['DELETE'])
def api_suite_delete(app_id):
    """Supprime une app de la suite."""
    models.delete_suite_app(app_id)
    return jsonify(ok=True)


@bp.route('/api/suite/apps/refresh', methods=['POST'])
def api_suite_refresh_all():
    """Rafraîchit les manifests de toutes les apps."""
    apps = models.get_suite_apps()
    results = []
    for a in apps:
        if not a['enabled']:
            continue
        try:
            manifest = _fetch_manifest(a['url'])
            models.update_suite_app_manifest(a['id'], manifest)
            results.append({'id': a['id'], 'app': a['app_id'], 'status': 'ok'})
        except Exception as e:
            models.update_suite_app_status(a['id'], 'unreachable', str(e))
            results.append({'id': a['id'], 'app': a['app_id'], 'status': 'error', 'error': str(e)})
    return jsonify(results)


@bp.route('/api/suite/apps/<int:app_id>/widget/<widget_id>')
def api_suite_widget_data(app_id, widget_id):
    """Proxy vers un widget d'une app suite."""
    a = models.get_suite_app(app_id)
    if not a:
        return jsonify(error='App non trouvée'), 404
    endpoint = None
    for w in a.get('widgets_json', []):
        if w.get('id') == widget_id:
            endpoint = w.get('endpoint')
            break
    if not endpoint:
        return jsonify(error='Widget non trouvé'), 404
    try:
        data = _fetch_widget_data(a['url'], endpoint)
        return jsonify(data)
    except Exception as e:
        return jsonify(error=str(e)), 502


@bp.route('/api/suite/notifications')
def api_suite_notifications():
    """Agrège les notifications de toutes les apps suite."""
    apps = models.get_suite_apps()
    all_notifs = []
    for a in apps:
        if not a['enabled']:
            continue
        endpoint = a.get('notifications_endpoint') or '/api/fabsuite/notifications'
        try:
            data = _fetch_widget_data(a['url'], endpoint)
            app_notifs = _extract_notifications(data)
            app_notifs.sort(key=lambda n: n.get('created_at', ''), reverse=True)
            # Évite qu'une app très bavarde masque les autres dans la topbar.
            for n in app_notifs[:25]:
                n['source_app'] = a['app_id']
                n['source_name'] = a['name']
                n['source_color'] = a['color']
                all_notifs.append(n)
        except Exception as exc:
            logger.warning("Notifications indisponibles pour %s (%s): %s", a.get('name'), a.get('url'), exc)
    all_notifs.sort(key=lambda n: n.get('created_at', ''), reverse=True)
    return jsonify({"notifications": all_notifs})


@bp.route('/api/suite/dashboard')
def api_suite_dashboard():
    """Données complètes du dashboard suite : apps + widgets + notifications."""
    apps = models.get_suite_apps()
    dashboard = []
    for a in apps:
        if not a['enabled']:
            continue
        app_data = {
            'id': a['id'],
            'app_id': a['app_id'],
            'name': a['name'],
            'version': a['version'],
            'icon': a['icon'],
            'color': a['color'],
            'url': a['url'],
            'status': a['status'],
            'description': a['description'],
            'last_seen': a['last_seen'],
            'widgets': []
        }
        for w in a.get('widgets_json', [])[:2]:
            try:
                wdata = _fetch_widget_data(a['url'], w['endpoint'])
                wdata['_meta'] = {'id': w['id'], 'label': w['label'], 'type': w['type']}
                app_data['widgets'].append(wdata)
            except Exception:
                app_data['widgets'].append({
                    '_meta': {'id': w['id'], 'label': w['label'], 'type': w['type']},
                    'error': True
                })
        dashboard.append(app_data)
    return jsonify(dashboard)


@bp.route('/api/suite/test-url', methods=['POST'])
def api_suite_test_url():
    """Teste l'accessibilité d'une URL d'app FabLab Suite côté backend (anti-CORS)."""
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify(error='URL requise'), 400
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    checks = [
        ('/api/fabsuite/health', 'fabsuite'),
        ('/api/health', 'generic'),
    ]
    last_error = None

    for endpoint, mode in checks:
        try:
            ok, payload = _check_health_endpoint(url, endpoint)
            if ok:
                return jsonify({
                    'ok': True,
                    'status': 'ok',
                    'url': url,
                    'browser_url': _browser_safe_url(url),
                    'endpoint': endpoint,
                    'mode': mode,
                    'payload': payload,
                })
            last_error = f'Statut non OK via {endpoint}'
        except Exception as exc:
            last_error = str(exc)

    return jsonify({
        'ok': False,
        'status': 'error',
        'url': url,
        'browser_url': _browser_safe_url(url),
        'error': last_error or 'FabBoard non accessible',
    }), 502
