"""
test_fabsuite_contract.py — Contrat FabLab Suite : FabBoard
============================================================
Tests offline via Flask test client + DB SQLite temporaire.
Le sync_worker est neutralisé pour éviter les threads parasites.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import models  # noqa: E402

REQUIRED_MANIFEST_FIELDS = (
    'app', 'name', 'version', 'suite_version',
    'capabilities', 'widgets', 'status',
)
VALID_WIDGET_TYPES = {'counter', 'list', 'status', 'chart', 'text', 'table'}
REQUIRED_NOTIF_FIELDS = ('id', 'type', 'title', 'message')


class FabBoardFabSuiteContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabboard-contract-')
        cls._orig_db_path = models.DB_PATH if hasattr(models, 'DB_PATH') else None

        # Patcher le chemin DB
        models.DB_PATH = os.path.join(cls._tmpdir, 'test.db')
        models.init_db()
        models.migrate_db()

        # Neutraliser le sync_worker (threads) avant import d'app
        with mock.patch('sync_worker.start_sync_worker', return_value=None), \
             mock.patch('sync_worker.stop_sync_worker', return_value=None):
            import app as app_module
            app_module.app.config['TESTING'] = True
            app_module._db_initialized = True
            cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db_path is not None:
            models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

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

    def test_manifest_app_id_is_fabboard(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertEqual(data['app'], 'fabboard')

    def test_manifest_capabilities_not_empty(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertIsInstance(data['capabilities'], list)
        self.assertGreater(len(data['capabilities']), 0)

    def test_manifest_widget_types_valid(self):
        data = self._json('/api/fabsuite/manifest')
        for w in data.get('widgets', []):
            self.assertIn(
                w.get('type'), VALID_WIDGET_TYPES,
                f'Widget {w.get("id")}: type "{w.get("type")}" non reconnu',
            )

    def test_manifest_widgets_have_endpoint(self):
        data = self._json('/api/fabsuite/manifest')
        for w in data.get('widgets', []):
            self.assertIn('endpoint', w)
            self.assertTrue(str(w['endpoint']).startswith('/'))

    # ── Health ────────────────────────────────────────────────
    def test_health_http_200(self):
        resp = self.client.get('/api/fabsuite/health')
        self.assertEqual(resp.status_code, 200)

    def test_health_status_ok(self):
        data = self._json('/api/fabsuite/health')
        self.assertEqual(data.get('status'), 'ok')

    # ── Widget active-slides ──────────────────────────────────
    def test_widget_active_slides(self):
        data = self._json('/api/fabsuite/widget/active-slides')
        self.assertIn('type', data)
        self.assertEqual(data['type'], 'counter')
        self.assertIn('value', data)

    def test_widget_unknown_returns_404(self):
        resp = self.client.get('/api/fabsuite/widget/inexistant-xyz-abc')
        self.assertEqual(resp.status_code, 404)

    # ── Notifications ─────────────────────────────────────────
    def test_notifications_http_200(self):
        resp = self.client.get('/api/fabsuite/notifications')
        self.assertEqual(resp.status_code, 200)

    def test_notifications_structure(self):
        data = self._json('/api/fabsuite/notifications')
        self.assertIn('notifications', data)
        self.assertIsInstance(data['notifications'], list)

    def test_notifications_fields_if_any(self):
        data = self._json('/api/fabsuite/notifications')
        for notif in data.get('notifications', []):
            for field in REQUIRED_NOTIF_FIELDS:
                self.assertIn(field, notif, f'Notification: "{field}" absent')

    # ── CORS ──────────────────────────────────────────────────
    def test_cors_header_on_manifest(self):
        resp = self.client.get('/api/fabsuite/manifest')
        self.assertIn('Access-Control-Allow-Origin', resp.headers)

    def test_cors_header_on_health(self):
        resp = self.client.get('/api/fabsuite/health')
        self.assertIn('Access-Control-Allow-Origin', resp.headers)

    # ── API slides (smoke) ────────────────────────────────────
    def test_api_slides_returns_json(self):
        resp = self.client.get('/api/slides')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data.decode('utf-8'))
        self.assertIsInstance(data, (list, dict))

    def test_api_sources_returns_json(self):
        resp = self.client.get('/api/sources')
        self.assertEqual(resp.status_code, 200)

    # ── Override affichage fermeture (smoke) ────────────────
    def test_display_override_status_returns_json(self):
        data = self._json('/api/display/override-status')
        self.assertIn('success', data)
        self.assertIn('enabled', data)
        self.assertIn('active', data)
        self.assertIn('mode', data)

    def test_display_override_status_mode_valid(self):
        data = self._json('/api/display/override-status')
        self.assertIn(data.get('mode'), ('text', 'image'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
