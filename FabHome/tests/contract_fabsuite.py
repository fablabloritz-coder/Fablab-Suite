"""
test_fabsuite_contract.py — Contrat FabLab Suite : FabHome
===========================================================
Tests offline via Flask test client + DB SQLite temporaire.
FabHome utilise FABHOME_DB (var d'env) pour localiser sa DB.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

REQUIRED_MANIFEST_FIELDS = (
    'app', 'name', 'version', 'suite_version',
    'capabilities', 'widgets', 'status',
)
VALID_WIDGET_TYPES = {'counter', 'list', 'status', 'chart', 'text', 'table'}
REQUIRED_NOTIF_FIELDS = ('id', 'type', 'title', 'message')


class FabHomeFabSuiteContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabhome-contract-')

        # Patch env avant tout import de models / app
        os.environ['FABHOME_DB']   = os.path.join(cls._tmpdir, 'fabhome_test.db')
        os.environ['FABHOME_DATA'] = cls._tmpdir

        import models as fabhome_models
        fabhome_models.DB_PATH = os.environ['FABHOME_DB']
        fabhome_models.init_db()

        import app as app_module
        app_module.app.config['TESTING'] = True
        cls.client = app_module.app.test_client()
        cls._models = fabhome_models

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        os.environ.pop('FABHOME_DB', None)
        os.environ.pop('FABHOME_DATA', None)

    def _json(self, path, expected=200):
        resp = self.client.get(path)
        self.assertEqual(
            resp.status_code, expected,
            f'GET {path} → attendu {expected}, obtenu {resp.status_code}',
        )
        return json.loads(resp.data.decode('utf-8'))

    # ── Manifest ──────────────────────────────────────────────
    def test_manifest_http_200(self):
        resp = self.client.get('/api/fabsuite/manifest')
        self.assertEqual(resp.status_code, 200)

    def test_manifest_required_fields(self):
        data = self._json('/api/fabsuite/manifest')
        for field in REQUIRED_MANIFEST_FIELDS:
            self.assertIn(field, data, f'Manifest: champ "{field}" absent')

    def test_manifest_app_id_is_fabhome(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertEqual(data['app'], 'fabhome')

    def test_manifest_suite_version_present(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertIsInstance(data['suite_version'], str)
        self.assertTrue(data['suite_version'])

    def test_manifest_capabilities_not_empty(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertIsInstance(data['capabilities'], list)
        self.assertGreater(len(data['capabilities']), 0)

    # ── Health ────────────────────────────────────────────────
    def test_health_http_200(self):
        resp = self.client.get('/api/fabsuite/health')
        self.assertEqual(resp.status_code, 200)

    def test_health_status_ok(self):
        data = self._json('/api/fabsuite/health')
        self.assertEqual(data.get('status'), 'ok')

    # ── FabHome n'a pas de widgets FabSuite ───────────────────
    def test_manifest_widgets_is_list(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertIsInstance(data.get('widgets', []), list)

    def test_widget_unknown_returns_404(self):
        resp = self.client.get('/api/fabsuite/widget/inexistant-xyz-abc')
        self.assertEqual(resp.status_code, 404)

    # ── API Suite (hub) ───────────────────────────────────────
    def test_api_suite_apps_returns_json(self):
        """GET /api/suite/apps doit retourner une liste JSON."""
        resp = self.client.get('/api/suite/apps')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data.decode('utf-8'))
        self.assertIsInstance(data, list)

    def test_api_suite_dashboard_returns_json(self):
        """GET /api/suite/dashboard doit retourner une liste JSON."""
        resp = self.client.get('/api/suite/dashboard')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data.decode('utf-8'))
        self.assertIsInstance(data, list)

    def test_api_suite_register_requires_url(self):
        """POST /api/suite/apps sans URL doit retourner 400."""
        resp = self.client.post(
            '/api/suite/apps',
            json={},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    # ── CORS ──────────────────────────────────────────────────
    def test_cors_header_on_manifest(self):
        resp = self.client.get('/api/fabsuite/manifest')
        self.assertIn('Access-Control-Allow-Origin', resp.headers)

    def test_cors_header_on_health(self):
        resp = self.client.get('/api/fabsuite/health')
        self.assertIn('Access-Control-Allow-Origin', resp.headers)


if __name__ == '__main__':
    unittest.main(verbosity=2)
