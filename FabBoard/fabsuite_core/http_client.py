"""
fabsuite_core.http_client — Client HTTP mutualisé pour FabHome et les apps.

Usage :
    from fabsuite_core.http_client import fetch_json

    data = fetch_json("http://host.docker.internal:5555/api/fabsuite/manifest", timeout=5)
    if data is not None:
        print(data["app"])
"""

import json
import logging
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger("fabsuite_core.http_client")


def fetch_json(url, timeout=5, retries=0, headers=None):
    """Récupère du JSON depuis une URL.

    Paramètres :
        url (str) : URL complète
        timeout (int) : Timeout en secondes (défaut 5)
        retries (int) : Nombre de tentatives (0 = une seule)
        headers (dict, opt) : Headers HTTP supplémentaires

    Retourne :
        dict/list : Données JSON parsées
        None : En cas d'erreur (loggée)
    """
    req = Request(url)
    req.add_header('Accept', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    attempts = 1 + max(0, int(retries))
    last_error = None

    for attempt in range(attempts):
        try:
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode('utf-8')
                return json.loads(data)
        except HTTPError as e:
            last_error = e
            logger.warning("HTTP %d pour %s (essai %d/%d)", e.code, url, attempt + 1, attempts)
        except URLError as e:
            last_error = e
            logger.warning("Erreur réseau pour %s : %s (essai %d/%d)", url, e.reason, attempt + 1, attempts)
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning("JSON invalide depuis %s : %s", url, e)
            break  # Pas de retry sur JSON invalide
        except Exception as e:
            last_error = e
            logger.warning("Erreur inattendue pour %s : %s", url, e)

    if last_error:
        logger.error("Échec fetch_json(%s) après %d essai(s) : %s", url, attempts, last_error)
    return None


def check_health(base_url, timeout=3):
    """Vérifie la santé d'une app FabSuite.

    Retourne True si l'app répond {"status": "ok"}, False sinon.
    """
    data = fetch_json(f"{base_url.rstrip('/')}/api/fabsuite/health", timeout=timeout)
    return data is not None and data.get("status") == "ok"


def fetch_manifest(base_url, timeout=5):
    """Récupère le manifest FabSuite d'une app.

    Retourne le dict manifest ou None en cas d'erreur.
    """
    return fetch_json(f"{base_url.rstrip('/')}/api/fabsuite/manifest", timeout=timeout)


def fetch_widget(base_url, endpoint, timeout=5):
    """Récupère les données d'un widget FabSuite.

    Paramètres :
        base_url (str) : URL de base de l'app
        endpoint (str) : Chemin du widget (ex: "/api/fabsuite/widget/monthly-consumptions")

    Retourne le dict données ou None.
    """
    url = f"{base_url.rstrip('/')}{endpoint}"
    return fetch_json(url, timeout=timeout)


def fetch_notifications(base_url, timeout=5):
    """Récupère les notifications d'une app FabSuite.

    Retourne la liste de notifications ou [].
    """
    data = fetch_json(f"{base_url.rstrip('/')}/api/fabsuite/notifications", timeout=timeout)
    if data and "notifications" in data:
        return data["notifications"]
    return []
