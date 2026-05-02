"""
test_fabsuite_contract.py — Contrat FabLab Suite : Fabtrack
============================================================
Tests offline via Flask test client + DB SQLite temporaire.
Vérifie : manifest, health, chaque widget, notifications, CORS.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

# Ajouter le répertoire de l'app au sys.path
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import models  # noqa: E402 — après patch sys.path

REQUIRED_MANIFEST_FIELDS = (
    'app', 'name', 'version', 'suite_version',
    'capabilities', 'widgets', 'status',
)
VALID_WIDGET_TYPES = {'counter', 'list', 'status', 'chart', 'text', 'table'}
REQUIRED_NOTIF_FIELDS = ('id', 'type', 'title', 'message')


class FabtrackFabSuiteContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-contract-')
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path  = models.DB_PATH
        models.DATA_DIR = cls._tmpdir
        models.DB_PATH  = os.path.join(cls._tmpdir, 'test.db')
        models.init_db()

        import app as app_module
        app_module.app.config['TESTING'] = True
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()
        cls._app_module = app_module

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH  = cls._orig_db_path
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

    def test_manifest_app_id_is_fabtrack(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertEqual(data['app'], 'fabtrack')

    def test_manifest_suite_version_present(self):
        data = self._json('/api/fabsuite/manifest')
        self.assertIsInstance(data['suite_version'], str)
        self.assertTrue(data['suite_version'], 'suite_version ne doit pas être vide')

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
            self.assertTrue(
                str(w['endpoint']).startswith('/'),
                f'Widget {w.get("id")}: endpoint "{w["endpoint"]}" doit commencer par /',
            )

    def test_manifest_widget_count(self):
        """Fabtrack doit exposer au moins 7 widgets."""
        data = self._json('/api/fabsuite/manifest')
        self.assertGreaterEqual(len(data.get('widgets', [])), 7)

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
        self.assertIn('type', data, f'Widget {widget_id}: champ "type" absent')
        self.assertEqual(data['type'], expected_type,
                         f'Widget {widget_id}: type attendu "{expected_type}", obtenu "{data["type"]}"')
        return data

    def test_widget_monthly_consumptions(self):
        data = self._widget('monthly-consumptions', 'counter')
        self.assertIn('value', data)

    def test_widget_machine_status(self):
        data = self._widget('machine-status', 'status')
        self.assertIn('items', data)

    def test_widget_top_machines(self):
        data = self._widget('top-machines', 'chart')
        self.assertIn('labels', data)
        self.assertIn('values', data)

    def test_widget_recent_activity(self):
        data = self._widget('recent-activity', 'list')
        self.assertIn('items', data)

    def test_widget_stock_low(self):
        data = self._widget('stock-low', 'list')
        self.assertIn('items', data)

    def test_widget_stock_summary(self):
        data = self._widget('stock-summary', 'counter')
        self.assertIn('value', data)

    def test_widget_pending_tasks(self):
        data = self._widget('pending-tasks', 'counter')
        self.assertIn('value', data)

    def test_widget_missions_board(self):
        data = self._widget('missions-board', 'table')
        self.assertIn('rows', data)

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
                self.assertIn(field, notif,
                              f'Notification: champ "{field}" absent dans {notif}')

    # ── CORS ──────────────────────────────────────────────────
    def test_cors_header_on_manifest(self):
        resp = self.client.get('/api/fabsuite/manifest')
        self.assertIn(
            'Access-Control-Allow-Origin', resp.headers,
            'CORS: header Access-Control-Allow-Origin absent du manifest',
        )

    def test_cors_header_on_health(self):
        resp = self.client.get('/api/fabsuite/health')
        self.assertIn(
            'Access-Control-Allow-Origin', resp.headers,
            'CORS: header absent du health',
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
