"""Tests : saisie d'un prêt — inventaire lié et texte libre (zone séparées, v1.8.0/1.8.1)."""
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


class SaisiePretTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-saisie-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-saisie')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cls.personne_id = conn.execute(
                "INSERT INTO personnes (nom, prenom, categorie) VALUES ('Durand', 'Alice', 'enseignant')"
            ).lastrowid
            cls.materiel_id = conn.execute(
                "INSERT INTO inventaire (numero_inventaire, type_materiel, marque, etat, actif) "
                "VALUES ('INV-1', 'PC portable', 'Dell', 'disponible', 1)"
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _creer(self, data):
        payload = {'personne_id': str(self.personne_id), 'duree_type': 'jours', 'duree_jours': '7'}
        payload.update(data)
        return self.client.post('/nouveau-pret', data=payload)

    def _dernier_pret(self):
        conn = database.get_db()
        try:
            return conn.execute('SELECT * FROM prets ORDER BY id DESC LIMIT 1').fetchone()
        finally:
            conn.close()

    def _liens(self, pret_id):
        conn = database.get_db()
        try:
            return conn.execute(
                'SELECT description, materiel_id FROM pret_materiels WHERE pret_id = ?', (pret_id,)
            ).fetchall()
        finally:
            conn.close()

    def test_inventaire_lie_sans_texte_libre(self):
        """Un prêt peut se limiter à l'inventaire lié : aucune ligne de texte libre."""
        resp = self._creer({
            'linked_items_description[]': 'PC portable (INV-1)',
            'linked_items_materiel_id[]': str(self.materiel_id),
            'free_text_items[]': '',
        })
        self.assertIn(resp.status_code, (200, 302), resp.data[:300])

        pret = self._dernier_pret()
        self.assertIsNotNone(pret)
        self.assertEqual(pret['descriptif_objets'], 'PC portable (INV-1)')
        liens = self._liens(pret['id'])
        self.assertEqual(len(liens), 1)
        self.assertEqual(liens[0]['materiel_id'], self.materiel_id)

    def test_texte_libre_sans_inventaire(self):
        """Symétriquement, une ligne de texte libre suffit à créer un prêt sans lien stock."""
        resp = self._creer({
            'linked_items_description[]': '',
            'linked_items_materiel_id[]': '',
            'free_text_items[]': 'Cable HDMI',
        })
        self.assertIn(resp.status_code, (200, 302), resp.data[:300])

        pret = self._dernier_pret()
        self.assertIsNotNone(pret)
        self.assertEqual(pret['descriptif_objets'], 'Cable HDMI')
        liens = self._liens(pret['id'])
        self.assertEqual(len(liens), 1)
        self.assertIsNone(liens[0]['materiel_id'], 'aucun lien stock pour un texte libre')

    def _compter_prets(self):
        conn = database.get_db()
        try:
            return conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0]
        finally:
            conn.close()

    def test_aucun_objet_refuse(self):
        """Ni inventaire lié ni texte libre : le prêt n'est pas créé."""
        avant = self._compter_prets()
        resp = self._creer({
            'linked_items_description[]': '',
            'linked_items_materiel_id[]': '',
            'free_text_items[]': '',
        })
        self.assertIn(resp.status_code, (200, 302), resp.data[:300])
        self.assertEqual(self._compter_prets(), avant)


if __name__ == '__main__':
    unittest.main()
