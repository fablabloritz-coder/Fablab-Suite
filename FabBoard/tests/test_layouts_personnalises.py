"""Tests : gabarits variés et personnalisation des colonnes/lignes.

La personnalisation est stockée par slide dans ``layout_config_json``. Une
valeur absente ou vide doit conserver le rendu égalitaire historique ; les
gabarits déjà installés ne doivent jamais être modifiés par la migration.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import models  # noqa: E402


class LayoutsPersonnalisesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabboard-layouts-')
        cls._orig_db_path = getattr(models, 'DB_PATH', None)
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabboard.db')
        models.init_db()
        models.migrate_db()

        with mock.patch('sync_worker.start_sync_worker', return_value=None), \
             mock.patch('sync_worker.stop_sync_worker', return_value=None):
            import app as app_module
            app_module.app.config['TESTING'] = True
            app_module._db_initialized = True
            cls.app = app_module.app
            cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db_path is not None:
            models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        conn = models.get_db()
        try:
            conn.execute('DELETE FROM slide_widgets')
            conn.execute('DELETE FROM slides')
            conn.commit()
        finally:
            conn.close()

    def _layout_id(self, code):
        conn = models.get_db()
        try:
            row = conn.execute('SELECT id FROM layouts WHERE code = ?', (code,)).fetchone()
            self.assertIsNotNone(row, f'layout {code} absent')
            return row['id']
        finally:
            conn.close()

    def _creer_slide(self, layout_code='grid_3x2', config=None):
        payload = {'nom': 'Slide layout', 'layout_id': self._layout_id(layout_code)}
        if config is not None:
            payload['layout_config'] = config
        response = self.client.post('/api/slides', json=payload)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()['data']

    def test_layouts_varies_presents_et_migration_idempotente(self):
        codes = {layout[0] for layout in models.LAYOUTS_VARIES}
        conn = models.get_db()
        try:
            presents = {
                row['code'] for row in conn.execute(
                    'SELECT code FROM layouts WHERE code IN ({})'.format(
                        ','.join('?' * len(codes))
                    ),
                    tuple(codes),
                )
            }
            avant = conn.execute('SELECT COUNT(*) FROM layouts').fetchone()[0]
            grille_2x2 = conn.execute(
                "SELECT grille_json FROM layouts WHERE code = 'grid_2x2'"
            ).fetchone()['grille_json']
        finally:
            conn.close()

        self.assertEqual(presents, codes)
        models.migrate_db()
        conn = models.get_db()
        try:
            apres = conn.execute('SELECT COUNT(*) FROM layouts').fetchone()[0]
            grille_apres = conn.execute(
                "SELECT grille_json FROM layouts WHERE code = 'grid_2x2'"
            ).fetchone()['grille_json']
        finally:
            conn.close()

        self.assertEqual(apres, avant, 'migrate_db ne doit pas dupliquer les layouts')
        self.assertEqual(grille_apres, grille_2x2, 'un layout existant ne doit pas être modifié')

    def test_geometrie_des_nouveaux_layouts(self):
        import json

        for code, nom, _description, colonnes, lignes, grille_json in models.LAYOUTS_VARIES:
            with self.subTest(layout=code):
                cases = json.loads(grille_json)
                self.assertTrue(cases, f'{code} doit avoir au moins une case')
                occupees = set()
                for case in cases:
                    self.assertGreaterEqual(case['x'], 0, code)
                    self.assertGreaterEqual(case['y'], 0, code)
                    self.assertGreaterEqual(case['w'], 1, code)
                    self.assertGreaterEqual(case['h'], 1, code)
                    self.assertLessEqual(case['x'] + case['w'], colonnes, code)
                    self.assertLessEqual(case['y'] + case['h'], lignes, code)
                    for x in range(case['x'], case['x'] + case['w']):
                        for y in range(case['y'], case['y'] + case['h']):
                            self.assertNotIn((x, y), occupees,
                                             f'{code} : cases qui se chevauchent')
                            occupees.add((x, y))

    def test_slide_sans_config_conserve_le_rendu_egalitaire(self):
        slide = self._creer_slide('grid_2x2')
        self.assertEqual(slide['layout_config'], {})

        slide_lu = self.client.get(f"/api/slides/{slide['id']}").get_json()['data']
        self.assertEqual(slide_lu['layout_config'], {})

    def test_api_normalise_et_relit_la_configuration(self):
        slide = self._creer_slide(
            'grid_3x2',
            {'cols': [2, 1, 1], 'rows': [1, 3]},
        )
        self.assertEqual(slide['layout_config']['cols'], [0.5, 0.25, 0.25])
        self.assertEqual(slide['layout_config']['rows'], [0.25, 0.75])

        slide_lu = self.client.get(f"/api/slides/{slide['id']}").get_json()['data']
        self.assertEqual(slide_lu['layout_config'], slide['layout_config'])

        liste = self.client.get('/api/slides').get_json()['data']
        self.assertEqual(liste[0]['layout_config'], slide['layout_config'])

    def test_api_refuse_les_configurations_invalides(self):
        slide = self._creer_slide('grid_3x2')
        url = f"/api/slides/{slide['id']}"

        for config in (
            {'cols': [1, 1]},
            {'cols': [1, 1, 0]},
            {'cols': [1, -1, 1]},
            {'cols': [1, True, 1]},
            {'rows': [0.01, 99]},
            {'cols': '2,1,1'},
        ):
            with self.subTest(config=config):
                response = self.client.put(url, json={'layout_config': config})
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))

        self.assertEqual(
            self.client.get(url).get_json()['data']['layout_config'],
            {},
            'une configuration refusée ne doit rien modifier',
        )

    def test_changement_de_layout_reinitialise_la_personnalisation(self):
        slide = self._creer_slide(
            'grid_3x2',
            {'cols': [2, 1, 1], 'rows': [1, 3]},
        )
        response = self.client.put(
            f"/api/slides/{slide['id']}",
            json={'layout_id': self._layout_id('medium_h')},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()['data']
        self.assertEqual(data['layout_code'], 'medium_h')
        self.assertEqual(data['layout_config'], {})

    def test_migration_ajoute_layout_config_aux_slides_existantes(self):
        if sqlite3.sqlite_version_info < (3, 35):
            self.skipTest('DROP COLUMN nécessite SQLite 3.35+')

        tmpdir = tempfile.mkdtemp(prefix='fabboard-layouts-old-')
        orig = models.DB_PATH
        models.DB_PATH = os.path.join(tmpdir, 'old.db')
        try:
            models.init_db()
            conn = models.get_db()
            try:
                layout_id = conn.execute('SELECT id FROM layouts LIMIT 1').fetchone()['id']
                conn.execute(
                    "INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif) "
                    "VALUES ('Ancienne slide', ?, 1, 30, 1)",
                    (layout_id,),
                )
                conn.execute('ALTER TABLE slides DROP COLUMN layout_config_json')
                conn.commit()
            finally:
                conn.close()

            models.migrate_db()
            models.migrate_db()

            conn = models.get_db()
            try:
                colonnes = [row['name'] for row in conn.execute('PRAGMA table_info(slides)')]
                valeur = conn.execute(
                    "SELECT layout_config_json FROM slides WHERE nom = 'Ancienne slide'"
                ).fetchone()['layout_config_json']
            finally:
                conn.close()

            self.assertIn('layout_config_json', colonnes)
            self.assertEqual(valeur, '{}')
        finally:
            models.DB_PATH = orig
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
