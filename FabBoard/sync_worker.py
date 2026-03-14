"""
FabBoard — Sync Worker Background
Polling automatique des sources externes et cache des données
Phase 3 : Système de synchronisation
"""

import threading
import time
import json
from datetime import datetime, timedelta
from models import get_db
import requests
from urllib.parse import quote
import os
from urllib.parse import urlparse


def _normalize_base_url(url):
    if not url:
        return ''
    return str(url).strip().rstrip('/')


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


class SyncWorker:
    """Worker qui synchronise les sources externes en continu."""
    
    def __init__(self, poll_interval=10):
        """
        Initialise le worker.
        
        Args:
            poll_interval: Intervalle de polling principal (en secondes)
        """
        self.poll_interval = poll_interval
        self.running = False
        self.thread = None
    
    def start(self):
        """Démarre le worker en background thread."""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._sync_loop, daemon=True)
            self.thread.start()
            print('[SyncWorker] Sync worker démarré')
    
    def stop(self):
        """Arrête le worker."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print('[SyncWorker] Sync worker arrêté')
    
    def _sync_loop(self):
        """Boucle infinie de synchronisation."""
        while self.running:
            try:
                db = get_db()
                try:
                    # Récupérer toutes les sources actives
                    sources = db.execute(
                        'SELECT * FROM sources WHERE actif = 1'
                    ).fetchall()
                    
                    for source_row in sources:
                        # Convertir sqlite3.Row en dict
                        source = dict(source_row)
                        self._sync_source(db, source)
                finally:
                    db.close()
            except Exception as e:
                # Log erreur mais continuer
                print(f'[SyncWorker] Erreur dans boucle: {e}')
            
            # Attendre avant prochain cycle
            time.sleep(self.poll_interval)
    
    def _sync_source(self, db, source):
        """Synchronise une source donnée si nécessaire."""
        source_id = source['id']
        
        # Vérifier si sync est nécessaire
        if not self._should_sync(source):
            return
        
        try:
            # Récupérer les données selon type
            data, error = self._fetch_source_data(source)
            
            if data is not None:
                # Cacher les données
                self._cache_source_data(db, source_id, data, source['sync_interval_sec'])
                
                # Mettre à jour dernière_sync
                db.execute(
                    'UPDATE sources SET derniere_sync = ?, derniere_erreur = ? WHERE id = ?',
                    (datetime.now().isoformat(), '', source_id)
                )
                db.commit()
            else:
                # Enregistrer erreur
                db.execute(
                    'UPDATE sources SET derniere_erreur = ? WHERE id = ?',
                    (error, source_id)
                )
                db.commit()
        except Exception as e:
            print(f'[SyncWorker] Erreur sync source {source_id}: {e}')
            try:
                db.execute(
                    'UPDATE sources SET derniere_erreur = ? WHERE id = ?',
                    (str(e), source_id)
                )
                db.commit()
            except Exception:
                pass
    
    def _should_sync(self, source):
        """Vérifie si une source doit être synchronisée."""
        if not source.get('derniere_sync'):
            return True
        
        try:
            last_sync = datetime.fromisoformat(source['derniere_sync'])
            sync_interval = timedelta(seconds=source.get('sync_interval_sec', 60))
            return datetime.now() >= last_sync + sync_interval
        except (ValueError, TypeError):
            return True
    
    def _fetch_source_data(self, source):
        """
        Récupère les données d'une source.
        
        Returns:
            (data_dict, error_string) ou (None, error_string) en cas d'erreur
        """
        source_type = source['type']
        url = source['url']
        credentials = json.loads(source.get('credentials_json', '{}') or '{}')
        
        try:
            if source_type == 'fabtrack':
                return self._fetch_fabtrack(url, credentials)
            elif source_type in ('repetier', 'prusalink'):
                return self._fetch_printer_api(url, credentials, source_type)
            elif source_type == 'nextcloud_caldav':
                return self._fetch_caldav(url, credentials)
            elif source_type == 'openweathermap':
                return self._fetch_openweathermap(url, credentials)
            elif source_type in ('rss', 'http'):
                return self._fetch_generic_http(url, credentials)
            else:
                return None, f"Type source non supporté: {source_type}"
        except Exception as e:
            return None, str(e)
    
    def _fetch_fabtrack(self, url, credentials):
        """Récupère les stats Fabtrack."""
        url = _normalize_base_url(url)
        candidates = [url]

        fallback = _default_fabtrack_url()
        if _is_localhost_url(url) and fallback and fallback != url:
            candidates.append(fallback)

        last_error = 'Service non disponible'

        for candidate in candidates:
            try:
                # Stats summary
                summary_resp = requests.get(f"{candidate}/api/stats/summary", timeout=4)
                summary_resp.raise_for_status()
                summary = summary_resp.json()

                # Consommations récentes
                conso_resp = requests.get(f"{candidate}/api/consommations?per_page=10&page=1", timeout=4)
                conso_resp.raise_for_status()
                conso = conso_resp.json()

                # Reference (machines, etc)
                ref_resp = requests.get(f"{candidate}/api/reference", timeout=4)
                ref_resp.raise_for_status()
                reference = ref_resp.json()

                # Compiler données
                machines = [
                    {
                        'id': m.get('id'),
                        'nom': m.get('nom', 'Machine'),
                        'statut': m.get('statut', 'inconnu'),
                        'actif': m.get('actif', 1),
                    }
                    for m in (reference.get('machines') or [])
                ]

                # Missions
                missions = []
                try:
                    missions_resp = requests.get(f"{candidate}/missions/api/list", timeout=4)
                    if missions_resp.status_code == 200:
                        missions_data = missions_resp.json()
                        missions = missions_data.get('data', [])
                except requests.RequestException:
                    pass  # Missions optionnelles

                return {
                    'summary': summary,
                    'consommations': conso.get('data', []),
                    'machines': machines,
                    'missions': missions,
                    'fetched_at': datetime.now().isoformat(),
                }, ''
            except requests.RequestException as e:
                last_error = str(e)

        return None, f"Fabtrack: {last_error}"
    
    def _fetch_printer_api(self, url, credentials, source_type):
        """Récupère l'état des imprimantes (Repetier ou PrusaLink)."""
        url = url.rstrip('/')
        
        try:
            if source_type == 'repetier':
                # Repetier Server API
                apikey = credentials.get('apikey', '')
                resp = requests.get(
                    f"{url}/api/v1/printers",
                    headers={'X-Api-Key': apikey} if apikey else {},
                    timeout=4
                )
            else:  # prusalink
                # PrusaLink API
                auth = None
                if credentials.get('user') and credentials.get('pass'):
                    auth = (credentials['user'], credentials['pass'])
                resp = requests.get(
                    f"{url}/api/v1/status",
                    auth=auth,
                    timeout=4
                )
            
            resp.raise_for_status()
            data = resp.json()
            
            return {
                'printers': data.get('printers', []) if source_type == 'repetier' else [data],
                'fetched_at': datetime.now().isoformat(),
            }, ''
        except requests.RequestException as e:
            return None, f"{source_type}: {str(e)}"
    
    def _fetch_caldav(self, url, credentials):
        """Récupère les événements CalDAV (iCal format)."""
        return SyncWorker._fetch_caldav_static(url, credentials)

    @staticmethod
    def _fetch_caldav_static(url, credentials):
        """Récupère les événements CalDAV (iCal format) — méthode statique."""
        try:
            user = credentials.get('user', '')
            password = credentials.get('pass', '')

            auth = (user, password) if user and password else None

            print(f'[CalDAV] Fetch: {url} (auth={"oui" if auth else "non"})')
            resp = requests.get(url, auth=auth, timeout=10)
            resp.raise_for_status()

            content_type = resp.headers.get('Content-Type', '')
            body = resp.text.strip()

            # Vérifier qu'on reçoit bien de l'iCal et pas du HTML
            if not body.startswith('BEGIN:VCALENDAR') and 'text/calendar' not in content_type:
                print(f'[CalDAV] Réponse inattendue (type={content_type}, début={body[:100]})')
                return None, f"CalDAV: réponse non-iCal (Content-Type: {content_type})"

            events = SyncWorker._parse_ical(body)
            print(f'[CalDAV] {len(events)} événements trouvés')

            return {
                'events': events,
                'fetched_at': datetime.now().isoformat(),
            }, ''
        except requests.RequestException as e:
            print(f'[CalDAV] Erreur requête: {e}')
            return None, f"CalDAV: {str(e)}"

    @staticmethod
    def _parse_ical(text):
        """Parse un flux iCal et extrait les événements."""
        events = []
        current_event = None
        last_prop = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            # Gérer les lignes dépliées (continuation RFC 5545)
            if raw_line.startswith((' ', '\t')) and current_event is not None and last_prop:
                current_event[last_prop] = current_event.get(last_prop, '') + line
                continue

            if line == 'BEGIN:VEVENT':
                current_event = {}
                last_prop = None
            elif line == 'END:VEVENT':
                if current_event is not None:
                    # N'ajouter que si on a au moins un titre
                    events.append({
                        'titre': current_event.get('SUMMARY', 'Événement'),
                        'description': current_event.get('DESCRIPTION', ''),
                        'lieu': current_event.get('LOCATION', ''),
                        'date_debut': SyncWorker._parse_ical_date(current_event.get('DTSTART', '')),
                        'date_fin': SyncWorker._parse_ical_date(current_event.get('DTEND', '')),
                        'uid': current_event.get('UID', ''),
                    })
                current_event = None
            elif current_event is not None and ':' in line:
                # Séparer la propriété (peut avoir des paramètres ;TZID=...)
                prop_part, _, value = line.partition(':')
                prop_name = prop_part.split(';')[0].upper()
                if prop_name in ('SUMMARY', 'DESCRIPTION', 'LOCATION', 'DTSTART', 'DTEND', 'UID'):
                    current_event[prop_name] = value
                    last_prop = prop_name
                else:
                    last_prop = None

        # Trier par date de début
        events.sort(key=lambda e: e.get('date_debut') or '')

        # Filtrer les événements passés (garder aujourd'hui + futur)
        today = datetime.now().strftime('%Y-%m-%d')
        future_events = [e for e in events if (e.get('date_debut') or '') >= today]

        return future_events[:30]  # Limiter à 30 événements

    @staticmethod
    def _parse_ical_date(value):
        """Convertit une date iCal (YYYYMMDD ou YYYYMMDDTHHmmSS) en ISO."""
        if not value:
            return ''
        # Nettoyer Z et espaces
        value = value.strip().replace('Z', '')
        try:
            if 'T' in value:
                dt = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
                return dt.isoformat()
            else:
                dt = datetime.strptime(value[:8], '%Y%m%d')
                return dt.date().isoformat()
        except (ValueError, IndexError):
            return value
    
    def _fetch_openweathermap(self, url, credentials):
        """Récupère les données météo OpenWeatherMap."""
        try:
            apikey = credentials.get('apikey', '')
            city = credentials.get('city', 'Paris')
            
            if not apikey:
                return None, "OpenWeatherMap: API key manquante"
            
            resp = requests.get(
                f"{url}/data/2.5/weather",
                params={'q': city, 'appid': apikey, 'units': 'metric'},
                timeout=4
            )
            resp.raise_for_status()
            
            data = resp.json()
            return {
                'weather': data,
                'fetched_at': datetime.now().isoformat(),
            }, ''
        except requests.RequestException as e:
            return None, f"OpenWeatherMap: {str(e)}"
    
    def _fetch_generic_http(self, url, credentials):
        """Récupère des données JSON d'une API générique."""
        try:
            headers = {}
            if credentials.get('headers'):
                headers = credentials.get('headers')
            
            auth = None
            if credentials.get('user') and credentials.get('pass'):
                auth = (credentials['user'], credentials['pass'])
            
            resp = requests.get(url, auth=auth, headers=headers, timeout=4)
            resp.raise_for_status()
            
            return {
                'data': resp.json(),
                'fetched_at': datetime.now().isoformat(),
            }, ''
        except requests.RequestException as e:
            return None, f"HTTP: {str(e)}"
    
    def _cache_source_data(self, db, source_id, data, interval_sec):
        """Cache les données d'une source."""
        expires_at = datetime.now() + timedelta(seconds=interval_sec)
        data_json = json.dumps(data)
        
        # INSERT OR REPLACE
        db.execute(
            '''INSERT OR REPLACE INTO sources_cache (source_id, data_json, expires_at)
               VALUES (?, ?, ?)''',
            (source_id, data_json, expires_at.isoformat())
        )
        db.commit()


# Instance globale
_worker = None


def start_sync_worker(poll_interval=10):
    """Démarre le worker de synchronisation."""
    global _worker
    if _worker is None:
        _worker = SyncWorker(poll_interval=poll_interval)
        _worker.start()
        return _worker
    return _worker


def stop_sync_worker():
    """Arrête le worker."""
    global _worker
    if _worker:
        _worker.stop()
        _worker = None


def get_sync_worker():
    """Retourne l'instance globale du worker."""
    return _worker
