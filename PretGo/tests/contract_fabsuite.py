"""
test_fabsuite_contract.py — Contrat FabLab Suite : PretGo
=========================================================
Tests offline via Flask test client + DB SQLite temporaire.
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

# Activer le mode test avant tout import Flask/app
os.environ['TESTING'] = '1'

import database  # noqa: E402

REQUIRED_MANIFEST_FIELDS = (
    'app', 'name', 'version', 'suite_version',
    'capabilities', 'widgets', 'status',
)
VALID_WIDGET_TYPES = {'counter', 'list', 'status', 'chart', 'text', 'table'}
REQUIRED_NOTIF_FIELDS = ('id', 'type', 'title', 'message')


class PretGoFabSuiteContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-contract-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR

        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-contract')
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
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

    def test_manifest_app_id_is_pretgo(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertEqual(data['app'], 'pretgo')

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
            self.assertIn('endpoint', w, f'Widget {w.get("id")}: "endpoint" manquant')
            self.assertTrue(str(w['endpoint']).startswith('/'))

    # ── Health ────────────────────────────────────────────────
    def test_health_http_200(self):
        resp = self.client.get('/api/fabsuite/health')
        self.assertEqual(resp.status_code, 200)

    def test_health_status_ok(self):
        data = self._json('/api/fabsuite/health')
        self.assertEqual(data.get('status'), 'ok')

    # ── Widgets connus ────────────────────────────────────────
    def _widget(self, widget_id, expected_type):
        data = self._json(f'/api/fabsuite/widget/{widget_id}')
        self.assertIn('type', data)
        self.assertEqual(data['type'], expected_type)
        return data

    def test_widget_active_loans(self):
        data = self._widget('active-loans', 'counter')
        self.assertIn('value', data)

    def test_widget_overdue_loans(self):
        data = self._widget('overdue-loans', 'list')
        self.assertIn('items', data)

    def test_widget_equipment_status(self):
        data = self._widget('equipment-status', 'chart')
        self.assertIn('labels', data)
        self.assertIn('values', data)

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
