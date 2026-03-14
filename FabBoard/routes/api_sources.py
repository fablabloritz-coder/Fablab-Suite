"""
FabBoard — API Sources + Cache (blueprint)
CRUD sources de données, cache, sync worker
"""

from flask import Blueprint, request, jsonify
from models import get_db
import json
import os
import requests as http_requests
from urllib.parse import quote, urlparse
from datetime import datetime

bp = Blueprint('api_sources', __name__)


# ============================================================
# HELPERS (partagés dans ce blueprint)
# ============================================================

def _normalize_base_url(url):
    """Normalise une URL de base en supprimant le slash final."""
    if not url:
        return ''
    return url.strip().rstrip('/')


def _is_running_in_docker():
    return os.path.exists('/.dockerenv')


def _is_localhost_url(url):
    try:
        host = (urlparse(str(url)).hostname or '').lower()
    except Exception:
        return False
    return host in ('localhost', '127.0.0.1', '::1')


def _default_fabtrack_url():
    from_env = _normalize_base_url(os.environ.get('FABTRACK_URL', ''))
    if from_env:
        return from_env
    if _is_running_in_docker():
        return 'http://host.docker.internal:5555'
    return 'http://localhost:5555'


def _get_active_source_url(source_type):
    """Retourne l'URL d'une source active par type, sinon chaîne vide."""
    db = get_db()
    try:
        row = db.execute(
            'SELECT url FROM sources WHERE type = ? AND actif = 1 ORDER BY id LIMIT 1',
            (source_type,),
        ).fetchone()
        return _normalize_base_url(row['url']) if row else ''
    finally:
        db.close()


def _resolve_fabtrack_base_url():
    """Résout l'URL de Fabtrack via sources DB puis variable d'environnement."""
    from_db = _get_active_source_url('fabtrack')
    fallback = _default_fabtrack_url()

    if from_db:
        if _is_localhost_url(from_db) and fallback and not _is_localhost_url(fallback):
            return fallback
        return from_db
    return fallback


def _request_json(base_url, path, timeout=4):
    """Exécute une requête GET JSON et retourne (ok, data, erreur)."""
    url = f"{_normalize_base_url(base_url)}{path}"
    try:
        response = http_requests.get(url, timeout=timeout)
        response.raise_for_status()
        return True, response.json(), ''
    except http_requests.ConnectionError:
        return False, None, 'Service non disponible'
    except http_requests.Timeout:
        return False, None, 'Délai de réponse dépassé'
    except http_requests.RequestException as e:
        return False, None, str(e)


def _extract_fabtrack_payload(base_url):
    """Agrège les données nécessaires au dashboard depuis Fabtrack."""
    base_url = _normalize_base_url(base_url)
    candidates = [base_url] if base_url else []
    fallback = _default_fabtrack_url()
    if fallback and fallback not in candidates:
        if not base_url or _is_localhost_url(base_url):
            candidates.append(fallback)

    last_error = ''
    for candidate in candidates:
        ok_summary, summary, err_summary = _request_json(candidate, '/api/stats/summary')
        ok_conso, conso, err_conso = _request_json(candidate, '/api/consommations?per_page=5&page=1')
        ok_ref, reference, _ = _request_json(candidate, '/api/reference')
        ok_missions, missions_payload, _ = _request_json(candidate, '/missions/api/list')

        if not ok_summary and not ok_conso:
            last_error = err_summary or err_conso or 'Service non disponible'
            continue

        summary = summary or {}
        conso = conso or {}
        reference = reference or {}

        machines = []
        if ok_ref and isinstance(reference, dict):
            for machine in (reference.get('machines') or []):
                machines.append({
                    'id': machine.get('id'),
                    'nom': machine.get('nom', 'Machine'),
                    'statut': machine.get('statut', 'inconnu'),
                    'actif': machine.get('actif', 1),
                })

        compteurs = {
            'interventions_total': summary.get('total_interventions', 0),
            'impression_3d_grammes': summary.get('total_3d_grammes', 0),
            'decoupe_m2': summary.get('total_decoupe_m2', 0),
            'papier_feuilles': summary.get('total_papier_feuilles', 0),
        }

        missions = []
        if ok_missions and isinstance(missions_payload, dict):
            missions = missions_payload.get('data', []) or []

        return {
            'compteurs': compteurs,
            'fabtrack_stats': summary,
            'activites': conso.get('data', []),
            'machines': machines,
            'missions': missions,
            'source_url': candidate,
        }, ''

    return None, f"Fabtrack indisponible: {last_error or 'Service non disponible'}"


