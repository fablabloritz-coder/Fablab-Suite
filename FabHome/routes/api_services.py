"""FabHome — Blueprint : API services externes."""

import json
import logging
import ssl
import socket
from urllib.parse import urlparse
from urllib.request import urlopen, Request

from flask import Blueprint, jsonify, request

import models

bp = Blueprint('api_services', __name__)
logger = logging.getLogger(__name__)


@bp.route('/api/services', methods=['POST'])
def api_create_service():
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify(error='Nom requis'), 400
        stype = (data.get('type') or 'generic').strip()[:50]
        url = (data.get('url') or '').strip()[:2000]
        api_key = (data.get('api_key') or '')[:500]
        config = data.get('config', {})
        sid = models.create_service(name[:100], stype, url, api_key, config)
        return jsonify(id=sid), 201
    except Exception as e:
        logger.error(f"Erreur création service: {e}")
        return jsonify(error=f'Erreur: {str(e)}'), 500


@bp.route('/api/services/<int:sid>', methods=['PUT'])
def api_update_service(sid):
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify(error='Nom requis'), 400
    models.update_service(
        sid, name[:100],
        (data.get('type') or 'generic')[:50],
        (data.get('url') or '')[:2000],
        (data.get('api_key') or '')[:500],
        data.get('config', {}),
        1 if data.get('enabled', True) else 0)
    return jsonify(ok=True)


@bp.route('/api/services/<int:sid>', methods=['DELETE'])
def api_delete_service(sid):
    models.delete_service(sid)
    return jsonify(ok=True)


