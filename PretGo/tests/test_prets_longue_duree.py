"""Tests : prêts longue durée (sans date de fin), DB SQLite temporaire."""
import os
import shutil
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ['TESTING'] = '1'

import database  # noqa: E402


class PretsLongueDureeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-longue-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-longue')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO personnes (nom, prenom, categorie) VALUES ('Durand', 'Alice', 'enseignant')")
            cls.personne_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _create(self, objet, duree_type):
        resp = self.client.post('/nouveau-pret', data={
            'personne_id': str(self.personne_id),
            'items_description[]': objet,
            'items_materiel_id[]': '',
            'duree_type': duree_type,
        })
        self.assertIn(resp.status_code, (200, 302), resp.data[:300])
        conn = database.get_db()
        try:
            return conn.execute(
                'SELECT * FROM prets WHERE descriptif_objets = ?', (objet,)).fetchone()
        finally:
            conn.close()

    def test_creation_longue_duree(self):
        pret = self._create('PC portable prof', 'longue_duree')
        self.assertIsNotNone(pret)
        self.assertEqual(pret['type_duree'], 'longue_duree')
        self.assertEqual(pret['date_retour_prevue'], '2999-12-31')

    def test_jamais_en_retard(self):
        from utils import calcul_depassement_heures
        pret = self._create('Tablette prof', 'longue_duree')
        depasse, _ = calcul_depassement_heures(
            '2000-01-01 08:00:00', None, None, date_retour_prevue=pret['date_retour_prevue'])
        self.assertFalse(depasse)

    def test_onglet_et_alertes(self):
        self._create('Casque audio', 'longue_duree')
        self._create('Livre normal', 'jours')
        page = self.client.get('/prets-longue-duree')
        self.assertEqual(page.status_code, 200)
        html = page.data.decode('utf-8')
        self.assertIn('Casque audio', html)
        self.assertNotIn('Livre normal', html)
        self.assertEqual(self.client.get('/alertes').status_code, 200)

    def test_pages_detail_et_modification(self):
        pret = self._create('Clavier', 'longue_duree')
        detail = self.client.get(f"/pret/{pret['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn('sans date de fin', detail.data.decode('utf-8'))
        with self.client.session_transaction() as sess:
            sess['admin_logged_in'] = True
        self.assertEqual(self.client.get(f"/pret/modifier/{pret['id']}").status_code, 200)


if __name__ == '__main__':
    unittest.main()
