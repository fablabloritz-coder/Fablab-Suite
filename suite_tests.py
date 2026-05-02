#!/usr/bin/env python3
"""
suite_tests.py — Campagne de tests complète FabLab Suite
=========================================================

Usage :
    python suite_tests.py                  # tous les tests (offline + réseau si dispo)
    python suite_tests.py --offline        # uniquement tests unitaires + contrat offline
    python suite_tests.py --online         # uniquement tests réseau
    python suite_tests.py --app fabtrack   # une seule app
    python suite_tests.py --report out.json

Couches testées :
    UNIT      tests unitaires existants (unittest discover par app)
    CONTRACT  contrat FabSuite offline (Flask test client, DB temporaire)
    NETWORK   smoke tests HTTP pages + API + contrat FabSuite (si app disponible)
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VENV_PYTHON = os.path.join(WORKSPACE, 'FabStock', '.venv', 'Scripts', 'python.exe')

# ──────────────────────────────────────────────────────────────
# Registre des applications
# ──────────────────────────────────────────────────────────────
APPS = {
    'fabtrack': {
        'name': 'Fabtrack',
        'dir': os.path.join(WORKSPACE, 'Fabtrack'),
        'port': 5555,
        'unit_tests_dir': 'tests',
        'contract_tests': 'tests/contract_fabsuite.py',
        'pages': ['/', '/machines', '/consommations', '/stock', '/missions', '/parametres'],
        'api_endpoints': [
            '/api/stats/summary',
            '/api/machines',
            '/missions/api/list',
        ],
        'fabsuite_widgets': [
            'monthly-consumptions', 'machine-status', 'top-machines',
            'recent-activity', 'stock-low', 'stock-summary',
            'pending-tasks', 'missions-board',
        ],
    },
    'pretgo': {
        'name': 'PretGo',
        'dir': os.path.join(WORKSPACE, 'PretGo'),
        'port': 5000,
        'unit_tests_dir': None,  # tests à la racine, pas de discover
        'contract_tests': 'tests/contract_fabsuite.py',
        'pages': ['/', '/nouveau-pret', '/historique', '/admin/login', '/reservations'],
        'api_endpoints': [
            '/api/autocomplete/personnes',
            '/api/inventaire/search?q=',
        ],
        'fabsuite_widgets': ['active-loans', 'overdue-loans', 'equipment-status'],
    },
    'fabboard': {
        'name': 'FabBoard',
        'dir': os.path.join(WORKSPACE, 'FabBoard'),
        'port': 5580,
        'venv_python': os.path.join(WORKSPACE, 'FabBoard', 'venv', 'Scripts', 'python.exe'),
        'unit_tests_dir': None,
        'contract_tests': 'tests/contract_fabsuite.py',
        'pages': ['/'],
        'api_endpoints': ['/api/slides', '/api/sources'],
        'fabsuite_widgets': ['active-slides'],
    },
    'fabhome': {
        'name': 'FabHome',
        'dir': os.path.join(WORKSPACE, 'FabHome'),
        'port': 3001,
        'unit_tests_dir': None,
        'contract_tests': 'tests/contract_fabsuite.py',
        'pages': ['/'],
        'api_endpoints': ['/api/suite/apps', '/api/suite/dashboard'],
        'fabsuite_widgets': [],
    },
}

# ──────────────────────────────────────────────────────────────
# Couleurs terminal (Windows ANSI compatible)
# ──────────────────────────────────────────────────────────────
try:
    import colorama
    colorama.init()
    _COLOR = True
except ImportError:
    _COLOR = os.name != 'nt'  # Windows sans colorama : désactiver

def _c(code, text):
    return f'{code}{text}\033[0m' if _COLOR else text

def _ok(msg):  print(f'  {_c(chr(27)+"[92m", "✓")} {msg}')
def _fail(msg): print(f'  {_c(chr(27)+"[91m", "✗")} {msg}')
def _warn(msg): print(f'  {_c(chr(27)+"[93m", "⚠")} {msg}')
def _info(msg): print(f'  {_c(chr(27)+"[94m", "ℹ")} {msg}')
def _section(msg): print(f'\n{_c(chr(27)+"[1m"+chr(27)+"[96m", "▶ " + msg)}')

# Aliases simples sans escape complexe
GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
BLUE   = '\033[94m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'

def ok(msg):      print(f'  {GREEN}✓{RESET} {msg}')
def fail(msg):    print(f'  {RED}✗{RESET} {msg}')
def warn(msg):    print(f'  {YELLOW}⚠{RESET} {msg}')
def info(msg):    print(f'  {BLUE}ℹ{RESET} {msg}')
def section(msg): print(f'\n{BOLD}{CYAN}▶ {msg}{RESET}')

# ──────────────────────────────────────────────────────────────
# Résultats agrégés
# ──────────────────────────────────────────────────────────────
class Results:
    def __init__(self):
        self.passed  = []
        self.failed  = []
        self.warned  = []
        self.skipped = []

    def add_pass(self, label): self.passed.append(label)
    def add_fail(self, label): self.failed.append(label)
    def add_warn(self, label): self.warned.append(label)
    def add_skip(self, label): self.skipped.append(label)

# ──────────────────────────────────────────────────────────────
# Contrat FabSuite
# ──────────────────────────────────────────────────────────────
REQUIRED_MANIFEST_FIELDS = ('app', 'name', 'version', 'suite_version',
                             'capabilities', 'widgets', 'status')
VALID_WIDGET_TYPES = {'counter', 'list', 'status', 'chart', 'text', 'table'}
REQUIRED_NOTIF_FIELDS = ('id', 'type', 'title', 'message')

# ──────────────────────────────────────────────────────────────
# HTTP utilities
# ──────────────────────────────────────────────────────────────
def http_get(url, timeout=6):
    """(status_code, json_or_None, error_or_None)"""
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            try:
                return resp.status, json.loads(body), None
            except json.JSONDecodeError:
                return resp.status, None, 'réponse non-JSON'
    except urllib.error.HTTPError as e:
        return e.code, None, f'HTTP {e.code}'
    except Exception as exc:
        return 0, None, str(exc)

def is_reachable(base_url):
    status, _, _ = http_get(f'{base_url}/api/fabsuite/health', timeout=3)
    return status == 200

# ──────────────────────────────────────────────────────────────
# Subprocess runner
# ──────────────────────────────────────────────────────────────
def _python(args_cli):
    """Chemin Python à utiliser."""
    return args_cli.venv_python

def run_discover(app_dir, tests_dir, python_exe):
    env = os.environ.copy()
    env['PYTHONPATH'] = app_dir
    return subprocess.run(
        [python_exe, '-m', 'unittest', 'discover', '-s', tests_dir, '-v'],
        cwd=app_dir, env=env,
        capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        timeout=180,
    )

def run_test_file(app_dir, rel_path, python_exe):
    env = os.environ.copy()
    env['PYTHONPATH'] = app_dir
    return subprocess.run(
        [python_exe, '-m', 'unittest', rel_path.replace('/', '.').rstrip('.py'), '-v'],
        cwd=app_dir, env=env,
        capture_output=True, text=True,
        encoding='utf-8', errors='replace',
        timeout=120,
    )

def _extract_summary(proc):
    """Retourne la dernière ligne de résumé unittest."""
    combined = (proc.stderr or '') + (proc.stdout or '')
    for line in reversed(combined.splitlines()):
        s = line.strip()
        if s.startswith('Ran ') or s in ('OK', 'FAILED') or 'error' in s.lower():
            return s
    return ''

def _print_failures(proc):
    combined = (proc.stderr or '') + (proc.stdout or '')
    for line in combined.splitlines():
        if any(k in line for k in ('FAIL:', 'ERROR:', 'AssertionError', 'Traceback')):
            print(f'      {RED}{line}{RESET}')

# ──────────────────────────────────────────────────────────────
# Tests réseau par couche
# ──────────────────────────────────────────────────────────────
def check_manifest(base_url, app_key, results):
    url = f'{base_url}/api/fabsuite/manifest'
    status, data, err = http_get(url)
    if status != 200:
        fail(f'manifest → HTTP {status} {err or ""}')
        results.add_fail(f'{app_key}/manifest: HTTP {status}')
        return None

    missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in data]
    if missing:
        fail(f'manifest: champs manquants {missing}')
        results.add_fail(f'{app_key}/manifest: champs absents {missing}')
    else:
        ok(f'manifest → app={data["app"]} v{data["version"]} ({len(data.get("widgets",[]))} widgets)')
        results.add_pass(f'{app_key}/manifest')

    for w in data.get('widgets', []):
        wt = w.get('type', '')
        wi = w.get('id', '?')
        if wt not in VALID_WIDGET_TYPES:
            fail(f'widget {wi}: type inconnu "{wt}"')
            results.add_fail(f'{app_key}/manifest/widget/{wi}: type={wt}')

    return data

def check_health(base_url, app_key, results):
    url = f'{base_url}/api/fabsuite/health'
    status, data, err = http_get(url)
    if status != 200:
        fail(f'health → HTTP {status} {err or ""}')
        results.add_fail(f'{app_key}/health: HTTP {status}')
        return
    s = (data or {}).get('status', '')
    if s == 'ok':
        ok('health → status=ok')
        results.add_pass(f'{app_key}/health')
    else:
        fail(f'health → status="{s}" (attendu: ok)')
        results.add_fail(f'{app_key}/health: status={s}')

def check_widget(base_url, app_key, widget_id, results):
    url = f'{base_url}/api/fabsuite/widget/{widget_id}'
    status, data, err = http_get(url)
    if status != 200:
        fail(f'widget/{widget_id} → HTTP {status}')
        results.add_fail(f'{app_key}/widget/{widget_id}: HTTP {status}')
        return
    if not data or 'type' not in data:
        fail(f'widget/{widget_id} → champ "type" absent')
        results.add_fail(f'{app_key}/widget/{widget_id}: champ type absent')
        return
    ok(f'widget/{widget_id} → type={data["type"]}')
    results.add_pass(f'{app_key}/widget/{widget_id}')

def check_notifications(base_url, app_key, results):
    url = f'{base_url}/api/fabsuite/notifications'
    status, data, err = http_get(url)
    if status != 200:
        fail(f'notifications → HTTP {status}')
        results.add_fail(f'{app_key}/notifications: HTTP {status}')
        return
    notifs = (data or {}).get('notifications', [])
    ok(f'notifications → {len(notifs)} notification(s)')
    bad = []
    for n in notifs:
        for f in REQUIRED_NOTIF_FIELDS:
            if f not in n:
                bad.append(f'{f} absent dans {n.get("id", "?")}')
    if bad:
        for b in bad:
            fail(f'  notification: {b}')
            results.add_fail(f'{app_key}/notification: {b}')
    else:
        results.add_pass(f'{app_key}/notifications')

def check_pages(base_url, app_key, pages, results):
    for path in pages:
        url = f'{base_url}{path}'
        status, _, err = http_get(url, timeout=7)
        if status in (200, 302, 301):
            ok(f'GET {path} → {status}')
            results.add_pass(f'{app_key}{path}')
        else:
            fail(f'GET {path} → {status} {err or ""}')
            results.add_fail(f'{app_key}{path}: HTTP {status}')

def check_api_endpoints(base_url, app_key, endpoints, results):
    for path in endpoints:
        url = f'{base_url}{path}'
        status, data, err = http_get(url, timeout=7)
        if status == 200 and data is not None:
            ok(f'API {path} → 200 JSON')
            results.add_pass(f'{app_key}{path}')
        elif status == 200:
            warn(f'API {path} → 200 non-JSON')
            results.add_warn(f'{app_key}{path}: réponse non-JSON')
        else:
            fail(f'API {path} → {status} {err or ""}')
            results.add_fail(f'{app_key}{path}: HTTP {status}')

# ──────────────────────────────────────────────────────────────
# Campagne principale
# ──────────────────────────────────────────────────────────────
def run_campaign(args):
    results = Results()
    target_apps = [args.app] if args.app else list(APPS.keys())
    t0 = time.time()

    # ── Couche 1 : Tests unitaires existants ─────────────────
    if not args.online_only:
        section('COUCHE 1 — Tests unitaires existants (unittest discover)')

        for key in target_apps:
            cfg = APPS[key]
            app_dir = cfg['dir']
            tdir = cfg.get('unit_tests_dir')
            print(f'\n  [{cfg["name"]}]')

            if not tdir:
                warn('Pas de dossier tests/ configuré (tests unitaires ignorés)')
                results.add_skip(f'{key}/unit-tests: non configuré')
                continue

            full = os.path.join(app_dir, tdir)
            if not os.path.isdir(full):
                warn(f'Dossier tests/ introuvable : {full}')
                results.add_skip(f'{key}/unit-tests: dossier absent')
                continue

            t1 = time.time()
            python_exe = cfg.get('venv_python') or _python(args)
            proc = run_discover(app_dir, tdir, python_exe)
            elapsed = time.time() - t1
            summary = _extract_summary(proc)

            if proc.returncode == 0:
                ok(f'{summary}  ({elapsed:.1f}s)')
                results.add_pass(f'{key}/unit-tests')
            else:
                fail(f'{summary or "échec"}  ({elapsed:.1f}s)')
                results.add_fail(f'{key}/unit-tests')
                _print_failures(proc)

    # ── Couche 2 : Contract tests FabSuite offline ────────────
    if not args.online_only:
        section('COUCHE 2 — Contrat FabSuite (offline, Flask test client)')

        for key in target_apps:
            cfg = APPS[key]
            app_dir = cfg['dir']
            rel = cfg.get('contract_tests', '')
            full = os.path.join(app_dir, rel)
            print(f'\n  [{cfg["name"]}]')

            if not rel or not os.path.isfile(full):
                warn(f'Fichier contrat absent : {rel or "(non défini)"}')
                results.add_skip(f'{key}/contract: fichier absent')
                continue

            # Convertir chemin en module unittest
            module = rel.replace('/', '.').replace('\\', '.')
            if module.endswith('.py'):
                module = module[:-3]

            t1 = time.time()
            python_exe = cfg.get('venv_python') or _python(args)
            env = os.environ.copy()
            env['PYTHONPATH'] = app_dir
            proc = subprocess.run(
                [python_exe, '-m', 'unittest', module, '-v'],
                cwd=app_dir, env=env,
                capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=120,
            )
            elapsed = time.time() - t1
            summary = _extract_summary(proc)

            if proc.returncode == 0:
                ok(f'{summary}  ({elapsed:.1f}s)')
                results.add_pass(f'{key}/contract')
            else:
                fail(f'{summary or "échec"}  ({elapsed:.1f}s)')
                results.add_fail(f'{key}/contract')
                _print_failures(proc)
                # Afficher les 30 dernières lignes pour diagnostic
                combined = (proc.stderr or '') + (proc.stdout or '')
                lines = combined.splitlines()[-30:]
                for line in lines:
                    if line.strip():
                        print(f'    {line}')

    # ── Couche 3+4 : Tests réseau ─────────────────────────────
    if not args.offline_only:
        section('COUCHE 3+4 — Tests réseau (pages + API + contrat FabSuite HTTP)')

        for key in target_apps:
            cfg = APPS[key]
            base_url = f'http://localhost:{cfg["port"]}'
            print(f'\n  [{cfg["name"]}]  {base_url}')

            if not is_reachable(base_url):
                warn(f'App non disponible sur {base_url} — tests réseau ignorés')
                results.add_skip(f'{key}/network: app non disponible')
                continue

            ok('App joignable')

            # Contrat FabSuite
            manifest = check_manifest(base_url, key, results)
            check_health(base_url, key, results)

            # Widgets depuis le manifest live (ou liste statique si manifest KO)
            widget_ids = cfg.get('fabsuite_widgets', [])
            if manifest:
                widget_ids = [w['id'] for w in manifest.get('widgets', [])]
                if manifest.get('notifications'):
                    check_notifications(base_url, key, results)

            for wid in widget_ids:
                check_widget(base_url, key, wid, results)

            # Pages principales
            check_pages(base_url, key, cfg.get('pages', []), results)

            # API internes
            check_api_endpoints(base_url, key, cfg.get('api_endpoints', []), results)

    # ── Rapport final ─────────────────────────────────────────
    elapsed_total = time.time() - t0
    n_pass  = len(results.passed)
    n_fail  = len(results.failed)
    n_warn  = len(results.warned)
    n_skip  = len(results.skipped)
    total   = n_pass + n_fail

    print(f'\n{"=" * 62}')
    print(f'{BOLD}  RÉSULTAT — FabLab Suite Test Campaign{RESET}')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  |  durée : {elapsed_total:.1f}s')
    print(f'{"=" * 62}')
    print(f'  {GREEN}✓ PASS  {n_pass:4d}{RESET}  /{total}')
    print(f'  {RED}✗ FAIL  {n_fail:4d}{RESET}')
    print(f'  {YELLOW}⚠ WARN  {n_warn:4d}{RESET}')
    print(f'  {BLUE}⬜ SKIP  {n_skip:4d}{RESET}')

    if results.failed:
        print(f'\n{RED}Échecs :{RESET}')
        for item in results.failed:
            print(f'  • {item}')

    if results.warned:
        print(f'\n{YELLOW}Avertissements :{RESET}')
        for item in results.warned:
            print(f'  • {item}')

    if results.skipped:
        print(f'\n{BLUE}Ignorés :{RESET}')
        for item in results.skipped:
            print(f'  • {item}')

    print()

    # Export JSON
    if args.report:
        report = {
            'date': datetime.now().isoformat(),
            'duration_s': round(elapsed_total, 2),
            'summary': {
                'pass': n_pass, 'fail': n_fail,
                'warn': n_warn, 'skip': n_skip,
                'total': total,
            },
            'results': {
                'passed':  results.passed,
                'failed':  results.failed,
                'warned':  results.warned,
                'skipped': results.skipped,
            },
        }
        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        ok(f'Rapport exporté : {args.report}')

    return n_fail == 0


# ──────────────────────────────────────────────────────────────
# Entrée principale
# ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Campagne de tests complète FabLab Suite',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--offline', dest='offline_only', action='store_true',
                        help='Tests offline uniquement (unitaires + contrat)')
    parser.add_argument('--online', dest='online_only', action='store_true',
                        help='Tests réseau uniquement (apps doivent tourner)')
    parser.add_argument('--app', choices=list(APPS.keys()), metavar='APP',
                        help=f'Filtrer sur une seule app : {"|".join(APPS)}')
    parser.add_argument('--report', metavar='FICHIER',
                        help='Exporter le rapport en JSON')
    parser.add_argument('--venv-python', default=DEFAULT_VENV_PYTHON,
                        dest='venv_python', metavar='PYTHON',
                        help='Chemin vers python du venv')
    args = parser.parse_args()

    print(f'{BOLD}{"═" * 62}')
    print(f'  FabLab Suite — Campagne de tests complète')
    print(f'  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'{"═" * 62}{RESET}')

    if args.offline_only and args.online_only:
        parser.error('--offline et --online sont mutuellement exclusifs')

    success = run_campaign(args)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
