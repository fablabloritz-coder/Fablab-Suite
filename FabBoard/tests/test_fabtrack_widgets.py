"""
test_fabtrack_widgets.py — Widgets Fabtrack de FabBoard
======================================================
Tests offline (Flask test client + SQLite temporaire) du proxy FabBoard vers
Fabtrack : bornes de période, routes /api/fabtrack/summary et
/api/fabtrack/consommations, et cache court qui mutualise les appels des widgets.

Fabtrack n'est jamais contacté : _request_json est remplacé par un faux.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import models  # noqa: E402


class FabtrackWidgetApiTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabboard-widgets-')
        cls._orig_db_path = models.DB_PATH if hasattr(models, 'DB_PATH') else None
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

        from routes import api_sources
        cls.api_sources = api_sources

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db_path is not None:
            models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        self.api_sources.clear_fabtrack_cache()
        self.calls = []

    # ── Helpers ───────────────────────────────────────────────
    def _patch_upstream(self, summary=None, conso=None, fail=False):
        summary = summary if summary is not None else {
            'total_interventions': 5,
            'total_3d_grammes': 160,
            'by_type': [{'nom': 'Impression 3D', 'couleur': '#f59e0b', 'total': 3}],
        }
        conso = conso if conso is not None else {
            'data': [{'id': 1, 'projet_nom': 'Demo', 'type_activite_nom': 'Impression 3D'}],
            'total': 1,
        }

        def fake(base_url, path, timeout=4):
            self.calls.append(path)
            if fail:
                return False, None, 'Service non disponible'
            if path.startswith('/api/consommations'):
                return True, conso, ''
            return True, summary, ''

        return mock.patch.object(self.api_sources, '_request_json', side_effect=fake)

    def _get(self, path, expected=200):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, expected, resp.data)
        return json.loads(resp.data.decode('utf-8'))

    def _paths(self, prefix=''):
        return [path for path in self.calls if path.startswith(prefix)]

    # ── Bornes de période ─────────────────────────────────────
    def test_bornes_de_periode_jour(self):
        debut, fin, label = self.api_sources._period_bounds('jour')
        self.assertEqual(debut, date.today().isoformat())
        self.assertEqual(debut, fin)
        self.assertEqual(label, "Aujourd'hui")

    def test_bornes_de_periode_semaine_commence_lundi(self):
        debut, fin, _ = self.api_sources._period_bounds('semaine')
        self.assertEqual(debut, (date.today() - timedelta(days=date.today().weekday())).isoformat())
        self.assertEqual(fin, date.today().isoformat())

    def test_bornes_de_periode_mois_et_cumul(self):
        debut, _, label = self.api_sources._period_bounds('mois')
        self.assertEqual(debut, date.today().replace(day=1).isoformat())
        self.assertTrue(label.startswith('Mois en cours'))
        debut, fin, label = self.api_sources._period_bounds('cumul')
        self.assertEqual((debut, fin), ('', ''))
        self.assertEqual(label, 'Cumul depuis le début')

    # ── Résumé par période ────────────────────────────────────
    def test_summary_mois_transmet_les_bornes(self):
        with self._patch_upstream():
            data = self._get('/api/fabtrack/summary?periode=mois')
        self.assertTrue(data['success'])
        self.assertEqual(data['periode'], 'mois')
        self.assertTrue(data['periode_label'].startswith('Mois en cours'))
        path = self.calls[0]
        self.assertTrue(path.startswith('/api/stats/summary?'), path)
        self.assertIn('date_debut=', path)
        self.assertIn('date_fin=', path)

    def test_summary_annee_scolaire_interroge_endpoint_dedie(self):
        with self._patch_upstream(summary={'periode_label': 'Année scolaire 2026-2027'}):
            data = self._get('/api/fabtrack/summary')
        self.assertEqual(data['periode'], 'annee_scolaire')
        self.assertEqual(data['periode_label'], 'Année scolaire 2026-2027')
        self.assertEqual(self.calls[0], '/api/stats/school-year-summary')

    def test_periode_inconnue_retombe_sur_annee_scolaire(self):
        with self._patch_upstream():
            data = self._get('/api/fabtrack/summary?periode=trimestre')
        self.assertEqual(data['periode'], 'annee_scolaire')
        self.assertEqual(self.calls[0], '/api/stats/school-year-summary')

    def test_fabtrack_indisponible_renvoie_502(self):
        with self._patch_upstream(fail=True):
            data = self._get('/api/fabtrack/summary?periode=mois', expected=502)
        self.assertFalse(data['success'])
        self.assertIn('error', data)

    # ── Listes de consommations ───────────────────────────────
    def test_consommations_limite_bornee(self):
        with self._patch_upstream():
            self._get('/api/fabtrack/consommations?periode=mois&limit=999')
        self.assertIn('per_page=100', self._paths('/api/consommations')[0])

        self.api_sources.clear_fabtrack_cache()
        self.calls = []
        with self._patch_upstream():
            self._get('/api/fabtrack/consommations?periode=mois&limit=0')
        self.assertIn('per_page=1', self._paths('/api/consommations')[0])

    def test_consommations_renvoie_types_et_lignes(self):
        with self._patch_upstream():
            data = self._get('/api/fabtrack/consommations?periode=mois&limit=5')
        self.assertTrue(data['success'])
        self.assertEqual(data['by_type'][0]['nom'], 'Impression 3D')
        self.assertEqual(data['data'][0]['projet_nom'], 'Demo')
        self.assertTrue(data['periode_label'])
        self.assertTrue(data['periode_bornes']['debut'])

    def test_consommations_echec_total_renvoie_502(self):
        with self._patch_upstream(fail=True):
            data = self._get('/api/fabtrack/consommations?periode=jour', expected=502)
        self.assertFalse(data['success'])

    # ── Cache court ───────────────────────────────────────────
    def test_cache_mutualise_les_appels_identiques(self):
        with self._patch_upstream():
            self._get('/api/fabtrack/consommations?periode=mois&limit=5')
            self._get('/api/fabtrack/consommations?periode=mois&limit=5')
        # Un résumé + une liste, appelés une seule fois malgré deux widgets.
        self.assertEqual(len(self.calls), 2, self.calls)

    def test_cache_isole_par_periode(self):
        with self._patch_upstream():
            self._get('/api/fabtrack/consommations?periode=mois&limit=5')
            self._get('/api/fabtrack/consommations?periode=jour&limit=5')
        self.assertEqual(len(self.calls), 4, self.calls)

    def test_refresh_force_le_rechargement(self):
        with self._patch_upstream():
            self._get('/api/fabtrack/consommations?periode=mois&limit=5')
            before = len(self.calls)
            self._get('/api/fabtrack/consommations?periode=mois&limit=5&refresh=1')
        self.assertGreater(len(self.calls), before)

    def test_cache_ne_conserve_pas_les_erreurs(self):
        with self._patch_upstream(fail=True):
            self._get('/api/fabtrack/summary?periode=mois', expected=502)
        with self._patch_upstream():
            data = self._get('/api/fabtrack/summary?periode=mois')
        self.assertTrue(data['success'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
