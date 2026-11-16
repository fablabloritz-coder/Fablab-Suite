"""Tests : seuils d'alerte par categorie d'activite.

Couvre l'enregistrement des seuils (table `parametres`, sans migration de
schema) et leur application au tableau des plus grosses saisies, unite par
unite : g pour l'impression 3D, m2 pour la decoupe, feuilles pour le papier.
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


class SeuilsAlerteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-seuils-')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabtrack_test.db')
        models.init_db()

        db = models.get_db()
        types = {
            'laser': ('Découpe Laser', 'm²'),
            '3d': ('Impression 3D', 'g'),
            'papier': ('Impression Papier', 'feuilles'),
            'bricolage': ('Bricolage', ''),
        }
        cls.type_ids = {}
        for cle, (nom, unite) in types.items():
            db.execute(
                'INSERT INTO types_activite (nom, unite_defaut) VALUES (?,?)',
                (nom, unite),
            )
            cls.type_ids[cle] = db.execute(
                'SELECT id FROM types_activite WHERE nom=?', (nom,)
            ).fetchone()['id']

        lignes = [
            ('2026-09-10 10:00', 'laser', 0.06, None, None),
            ('2026-09-11 10:00', 'laser', 600.0, 300, 200),
            ('2026-09-12 10:00', '3d', None, None, None),
            ('2026-09-13 10:00', 'papier', None, None, None),
            ('2026-09-14 10:00', '3d', None, None, None),
            ('2026-09-15 10:00', 'papier', None, None, None),
        ]
        for date_saisie, cle, surface, longueur, largeur in lignes:
            db.execute(
                '''INSERT INTO consommations
                   (date_saisie, type_activite_id, surface_m2, longueur_mm,
                    largeur_mm, projet_nom)
                   VALUES (?,?,?,?,?,?)''',
                (date_saisie, cls.type_ids[cle], surface, longueur, largeur, 'Projet'),
            )
        # Quantites 3D et papier, ajoutees apres coup pour rester lisible.
        db.execute(
            "UPDATE consommations SET poids_grammes=300 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-12%'",
            (cls.type_ids['3d'],),
        )
        db.execute(
            "UPDATE consommations SET poids_grammes=1200 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-14%'",
            (cls.type_ids['3d'],),
        )
        db.execute(
            "UPDATE consommations SET nb_feuilles=12 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-13%'",
            (cls.type_ids['papier'],),
        )
        db.execute(
            "UPDATE consommations SET nb_feuilles=80 WHERE type_activite_id=? AND date_saisie LIKE '2026-09-15%'",
            (cls.type_ids['papier'],),
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

    def _seuils(self):
        reponse = self.client.get('/api/stats/seuils')
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()['categories']

    def _enregistrer(self, seuils):
        reponse = self.client.put('/api/stats/seuils', json={'seuils': seuils})
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()['categories']

    def _saisies(self, **params):
        params.setdefault('date_debut', '2026-09-01')
        params.setdefault('date_fin', '2026-09-30')
        reponse = self.client.get('/api/stats/surfaces', query_string=params)
        self.assertEqual(reponse.status_code, 200, reponse.data)
        return reponse.get_json()

    def test_les_categories_sans_unite_de_quantite_sont_ignorees(self):
        categories = self._seuils()
        noms = {cat['nom'] for cat in categories}
        self.assertIn('Découpe Laser', noms)
        self.assertIn('Impression 3D', noms)
        self.assertIn('Impression Papier', noms)
        self.assertNotIn('Bricolage', noms)
        unites = {cat['nom']: cat['unite'] for cat in categories}
        self.assertEqual(unites['Impression 3D'], 'g')
        self.assertEqual(unites['Découpe Laser'], 'm²')

    def test_les_seuils_sont_enregistres_et_relus(self):
        categories = self._enregistrer({
            str(self.type_ids['3d']): '500',
            str(self.type_ids['papier']): 50,
            str(self.type_ids['laser']): '',
        })
        par_nom = {cat['nom']: cat for cat in categories}
        self.assertEqual(float(par_nom['Impression 3D']['seuil']), 500.0)
        self.assertEqual(float(par_nom['Impression Papier']['seuil']), 50.0)
        self.assertIsNone(par_nom['Découpe Laser']['seuil'])
        self.assertEqual(models.get_seuils_alerte()[self.type_ids['3d']], 500.0)

    def test_les_valeurs_invalides_ne_creent_pas_de_seuil(self):
        self._enregistrer({str(self.type_ids['3d']): 'abc'})
        self.assertNotIn(self.type_ids['3d'], models.get_seuils_alerte())

    def test_une_saisie_3d_au_dessus_du_seuil_est_signalee(self):
        self._enregistrer({str(self.type_ids['3d']): 500})
        corps = self._saisies(type_id=self.type_ids['3d'])

        self.assertEqual(corps['unite'], 'g')
        self.assertEqual(corps['seuil'], 500.0)
        valeurs = [row['valeur'] for row in corps['rows']]
        self.assertEqual(valeurs, [1200.0, 300.0])
        self.assertTrue(all(row['unite'] == 'g' for row in corps['rows']))
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])
        self.assertEqual(corps['suspectes'], 1)
        self.assertEqual(corps['count'], 2)

    def test_une_saisie_papier_au_dessus_du_seuil_est_signalee(self):
        self._enregistrer({str(self.type_ids['papier']): 50})
        corps = self._saisies(type_id=self.type_ids['papier'])

        self.assertEqual(corps['unite'], 'feuilles')
        valeurs = [row['valeur'] for row in corps['rows']]
        self.assertEqual(valeurs, [80.0, 12.0])
        self.assertEqual([row['depasse'] for row in corps['rows']], [True, False])

    def test_une_decoupe_au_dessus_du_seuil_reste_signalee(self):
        self._enregistrer({str(self.type_ids['laser']): 2})
        corps = self._saisies(type_id=self.type_ids['laser'])

        self.assertEqual(corps['unite'], 'm²')
        self.assertEqual(corps['rows'][0]['valeur'], 600.0)
        self.assertTrue(corps['rows'][0]['depasse'])
        self.assertEqual(corps['rows'][0]['alerte'], 'unite')
        # Une ligne signalee reste comptee une seule fois, meme si elle cumule
        # depassement de seuil et incoherence d'unite.
        self.assertEqual(corps['suspectes'], 1)

    def test_une_categorie_sans_unite_est_refusee(self):
        reponse = self.client.get('/api/stats/surfaces', query_string={
            'type_id': self.type_ids['bricolage'],
        })
        self.assertEqual(reponse.status_code, 400)

    def test_une_categorie_inconnue_est_refusee(self):
        reponse = self.client.get('/api/stats/surfaces', query_string={'type_id': 999999})
        self.assertEqual(reponse.status_code, 404)


if __name__ == '__main__':
    unittest.main()