SUPPORTED_SOURCE_TYPES = {
    'fabtrack': {
        'label': 'Fabtrack',
        'description': 'Statistiques et consommations depuis Fabtrack',
        'default_url': _default_fabtrack_url(),
    },
    'repetier': {
        'label': 'Repetier Server',
        'description': 'Etat des imprimantes 3D via API Repetier',
        'default_url': 'http://localhost:3344',
    },
    'nextcloud_caldav': {
        'label': 'Nextcloud CalDAV',
        'description': 'Evenements calendrier depuis Nextcloud',
        'default_url': 'https://cloud.exemple.fr/remote.php/dav/calendars/user/calendrier',
    },
    'prusalink': {
        'label': 'PrusaLink',
        'description': 'Etat des imprimantes Prusa via PrusaLink',
        'default_url': 'http://localhost:8080',
    },
    'openweathermap': {
        'label': 'OpenWeatherMap',
        'description': 'Donnees meteo pour widget meteo',
        'default_url': 'https://api.openweathermap.org',
    },
    'rss': {
        'label': 'Flux RSS',
        'description': 'Flux RSS/Atom externe',
        'default_url': 'https://example.com/feed.xml',
    },
    'http': {
        'label': 'HTTP/REST',
        'description': 'Endpoint HTTP generique',
        'default_url': 'https://api.example.com/data',
    },
}


def _decode_source_credentials(credentials_json):
    """Decode credentials JSON safely."""
    if not credentials_json:
        return {}
    try:
        parsed = json.loads(credentials_json)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _serialize_source_public(source_row):
    """Return a safe public source object without secrets."""
    source = dict(source_row)
    credentials = _decode_source_credentials(source.get('credentials_json'))
    has_credentials = any(str(v).strip() for v in credentials.values())

    source['credentials_json'] = '***' if has_credentials else '{}'
    source['has_credentials'] = has_credentials

    if source.get('derniere_erreur'):
        source['status'] = 'error'
    elif source.get('derniere_sync'):
        source['status'] = 'ok'
    else:
        source['status'] = 'never'

    return source


def _coerce_source_payload(data, existing=None):
    """Validate and normalize source payload for create/update."""
    if not isinstance(data, dict):
        return None, 'Payload JSON invalide'

    payload = {}

    if existing is None or 'nom' in data:
        nom = str(data.get('nom', '')).strip()
        if not nom:
            return None, "Le champ 'nom' est requis"
        payload['nom'] = nom
    else:
        payload['nom'] = existing['nom']

    if existing is None or 'type' in data:
        source_type = str(data.get('type', '')).strip().lower()
        if source_type not in SUPPORTED_SOURCE_TYPES:
            allowed = ', '.join(sorted(SUPPORTED_SOURCE_TYPES.keys()))
            return None, f"Type invalide. Types autorises: {allowed}"
        payload['type'] = source_type
    else:
        payload['type'] = existing['type']

    if existing is None or 'url' in data:
        url = _normalize_base_url(str(data.get('url', '')).strip())
        if not url:
            return None, "Le champ 'url' est requis"
        if not (url.startswith('http://') or url.startswith('https://')):
            return None, "L'URL doit commencer par http:// ou https://"

        # En Docker, localhost ne pointe pas Fabtrack mais le conteneur FabBoard.
        if payload.get('type') == 'fabtrack' and _is_localhost_url(url):
            fallback_url = _default_fabtrack_url()
            if fallback_url and not _is_localhost_url(fallback_url):
                url = fallback_url

        payload['url'] = url
    else:
        payload['url'] = existing['url']

    raw_interval = data.get('sync_interval_sec', existing['sync_interval_sec'] if existing else 60)
    try:
        sync_interval = int(raw_interval)
    except (TypeError, ValueError):
        return None, "'sync_interval_sec' doit etre un entier"

    if sync_interval < 10 or sync_interval > 3600:
        return None, "'sync_interval_sec' doit etre compris entre 10 et 3600"
    payload['sync_interval_sec'] = sync_interval

    raw_actif = data.get('actif', existing['actif'] if existing else 1)
    if isinstance(raw_actif, bool):
        payload['actif'] = 1 if raw_actif else 0
    else:
        try:
            payload['actif'] = 1 if int(raw_actif) == 1 else 0
        except (TypeError, ValueError):
            payload['actif'] = 0

    if 'credentials' in data:
        credentials = data.get('credentials') or {}
        if not isinstance(credentials, dict):
            return None, "Le champ 'credentials' doit etre un objet JSON"
    elif existing:
        credentials = _decode_source_credentials(existing.get('credentials_json'))
    else:
        credentials = {}

    payload['credentials_json'] = json.dumps(credentials)
    return payload, ''