@bp.route('/api/services/<int:sid>/proxy')
def api_service_proxy(sid):
    """Proxy pour interroger un service externe (évite CORS)."""
    services = models.get_services()
    svc = next((s for s in services if s['id'] == sid), None)
    if not svc or not svc['enabled']:
        return jsonify(error='Service non trouvé'), 404
    try:
        svc_url = svc['url'].rstrip('/')
        svc_type = svc.get('type', 'generic')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {'User-Agent': 'FabHome/1.0'}
        if svc.get('api_key'):
            headers['X-Api-Key'] = svc['api_key']

        def _fetch_json(url):
            req = Request(url)
            for k, v in headers.items():
                req.add_header(k, v)
            resp = urlopen(req, timeout=10, context=ctx)
            return json.loads(resp.read().decode())

        # ── Pi-hole : statistiques DNS blocking
        if svc_type == 'pihole':
            result = {'type': 'pihole'}
            try:
                api_key = svc.get('api_key') or ''
                auth = f'&auth={api_key}' if api_key else ''
                data = _fetch_json(f'{svc_url}/admin/api.php?summaryRaw{auth}')
                result['dns_queries_today'] = data.get('dns_queries_today', 0)
                result['ads_blocked_today'] = data.get('ads_blocked_today', 0)
                result['ads_percentage'] = round(float(data.get('ads_percentage_today', 0)), 1)
                result['domains_blocked'] = data.get('domains_being_blocked', 0)
                result['status'] = data.get('status', 'unknown')
            except Exception as e:
                result['error'] = f'Pi-hole API: {e}'
            return jsonify(result)

        # ── AdGuard Home : statistiques DNS blocking
        if svc_type == 'adguard':
            import base64
            result = {'type': 'adguard'}
            try:
                api_key = svc.get('api_key') or ''
                if api_key and ':' in api_key:
                    headers['Authorization'] = 'Basic ' + base64.b64encode(api_key.encode()).decode()
                stats = _fetch_json(f'{svc_url}/control/stats')
                result['num_dns_queries'] = stats.get('num_dns_queries', 0)
                result['num_blocked_filtering'] = stats.get('num_blocked_filtering', 0)
                result['avg_processing_ms'] = round(stats.get('avg_processing_time', 0) * 1000, 2)
                status = _fetch_json(f'{svc_url}/control/status')
                result['running'] = status.get('running', False)
                result['protection_enabled'] = status.get('protection_enabled', False)
            except Exception as e:
                result['error'] = f'AdGuard API: {e}'
            return jsonify(result)

        # ── Uptime Kuma : monitoring
        if svc_type == 'uptimekuma':
            result = {'type': 'uptimekuma'}
            try:
                page = _fetch_json(f'{svc_url}/api/status-page/default')
                groups = page.get('publicGroupList', [])
                monitors = [m for g in groups for m in g.get('monitorList', [])]
                result['total'] = len(monitors)
                result['up'] = sum(1 for m in monitors if m.get('active', False))
                result['down'] = result['total'] - result['up']
            except Exception as e:
                result['error'] = f'Uptime Kuma: {e}'
            return jsonify(result)

        # ── Repetier-Server : imprimantes 3D
        if svc_type == 'repetier':
            result = {'type': 'repetier'}
            try:
                api_key = svc.get('api_key') or ''
                printer_data = _fetch_json(f'{svc_url}/printer/list?apikey={api_key}')
                printers_raw = printer_data.get('data', [])
                result['total'] = len(printers_raw)
                result['online'] = 0
                result['printing'] = 0
                result['printers'] = []
                for p in printers_raw[:8]:
                    slug = p.get('slug', '')
                    info = {
                        'name': p.get('name', slug),
                        'slug': slug,
                        'online': p.get('online', 0) == 1,
                        'active': False,
                        'progress': 0,
                        'job': '',
                        'temp_ext': None,
                        'temp_bed': None,
                    }
                    if info['online']:
                        result['online'] += 1
                        try:
                            state = _fetch_json(f'{svc_url}/printer/api/{slug}?a=stateList&apikey={api_key}')
                            sd = state.get('data', {})
                            ext = sd.get('extruder', [])
                            if ext:
                                info['temp_ext'] = round(ext[0].get('tempRead', 0), 1)
                            beds = sd.get('heatedBeds', [])
                            if beds:
                                info['temp_bed'] = round(beds[0].get('tempRead', 0), 1)
                            info['job'] = sd.get('job', '')
                            info['progress'] = round(sd.get('done', 0), 1)
                            info['active'] = bool(sd.get('active', False))
                            if info['active']:
                                result['printing'] += 1
                        except Exception:
                            pass
                    result['printers'].append(info)
            except Exception as e:
                result['error'] = f'Repetier-Server API: {e}'
            return jsonify(result)

        # ── Docker : containers via API
        if svc_type == 'docker':
            result = {'type': 'docker'}
            try:
                containers = _fetch_json(svc_url + '/containers/json?all=true')
                result['total'] = len(containers)
                result['running'] = len([c for c in containers if c.get('State') == 'running'])
                result['stopped'] = result['total'] - result['running']
                result['containers'] = [
                    {'name': (c.get('Names') or ['/??'])[0].lstrip('/'),
                     'state': c.get('State', ''),
                     'status': c.get('Status', '')}
                    for c in containers[:10]
                ]
            except Exception as e:
                result['error'] = f'Docker API: {e}'
            return jsonify(result)

        # ── Portainer : environments + containers
        if svc_type == 'portainer':
            result = {'type': 'portainer'}
            try:
                headers['Authorization'] = 'Bearer ' + (svc.get('api_key') or '')
                endpoints = _fetch_json(svc_url + '/api/endpoints')
                result['endpoints'] = len(endpoints)
                total_c = 0
                running_c = 0
                for ep in endpoints[:5]:
                    try:
                        snap = ep.get('Snapshots', [{}])[0]
                        total_c += snap.get('DockerSnapshotRaw', {}).get('Containers', snap.get('TotalCPU', 0))
                        running_c += snap.get('RunningContainerCount', 0)
                    except Exception:
                        pass
                result['containers_total'] = total_c
                result['containers_running'] = running_c
            except Exception as e:
                result['error'] = f'Portainer API: {e}'
            return jsonify(result)

        # ── Proxmox : nodes + VMs
        if svc_type == 'proxmox':
            result = {'type': 'proxmox'}
            try:
                headers['Authorization'] = 'PVEAPIToken=' + (svc.get('api_key') or '')
                nodes_data = _fetch_json(svc_url + '/api2/json/nodes')
                nodes = nodes_data.get('data', [])
                result['nodes'] = len(nodes)
                result['node_list'] = [
                    {'name': n.get('node', ''), 'status': n.get('status', ''),
                     'cpu': round(n.get('cpu', 0) * 100, 1)}
                    for n in nodes[:5]
                ]
            except Exception as e:
                result['error'] = f'Proxmox API: {e}'
            return jsonify(result)

        # ── Plex : bibliothèques + sessions
        if svc_type == 'plex':
            result = {'type': 'plex'}
            try:
                headers['X-Plex-Token'] = svc.get('api_key') or ''
                headers['Accept'] = 'application/json'
                libs = _fetch_json(svc_url + '/library/sections')
                sections = libs.get('MediaContainer', {}).get('Directory', [])
                result['libraries'] = len(sections)
                result['library_list'] = [
                    {'title': s.get('title', ''), 'type': s.get('type', '')}
                    for s in sections[:8]
                ]
                try:
                    sess = _fetch_json(svc_url + '/status/sessions')
                    result['active_streams'] = sess.get('MediaContainer', {}).get('size', 0)
                except Exception:
                    result['active_streams'] = 0
            except Exception as e:
                result['error'] = f'Plex API: {e}'
            return jsonify(result)

        # ── Radarr : films
        if svc_type == 'radarr':
            result = {'type': 'radarr'}
            try:
                movies = _fetch_json(svc_url + '/api/v3/movie')
                result['total'] = len(movies)
                result['monitored'] = len([m for m in movies if m.get('monitored')])
                result['has_file'] = len([m for m in movies if m.get('hasFile')])
                result['missing'] = result['monitored'] - result['has_file']
            except Exception as e:
                result['error'] = f'Radarr API: {e}'
            return jsonify(result)

        # ── Sonarr : séries
        if svc_type == 'sonarr':
            result = {'type': 'sonarr'}
            try:
                series = _fetch_json(svc_url + '/api/v3/series')
                result['total'] = len(series)
                result['monitored'] = len([s for s in series if s.get('monitored')])
                total_eps = sum(s.get('statistics', {}).get('totalEpisodeCount', 0) for s in series)
                have_eps = sum(s.get('statistics', {}).get('episodeFileCount', 0) for s in series)
                result['episodes_total'] = total_eps
                result['episodes_have'] = have_eps
            except Exception as e:
                result['error'] = f'Sonarr API: {e}'
            return jsonify(result)

        # ── TrueNAS : pools + alertes
        if svc_type == 'truenas':
            result = {'type': 'truenas'}
            try:
                headers['Authorization'] = 'Bearer ' + (svc.get('api_key') or '')
                pools = _fetch_json(svc_url + '/api/v2.0/pool')
                result['pools'] = len(pools)
                result['pool_list'] = [
                    {'name': p.get('name', ''), 'status': p.get('status', ''),
                     'healthy': p.get('healthy', False)}
                    for p in pools[:5]
                ]
                try:
                    alerts = _fetch_json(svc_url + '/api/v2.0/alert/list')
                    result['alerts'] = len(alerts)
                except Exception:
                    result['alerts'] = 0
            except Exception as e:
                result['error'] = f'TrueNAS API: {e}'
            return jsonify(result)

        # ── Générique / autres types
        endpoint = svc.get('config', {}).get('endpoint', '')
        target = svc_url + endpoint
        data = _fetch_json(target)
        return jsonify(data)
    except Exception as e:
        return jsonify(error=str(e)), 502


@bp.route('/api/services/<int:sid>/test')
def api_service_test(sid):
    """Diagnostic de connectivité pour un service."""
    services = models.get_services()
    svc = next((s for s in services if s['id'] == sid), None)
    if not svc:
        return jsonify(error='Service non trouvé'), 404
    svc_url = svc['url'].rstrip('/')
    result = {'service': svc['name'], 'url': svc_url, 'type': svc.get('type', 'generic')}
    try:
        parsed = urlparse(svc_url)
        host = parsed.hostname
        port = parsed.port or 80
        result['resolved_ip'] = socket.gethostbyname(host)
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        result['tcp_connect'] = True
    except Exception as e:
        result['tcp_connect'] = False
        result['tcp_error'] = str(e)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = Request(svc_url)
        req.add_header('User-Agent', 'FabHome/1.0')
        resp = urlopen(req, timeout=10, context=ctx)
        result['http_status'] = resp.getcode()
        result['http_ok'] = True
    except Exception as e:
        result['http_ok'] = False
        result['http_error'] = str(e)
    return jsonify(result)
