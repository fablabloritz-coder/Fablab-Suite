"""Tests : classement des plus grosses surfaces et controle des unites.

Couvre l'API qui alimente le tableau « Plus grosses saisies » de l'ecran
Statistiques, ainsi que le diagnostic partage avec `audit_surface.py`.
"""
import os
import shutil
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import app as app_module  # noqa: E402
import models  # noqa: E402
from surface_checks import diagnose_surface  # noqa: E402


class DiagnoseSurfaceTests(unittest.TestCase):

    def test_une_piece_normale_est_coherente(self):
        diag = diagnose_surface(0.06, 300, 200)
        self.assertEqual(diag['alerte'], '')
        self.assertAlmostEqual(diag['surface_dimensions_m2'], 0.06, places=4)
        self.assertFalse(diag['sans_dimensions'])

    def test_une_surface_en_cm2_est_signalee(self):
        diag = diagnose_surface(600.0, 300, 200)
        self.assertEqual(diag['alerte'], 'unite')
        self.assertEqual(diag['facteur'], 10000.0)

    def test_une_surface_en_mm2_est_signalee(self):
        diag = diagnose_surface(60000.0, 300, 200)
        self.assertEqual(diag['alerte'], 'unite')
        self.assertEqual(diag['facteur'], 1000000.0)

    def test_une_grande_surface_sans_dimensions_est_signalee(self):
        diag = diagnose_surface(12.5, None, None)
        self.assertEqual(diag['alerte'], 'trop_grande')
        self.assertTrue(diag['sans_dimensions'])


class StatsSurfacesApiTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-surfaces-')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabtrack_test.db')
        models.init_db()

        db = models.get_db()
        db.execute(
            "INSERT INTO types_activite (nom, unite_defaut) VALUES ('Découpe Laser', 'm²')"
        )
        type_id = db.execute(
            "SELECT id FROM types_activite WHERE nom='Découpe Laser'"
        ).fetchone()['id']

        lignes = [
            ('2026-09-10 10:00', 300, 200, 0.06, 'Projet normal'),
            ('2026-09-11 10:00', 300, 200, 600.0, 'Projet cm2'),
            ('2026-09-12 10:00', 300, 200, 60000.0, 'Projet mm2'),
            ('2026-09-13 10:00', None, None, 12.5, 'Projet manuel'),
            ('2026-09-14 10:00', 600, 400, 0.24, 'Projet grand'),
            ('2024-10-01 10:00', 300, 200, 0.06, 'Projet ancien'),
        ]
        for date_saisie, longueur, largeur, surface, projet in lignes:
            db.execute(
                '''INSERT INTO consommations
                   (date_saisie, type_activite_id, longueur_mm, largeur_mm,
                    surface_m2, projet_nom, nom_type_activite)
                   VALUES (?,?,?,?,?,?,?)''',
                (date_saisie, type_id, longueur, largeur, surface, projet,
                 'Découpe Laser'),
            )
        db.commit()
        db.close()

        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _get(self, **params):
        params.setdefault('date_debut', '2026-09-01')
        params.setdefault('date_fin', '2026-09-30')
        reponse = self.client.get('/api/stats/surfaces', query_string=params)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()

    def test_les_saisies_sont_triees_de_la_plus_grande_a_la_plus_petite(self):
        corps = self._get()
        surfaces = [row['surface_m2'] for row in corps['rows']]
        self.assertEqual(surfaces, sorted(surfaces, reverse=True))
        self.assertEqual(surfaces[0], 60000.0)

    def test_le_tri_ascendant_est_disponible(self):
        corps = self._get(order='asc')
        surfaces = [row['surface_m2'] for row in corps['rows']]
        self.assertEqual(surfaces, sorted(surfaces))

    def test_les_erreurs_d_unite_et_les_grandes_valeurs_sont_signalees(self):
        corps = self._get()
        par_projet = {row['projet']: row for row in corps['rows']}

        self.assertEqual(par_projet['Projet cm2']['alerte'], 'unite')
        self.assertEqual(par_projet['Projet cm2']['facteur'], 10000.0)
        self.assertEqual(par_projet['Projet mm2']['alerte'], 'unite')
        self.assertEqual(par_projet['Projet mm2']['facteur'], 1000000.0)
        self.assertEqual(par_projet['Projet manuel']['alerte'], 'trop_grande')
        self.assertTrue(par_projet['Projet manuel']['sans_dimensions'])
        self.assertEqual(par_projet['Projet normal']['alerte'], '')

        self.assertEqual(corps['count'], 5)
        self.assertEqual(corps['suspectes'], 3)

    def test_la_periode_exclut_les_autres_annees(self):
        corps = self._get()
        projets = {row['projet'] for row in corps['rows']}
        self.assertNotIn('Projet ancien', projets)
        self.assertEqual(corps['count'], 5)

    def test_la_limite_borne_le_nombre_de_lignes(self):
        corps = self._get(limit='2')
        self.assertEqual(len(corps['rows']), 2)
        self.assertEqual(corps['rows'][0]['surface_m2'], 60000.0)
        # Le comptage des alertes reste celui de toute la periode.
        self.assertEqual(corps['count'], 5)
        self.assertEqual(corps['suspectes'], 3)


if __name__ == '__main__':
    unittest.main()