def get_cached_source_data(source_id):
    """Récupère les données en cache pour une source."""
    db = get_db()
    try:
        row = db.execute(
            'SELECT data_json, expires_at FROM sources_cache WHERE source_id = ?',
            (source_id,)
        ).fetchone()

        if not row:
            return None

        # Ne pas utiliser de cache expiré, sinon les widgets peuvent afficher un état obsolète.
        expires_at = row['expires_at']
        if expires_at:
            try:
                exp = datetime.fromisoformat(str(expires_at).replace('Z', ''))
                if datetime.now() >= exp:
                    db.execute('DELETE FROM sources_cache WHERE source_id = ?', (source_id,))
                    db.commit()
                    return None
            except (ValueError, TypeError):
                # Format invalide: on considère le cache invalide par sécurité.
                db.execute('DELETE FROM sources_cache WHERE source_id = ?', (source_id,))
                db.commit()
                return None

        return json.loads(row['data_json'])
    except Exception as e:
        print(f'[Cache] Erreur lecture cache source {source_id}: {e}')
        return None
    finally:
        db.close()


# ============================================================
# ROUTES
# ============================================================

@bp.route('/api/sources/by-type/<source_type>')
def sources_by_type(source_type):
    """Liste les sources actives d'un type donné."""
    db = get_db()
    try:
        rows = db.execute(
            'SELECT id, nom, type, url, actif, derniere_sync, derniere_erreur FROM sources WHERE type = ? ORDER BY actif DESC, nom',
            (source_type,)
        ).fetchall()
        sources = [dict(r) for r in rows]
        return jsonify({'success': True, 'data': sources})
    finally:
        db.close()


@bp.route('/api/sources')
def get_sources():
    """Liste toutes les sources de données configurées."""
    db = get_db()
    try:
        rows = db.execute('SELECT * FROM sources ORDER BY actif DESC, nom').fetchall()
        sources = [_serialize_source_public(row) for row in rows]
        return jsonify({'success': True, 'data': sources})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/sources/types', methods=['GET'])
def get_source_types():
    """Liste les types de sources supportés et leurs métadonnées."""
    data = [
        {
            'code': code,
            'label': meta['label'],
            'description': meta['description'],
            'default_url': meta['default_url'],
        }
        for code, meta in SUPPORTED_SOURCE_TYPES.items()
    ]
    return jsonify({'success': True, 'data': data})


