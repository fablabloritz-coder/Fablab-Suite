"""Tests : repartition des surfaces par annee scolaire et multiplicateur d'occurrences.

Ces tests verrouillent les deux mecanismes mis en cause quand un total de m2
parait trop eleve :

- la periode selectionnee doit couper strictement aux bornes de l'annee
  scolaire (aucune saisie d'une autre annee ne doit entrer dans le total) ;
- le multiplicateur d'occurrences doit etre applique une seule fois, que la
  surface vienne des dimensions ou d'une saisie manuelle.
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

# 300 x 200 mm = 0,06 m2 par piece.
SURFACE_PIECE = 0.06


class StatsAnneeScolaireTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-annee-')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabtrack_test.db')
        models.init_db()

        db = models.get_db()
        db.execute(
            "INSERT INTO types_activite (nom, icone, couleur, badge_class, unite_defaut) "
            "VALUES ('Découpe Laser', 'x', '#f00', 'b', 'm²')"
        )
        db.commit()
        cls.type_id = db.execute(
            "SELECT id FROM types_activite WHERE nom='Découpe Laser'"
        ).fetchone()['id']
        db.close()

        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

        cls.debut, cls.fin = models.default_school_year_bounds()
        cls.debut_precedent = models._shift_years(cls.debut, -1)
        cls.fin_precedente = models._shift_years(cls.fin, -1)

        # Annee en cours : 1 + 3 + 5 occurrences, soit 9 x 0,06 = 0,54 m2.
        cls._poster(f'{cls.debut} 09:00', 300, 200, 1)
        cls._poster(f'{cls.debut} 10:00', 300, 200, 3)
        cls._poster(f'{cls.debut} 11:00', 300, 200, 5)
        # Annee precedente : 1 piece, 0,06 m2.
        cls._poster(f'{cls.debut_precedent} 09:00', 300, 200, 1)

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    @classmethod
    def _poster(cls, date_saisie, longueur_mm, largeur_mm, occurrences):
        action = {
            'type_activite_id': cls.type_id,
            'longueur_mm': longueur_mm,
            'largeur_mm': largeur_mm,
            'surface_m2': None,
            'occurrence_count': occurrences,
        }
        reponse = cls.client.post('/api/consommations/batch', json={
            'date_saisie': date_saisie,
            'referent_ids': [],
            'projet_nom': 'test-annee',
            'preparateur_id': 1,
            'actions': [action],
        })
        assert reponse.status_code == 201, reponse.data

    def _surface(self, **params):
        reponse = self.client.get('/api/stats/summary', query_string=params)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()['total_decoupe_m2']

    def test_les_surfaces_sont_coupees_aux_bornes_de_l_annee_scolaire(self):
        self.assertAlmostEqual(
            self._surface(date_debut=self.debut, date_fin=self.fin),
            9 * SURFACE_PIECE, places=3)
        self.assertAlmostEqual(
            self._surface(date_debut=self.debut_precedent, date_fin=self.fin_precedente),
            SURFACE_PIECE, places=3)
        self.assertAlmostEqual(self._surface(), 10 * SURFACE_PIECE, places=3)

    def test_le_total_par_annee_scolaire_separe_les_periodes(self):
        reponse = self.client.get('/api/stats/school-years')
        self.assertEqual(reponse.status_code, 200)
        corps = reponse.get_json()
        par_label = {annee['label']: annee for annee in corps['data']}

        courante = par_label[f"{self.debut[:4]}-{self.fin[:4]}"]
        precedente = par_label[f"{self.debut_precedent[:4]}-{self.fin_precedente[:4]}"]

        self.assertAlmostEqual(courante['total_decoupe_m2'], 9 * SURFACE_PIECE, places=3)
        self.assertAlmostEqual(precedente['total_decoupe_m2'], SURFACE_PIECE, places=3)

    def test_le_multiplicateur_d_occurrences_n_est_applique_qu_une_fois(self):
        db = models.get_db()
        try:
            row = db.execute(
                'SELECT surface_m2, longueur_mm, largeur_mm, quantite FROM consommations '
                'WHERE quantite = 5 ORDER BY id DESC LIMIT 1'
            ).fetchone()
        finally:
            db.close()

        self.assertIsNotNone(row, 'la saisie a 5 occurrences est introuvable')
        self.assertAlmostEqual(float(row['surface_m2']), 5 * SURFACE_PIECE, places=4)
        self.assertAlmostEqual(float(row['longueur_mm']), 300.0, places=1)
        self.assertAlmostEqual(float(row['largeur_mm']), 200.0, places=1)


if __name__ == '__main__':
    unittest.main()
