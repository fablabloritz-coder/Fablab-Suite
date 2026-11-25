"""Tests : widget FabBoard « Dépendances externes ».

Le widget affiche les missions Fabtrack dont `service_id` est renseigné (marqueur
livré en 2.14.0), regroupées par service. Les données arrivent par le sync worker
(`missions` de la source Fabtrack) : ces tests couvrent le contrat serveur
(seed du widget, rendu du template, injection sûre de la configuration) et non
le filtrage côté navigateur.
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

CODE = 'fabtrack_dependances'


class DependancesWidgetTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabboard-dependances-')
        cls._orig_db_path = getattr(models, 'DB_PATH', None)
        models.DB_PATH = os.path.join(cls._tmpdir, 'test.db')
        models.init_db()
        models.migrate_db()

        # Neutraliser le worker de synchronisation (threads) avant l'import d'app.
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

    def _widget(self):
        conn = models.get_db()
        try:
            return conn.execute(
                'SELECT * FROM widgets_disponibles WHERE code = ?', (CODE,)
            ).fetchone()
        finally:
            conn.close()

    def _render(self, config=None, widget_id='test-dependances'):
        return self.client.post(
            f'/api/widgets/{CODE}/render',
            json={'config': config or {}, 'widget_id': widget_id},
        )

    # ── Seed ──────────────────────────────────────────────────
    def test_widget_disponible(self):
        widget = self._widget()
        self.assertIsNotNone(widget, 'le widget doit être créé par migrate_db')
        self.assertEqual(widget['categorie'], 'fabtrack')
        self.assertIn('Dépendances', widget['nom'])
        self.assertTrue(widget['icone'])

    def test_migration_idempotente(self):
        models.migrate_db()
        models.migrate_db()
        conn = models.get_db()
        try:
            total = conn.execute(
                'SELECT COUNT(*) AS c FROM widgets_disponibles WHERE code = ?', (CODE,)
            ).fetchone()['c']
        finally:
            conn.close()
        self.assertEqual(total, 1)

    def test_widget_liste_par_l_api(self):
        codes = [w['code'] for w in self.client.get('/api/widgets').get_json()['data']]
        self.assertIn(CODE, codes)

    # ── Rendu ─────────────────────────────────────────────────
    def test_render_html_et_config(self):
        response = self._render({'services': 'CMIL, Rectorat', 'nombre_max': 5})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result['success'])
        html = result['html']
        self.assertIn('id="widget-fabtrack-dependances-test-dependances"', html)
        self.assertIn(f'data-widget-code="{CODE}"', html)
        self.assertIn('Dépendances externes', html)
        self.assertIn('CMIL, Rectorat', html)
        self.assertIn('"nombre_max": 5', html)
        # Le widget lit les missions de la source Fabtrack du store.
        self.assertIn('missions', html)

    def test_render_sans_config(self):
        response = self._render({})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def test_config_echappee_dans_le_script(self):
        charge = '</script><script>alert(1)</script>'
        response = self._render({'services': charge})
        self.assertEqual(response.status_code, 200)
        html = response.get_json()['html']
        self.assertNotIn('</script><script>alert(1)', html)
        self.assertIn('\\u003c/script\\u003e', html)

    def test_widget_inconnu_toujours_404(self):
        response = self.client.post(
            '/api/widgets/inexistant-xyz/render', json={'config': {}}
        )
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