@bp.route('/api/sources', methods=['POST'])
def create_source():
    """Créer une nouvelle source de données."""
    db = get_db()
    try:
        data = request.get_json() or {}
        payload, error = _coerce_source_payload(data)
        if error:
            return jsonify({'error': error}), 400

        cursor = db.execute('''
            INSERT INTO sources (nom, type, url, credentials_json, sync_interval_sec, actif)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            payload['nom'],
            payload['type'],
            payload['url'],
            payload['credentials_json'],
            payload['sync_interval_sec'],
            payload['actif']
        ))

        db.commit()
        source_id = cursor.lastrowid

        source = db.execute('SELECT * FROM sources WHERE id = ?', (source_id,)).fetchone()
        return jsonify({'success': True, 'data': _serialize_source_public(source)}), 201
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/sources/<int:id>', methods=['PUT'])
def update_source(id):
    """Modifier une source de données."""
    db = get_db()
    try:
        data = request.get_json() or {}

        existing = db.execute('SELECT * FROM sources WHERE id = ?', (id,)).fetchone()
        if not existing:
            return jsonify({'error': 'Source non trouvée'}), 404

        existing = dict(existing)
        payload, error = _coerce_source_payload(data, existing=existing)
        if error:
            return jsonify({'error': error}), 400

        db.execute('''
            UPDATE sources SET
                nom = ?, type = ?, url = ?, credentials_json = ?,
                sync_interval_sec = ?, actif = ?
            WHERE id = ?
        ''', (
            payload['nom'],
            payload['type'],
            payload['url'],
            payload['credentials_json'],
            payload['sync_interval_sec'],
            payload['actif'],
            id
        ))

        db.commit()

        source = db.execute('SELECT * FROM sources WHERE id = ?', (id,)).fetchone()
        return jsonify({'success': True, 'data': _serialize_source_public(source)})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/sources/<int:id>', methods=['DELETE'])
def delete_source(id):
    """Supprimer une source de données."""
    db = get_db()
    try:
        result = db.execute('DELETE FROM sources WHERE id = ?', (id,))
        db.commit()
        if result.rowcount == 0:
            return jsonify({'error': 'Source non trouvée'}), 404
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/sources/<int:id>/test', methods=['POST'])
def test_source(id):
    """Teste la connectivité d'une source configurée."""
    db = get_db()
    try:
        source = db.execute('SELECT * FROM sources WHERE id = ?', (id,)).fetchone()
        if not source:
            return jsonify({'error': 'Source non trouvée'}), 404

        source = dict(source)
        base_url = _normalize_base_url(source.get('url', ''))
        credentials = _decode_source_credentials(source.get('credentials_json'))

        def _mark_test_result(success, error_message=''):
            if success:
                db.execute(
                    "UPDATE sources SET derniere_sync = datetime('now','localtime'), derniere_erreur = '' WHERE id = ?",
                    (id,),
                )
            else:
                db.execute(
                    "UPDATE sources SET derniere_erreur = ? WHERE id = ?",
                    (error_message[:500], id),
                )
            db.commit()

        if source['type'] == 'fabtrack':
            ok, data, err = _request_json(base_url, '/api/stats/summary')
            tested_url = base_url

            # Auto-réparation: source historique en localhost dans un contexte Docker.
            if not ok and _is_localhost_url(base_url):
                fallback_url = _default_fabtrack_url()
                if fallback_url and fallback_url != base_url:
                    ok_fb, data_fb, err_fb = _request_json(fallback_url, '/api/stats/summary')
                    if ok_fb:
                        tested_url = fallback_url
                        ok = True
                        data = data_fb
                        err = ''
                        db.execute('UPDATE sources SET url = ? WHERE id = ?', (fallback_url, id))
                    else:
                        err = err_fb or err

            if not ok:
                _mark_test_result(False, err)
                return jsonify({'success': False, 'error': err, 'url': tested_url}), 400

            _mark_test_result(True)
            payload = {
                'success': True,
                'url': tested_url,
                'summary': {
                    'total_interventions': data.get('total_interventions', 0),
                    'total_3d_grammes': data.get('total_3d_grammes', 0),
                    'total_decoupe_m2': data.get('total_decoupe_m2', 0),
                },
            }
            if tested_url != base_url:
                payload['message'] = f"URL corrigée automatiquement vers {tested_url}"
            return jsonify(payload)

        if source['type'] == 'openweathermap':
            apikey = credentials.get('apikey') or credentials.get('api_key')
            city = str(credentials.get('city') or 'Nancy,FR').strip()

            if not apikey:
                error = "Credential manquant: apikey requis pour OpenWeatherMap"
                _mark_test_result(False, error)
                return jsonify({'success': False, 'error': error}), 400

            path = f"/data/2.5/weather?q={quote(city)}&appid={quote(str(apikey))}&units=metric&lang=fr"
            ok, data, err = _request_json(base_url, path)
            if not ok:
                _mark_test_result(False, err)
                return jsonify({'success': False, 'error': err, 'url': base_url}), 400

            _mark_test_result(True)
            return jsonify({
                'success': True,
                'url': base_url,
                'summary': {
                    'city': data.get('name', city),
                    'temperature': data.get('main', {}).get('temp'),
                    'conditions': (data.get('weather') or [{}])[0].get('description', ''),
                },
            })

        # Test HTTP générique pour les autres types
        headers = {}
        if credentials.get('apikey'):
            headers['Authorization'] = f"Bearer {credentials['apikey']}"

        auth_user = credentials.get('username') or credentials.get('user')
        auth_pass = credentials.get('password') or credentials.get('pass')
        auth = (auth_user, auth_pass) if auth_user and auth_pass else None

        try:
            response = http_requests.get(
                base_url,
                timeout=6,
                headers=headers or None,
                auth=auth,
            )
            if response.status_code >= 400:
                err = f"HTTP {response.status_code}"
                _mark_test_result(False, err)
                return jsonify({'success': False, 'error': err, 'url': base_url}), 400

            _mark_test_result(True)
            return jsonify({
                'success': True,
                'url': base_url,
                'summary': {
                    'status_code': response.status_code,
                    'type': source['type'],
                },
            })
        except http_requests.RequestException as e:
            err = str(e)
            _mark_test_result(False, err)
            return jsonify({'success': False, 'error': err, 'url': base_url}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@bp.route('/api/sources/<int:id>/resync', methods=['POST'])
def resync_source(id):
    """Force une re-synchronisation immédiate d'une source."""
    db = get_db()
    try:
        source = db.execute('SELECT id FROM sources WHERE id = ?', (id,)).fetchone()
        if not source:
            return jsonify({'error': 'Source non trouvée'}), 404

        db.execute("UPDATE sources SET derniere_sync = NULL WHERE id = ?", (id,))
        db.execute('DELETE FROM sources_cache WHERE source_id = ?', (id,))
        db.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# ============================================================
# CACHE ET WORKER
# ============================================================

@bp.route('/api/worker/status', methods=['GET'])
def worker_status():
    """Retourne l'état du sync worker et des caches sources."""
    from sync_worker import get_sync_worker, start_sync_worker

    try:
        worker = get_sync_worker()

        if not worker:
            worker = start_sync_worker(poll_interval=10)

        db = get_db()

        sources = db.execute(
            'SELECT id, nom, type, actif, sync_interval_sec, derniere_sync, derniere_erreur FROM sources ORDER BY id'
        ).fetchall()

        sources_status = []
        for source in sources:
            source_dict = dict(source)

            cache_row = db.execute(
                'SELECT expires_at, fetched_at FROM sources_cache WHERE source_id = ?',
                (source['id'],)
            ).fetchone()

            source_dict['cache_valid'] = cache_row is not None
            if cache_row:
                source_dict['cache_expires_at'] = cache_row['expires_at']
                source_dict['cache_fetched_at'] = cache_row['fetched_at']

            sources_status.append(source_dict)

        db.close()

        worker_info = 'None'
        worker_running = False
        worker_poll_interval = None

        if worker:
            worker_running = worker.running
            worker_poll_interval = worker.poll_interval
            worker_info = f'<SyncWorker running={worker_running}>'

        return jsonify({
            'success': True,
            'worker_running': worker_running,
            'worker_poll_interval': worker_poll_interval,
            '_debug_worker': worker_info,
            'sources': sources_status,
        })
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@bp.route('/api/cache/<int:source_id>', methods=['GET'])
def get_cache(source_id):
    """Récupère les données en cache pour une source."""
    try:
        data = get_cached_source_data(source_id)
        if data is None:
            return jsonify({'error': 'Pas de cache valide pour cette source'}), 404
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/cache', methods=['DELETE'])
def cleanup_cache():
    """Nettoie les caches expirés."""
    try:
        from datetime import datetime

        db = get_db()
        result = db.execute(
            'DELETE FROM sources_cache WHERE expires_at < ?',
            (datetime.now().isoformat(),)
        )
        db.commit()
        count = result.rowcount
        db.close()
        return jsonify({
            'success': True,
            'cleaned': count,
            'message': f'Supprimé {count} cache(s) expiré(s)'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/cache/<int:source_id>/refresh', methods=['POST'])
def refresh_cache(source_id):
    """Force la synchronisation d'une source."""
    try:
        from sync_worker import get_sync_worker

        db = get_db()
        source = db.execute('SELECT * FROM sources WHERE id = ?', (source_id,)).fetchone()
        db.close()

        if not source:
            return jsonify({'error': 'Source non trouvée'}), 404

        worker = get_sync_worker()
        if not worker or not worker.running:
            return jsonify({
                'error': 'Worker non actif',
                'hint': 'Le worker de sync n\'est pas actif.'
            }), 503

        db = get_db()
        db.execute('UPDATE sources SET derniere_sync = NULL WHERE id = ?', (source_id,))
        db.execute('DELETE FROM sources_cache WHERE source_id = ?', (source_id,))
        db.commit()
        db.close()

        return jsonify({
            'success': True,
            'message': 'Sync forcée demandée pour la prochaine pollarisation'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
