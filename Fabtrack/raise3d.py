"""
raise3d.py — Client API Raise Touch Remote Access v1.0
Gère l'authentification et la récupération du statut des imprimantes Raise3D du FabLab.

API doc : http://{ip}:10800/
Auth    : GET /v1/login?sign=md5(sha1("password={pwd}&timestamp={ms}"))&timestamp={ms}
"""

import hashlib
import json
import time
from urllib import request as urllib_req
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuration des imprimantes Raise3D
# ---------------------------------------------------------------------------
RAISE3D_PRINTERS = [
    {
        "id":       "raise3d-pro3plus",
        "name":     "Raise3D Pro3 Plus",
        "ip":       "192.168.1.175",
        "password": "c90b84",
    },
    {
        "id":       "raise3d-pro2plus",
        "name":     "Raise3D Pro2 Plus",
        "ip":       "192.168.1.130",
        "password": "ddb866",
    },
    {
        "id":       "raise3d-pro2",
        "name":     "Raise3D Pro2",
        "ip":       "192.168.1.127",
        "password": "3bb258",
    },
]

# Cache des tokens : ip -> {"token": str, "expires": float}
_TOKEN_CACHE: dict = {}
_TOKEN_TTL = 1700  # secondes (< 30 min pour rester dans la validité serveur)

# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

def _make_sign(password: str) -> tuple:
    """Génère (sign, timestamp_ms) pour le login Raise3D.
    Formule : sign = md5( sha1("password={pwd}&timestamp={ts}") )
    """
    ts = int(time.time() * 1000)
    sha1 = hashlib.sha1(
        f"password={password}&timestamp={ts}".encode()
    ).hexdigest()
    sign = hashlib.md5(sha1.encode()).hexdigest()
    return sign, ts


def get_token(ip: str, password: str, timeout: int = 5) -> str | None:
    """Retourne un token valide (cache ou nouveau login)."""
    now = time.time()
    cached = _TOKEN_CACHE.get(ip)
    if cached and now < cached["expires"]:
        return cached["token"]

    sign, ts = _make_sign(password)
    url = f"http://{ip}:10800/v1/login?sign={sign}&timestamp={ts}"
    try:
        with urllib_req.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("status") == 1:
            token = data["data"]["token"]
            _TOKEN_CACHE[ip] = {"token": token, "expires": now + _TOKEN_TTL}
            return token
    except Exception:
        pass
    return None


def invalidate_token(ip: str) -> None:
    """Force un nouveau login au prochain appel."""
    _TOKEN_CACHE.pop(ip, None)


# ---------------------------------------------------------------------------
# Appels API
# ---------------------------------------------------------------------------

def _api_get(ip: str, path: str, token: str, timeout: int = 5) -> dict:
    url = f"http://{ip}:10800/v1{path}?token={token}"
    with urllib_req.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Statut imprimante
# ---------------------------------------------------------------------------

def get_printer_status(ip: str, password: str, timeout: int = 5) -> dict:
    """Retourne le statut complet d'une imprimante.

    Champs retournés :
      online (bool)           — imprimante joignable
      error (str|None)        — message d'erreur si offline
      running_status (str)    — idle / busy / running / completed / error
      heatbed_cur/tar (float) — température plateau (°C)
      nozzle1_cur/tar (float) — température buse gauche (°C)
      nozzle2_cur/tar (float) — température buse droite (°C)
      job_status (str)        — running / paused / completed / stopped
      job_file (str)          — nom du fichier en cours (sans chemin)
      print_progress (float)  — progression 0-100
      printed_layer (int)     — couches imprimées
      total_layer (int)       — total de couches
      printed_time (int)      — temps imprimé (secondes)
      total_time (int)        — temps total estimé (secondes)
    """
    try:
        token = get_token(ip, password, timeout)
        if not token:
            return {"online": False, "error": "Authentification échouée"}

        run   = _api_get(ip, "/printer/runningstatus", token, timeout).get("data", {})
        basic = _api_get(ip, "/printer/basic",         token, timeout).get("data", {})
        n1    = _api_get(ip, "/printer/nozzle1",       token, timeout).get("data", {})
        n2    = _api_get(ip, "/printer/nozzle2",       token, timeout).get("data", {})
        job   = _api_get(ip, "/job/currentjob",        token, timeout).get("data", {})

        # L'API retourne print_progress entre 0.0 et 1.0
        raw_progress = job.get("print_progress") or 0
        progress = round(float(raw_progress) * 100, 1)

        return {
            "online": True,
            "error": None,
            "running_status": run.get("running_status", "unknown"),
            "heatbed_cur":  round(float(basic.get("heatbed_cur_temp") or 0), 1),
            "heatbed_tar":  round(float(basic.get("heatbed_tar_temp") or 0), 1),
            "nozzle1_cur":  round(float(n1.get("nozzle_cur_temp") or 0), 1),
            "nozzle1_tar":  round(float(n1.get("nozzle_tar_temp") or 0), 1),
            "nozzle2_cur":  round(float(n2.get("nozzle_cur_temp") or 0), 1),
            "nozzle2_tar":  round(float(n2.get("nozzle_tar_temp") or 0), 1),
            "job_status":      job.get("job_status"),
            "job_file":        (job.get("file_name") or "").rsplit("/", 1)[-1],
            "print_progress":  progress,
            "printed_layer":   job.get("printed_layer"),
            "total_layer":     job.get("total_layer"),
            "printed_time":    job.get("printed_time"),
            "total_time":      job.get("total_time"),
        }

    except (URLError, HTTPError) as e:
        invalidate_token(ip)
        return {"online": False, "error": f"Réseau : {e}"}
    except TimeoutError:
        invalidate_token(ip)
        return {"online": False, "error": "Timeout"}
    except Exception as e:
        return {"online": False, "error": str(e)}


def get_all_status(timeout: int = 5) -> list:
    """Retourne le statut de toutes les imprimantes configurées."""
    result = []
    for p in RAISE3D_PRINTERS:
        s = get_printer_status(p["ip"], p["password"], timeout)
        s["id"]   = p["id"]
        s["name"] = p["name"]
        s["ip"]   = p["ip"]
        result.append(s)
    return result


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def running_status_label(status: str) -> str:
    """Traduit le running_status en texte français."""
    return {
        "idle":      "En attente",
        "busy":      "Occupée",
        "running":   "En impression",
        "completed": "Terminée",
        "error":     "Erreur",
    }.get(status or "", status or "Inconnu")


def format_duration(seconds) -> str:
    """Formate une durée en secondes → 'Xh Ymin'."""
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    if h:
        return f"{h}h {m:02d}min"
    return f"{m}min"
