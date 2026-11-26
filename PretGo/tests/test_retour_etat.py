"""Tests : état constaté au retour d'un prêt (disponible, en panne, réformé).

Le retour complet, le retour partiel et la fiche matériel doivent conserver
l'état choisi et son motif ; le retour en masse reste sur « disponible ».
Un matériel en panne ou réformé ne doit plus être proposé aux réservations.
"""
import datetime
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
from reservations_logic import _stock_capacities  # noqa: E402


class RetourEtatTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-retour-etat-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-retour-etat')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cls.personne_id = conn.execute(
                "INSERT INTO personnes (nom, prenom, email, categorie) "
                "VALUES ('Martin', 'Noe', 'noe.martin@example.org', 'eleve')"
            ).lastrowid
            cls.materiel_a = cls._inserer_materiel(conn, 'ETAT-A')
            cls.materiel_b = cls._inserer_materiel(conn, 'ETAT-B')
            cls.materiel_c = cls._inserer_materiel(conn, 'ETAT-C')
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _inserer_materiel(cls, conn, numero):
        return conn.execute(
            "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
            "VALUES (?, 'Informatique', 'disponible', 1)",
            (numero,),
        ).lastrowid

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess['admin_logged_in'] = True

        conn = database.get_db()
        try:
            conn.execute('DELETE FROM reservations')
            conn.execute('DELETE FROM pret_materiels')
            conn.execute('DELETE FROM prets')
            conn.execute(
                "UPDATE inventaire SET etat = 'disponible', etat_motif = '', "
                "etat_modifie_at = '', actif = 1"
            )
            conn.commit()
        finally:
            conn.close()

    # ── Helpers ──────────────────────────────────────────────
    def _inserer_pret(self, objets, debut=None, jours=7):
        debut = debut or datetime.datetime.now()
        conn = database.get_db()
        try:
            pret_id = conn.execute(
                "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, "
                "duree_pret_jours, type_duree, retour_confirme) VALUES (?, ?, ?, ?, 'jours', 0)",
                (self.personne_id,
                 ' + '.join(f'Informatique ({numero})' for numero, _ in objets),
                 debut.strftime('%Y-%m-%d %H:%M:%S'), jours),
            ).lastrowid
            for numero, materiel_id in objets:
                conn.execute(
                    'INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?, ?, ?)',
                    (pret_id, materiel_id, f'Informatique ({numero})'),
                )
                conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (materiel_id,))
            conn.commit()
            return pret_id
        finally:
            conn.close()

    def _materiel(self, materiel_id):
        conn = database.get_db()
        try:
            return conn.execute('SELECT * FROM inventaire WHERE id = ?', (materiel_id,)).fetchone()
        finally:
            conn.close()

    def _pret(self, pret_id):
        conn = database.get_db()
        try:
            return conn.execute('SELECT * FROM prets WHERE id = ?', (pret_id,)).fetchone()
        finally:
            conn.close()

    def _pm(self, pret_id, materiel_id):
        conn = database.get_db()
        try:
            return conn.execute(
                'SELECT * FROM pret_materiels WHERE pret_id = ? AND materiel_id = ?',
                (pret_id, materiel_id),
            ).fetchone()
        finally:
            conn.close()

    # ── Retour complet ───────────────────────────────────────
    def test_retour_complet_en_reparation_conserve_etat_et_motif(self):
        pret_id = self._inserer_pret([('ETAT-A', self.materiel_a)])

        resp = self.client.post(
            f'/retour/{pret_id}',
            data={
                f'etat_retour_{pret_id}': 'en_reparation',
                f'etat_motif_{pret_id}': 'Écran fissuré',
                'signature': 'Noe Martin',
            },
            follow_redirects=True,
        )
        texte = resp.get_data(as_text=True)
        self.assertIn('Retour confirmé avec succès', texte)
        self.assertIn('marqué en réparation', texte)

        materiel = self._materiel(self.materiel_a)
        self.assertEqual(materiel['etat'], 'en_reparation')
        self.assertEqual(materiel['etat_motif'], 'Écran fissuré')
        self.assertTrue(materiel['etat_modifie_at'])

    def test_retour_sans_etat_reste_disponible(self):
        pret_id = self._inserer_pret([('ETAT-A', self.materiel_a)])

        self.client.post(f'/retour/{pret_id}', data={'signature': 'Noe Martin'},
                         follow_redirects=True)

        materiel = self._materiel(self.materiel_a)
        self.assertEqual(materiel['etat'], 'disponible')
        self.assertEqual(materiel['etat_motif'], '')
        self.assertTrue(materiel['etat_modifie_at'])

    def test_etat_invalide_retombe_sur_disponible(self):
        pret_id = self._inserer_pret([('ETAT-A', self.materiel_a)])

        self.client.post(
            f'/retour/{pret_id}',
            data={
                f'etat_retour_{pret_id}': 'inconnu',
                f'etat_motif_{pret_id}': 'motif ignoré',
                'signature': '',
            },
            follow_redirects=True,
        )

        materiel = self._materiel(self.materiel_a)
        self.assertEqual(materiel['etat'], 'disponible')
        self.assertEqual(materiel['etat_motif'], '')

    # ── Retour partiel ───────────────────────────────────────
    def test_retour_partiel_avec_etat_n_affecte_que_l_objet(self):
        pret_id = self._inserer_pret(
            [('ETAT-A', self.materiel_a), ('ETAT-B', self.materiel_b)]
        )
        pm_a = self._pm(pret_id, self.materiel_a)['id']

        self.client.post(
            f'/pret/{pret_id}/retour-objet/{pm_a}',
            data={'etat_retour': 'en_reparation', 'etat_motif': 'Clavier hors service'},
            follow_redirects=True,
        )

        self.assertEqual(self._materiel(self.materiel_a)['etat'], 'en_reparation')
        self.assertEqual(self._materiel(self.materiel_a)['etat_motif'], 'Clavier hors service')
        self.assertEqual(self._materiel(self.materiel_b)['etat'], 'prete')
        self.assertEqual(self._pret(pret_id)['retour_confirme'], 0)

    def test_dernier_objet_rendu_cloture_le_pret(self):
        pret_id = self._inserer_pret(
            [('ETAT-A', self.materiel_a), ('ETAT-B', self.materiel_b)]
        )
        pm_a = self._pm(pret_id, self.materiel_a)['id']
        pm_b = self._pm(pret_id, self.materiel_b)['id']

        self.client.post(f'/pret/{pret_id}/retour-objet/{pm_a}',
                         data={'etat_retour': 'disponible'}, follow_redirects=True)
        resp = self.client.post(f'/pret/{pret_id}/retour-objet/{pm_b}',
                                data={'etat_retour': 'disponible'}, follow_redirects=True)

        self.assertIn('le prêt est clôturé', resp.get_data(as_text=True))
        self.assertEqual(self._pret(pret_id)['retour_confirme'], 1)
        self.assertEqual(self._materiel(self.materiel_a)['etat'], 'disponible')
        self.assertEqual(self._materiel(self.materiel_b)['etat'], 'disponible')

    # ── Retour en masse ──────────────────────────────────────
    def test_retour_masse_ignore_un_etat_fourni(self):
        pret_id = self._inserer_pret([('ETAT-A', self.materiel_a)])

        self.client.post(
            '/retour/masse',
            data={
                'pret_ids': [str(pret_id)],
                f'etat_retour_{pret_id}': 'en_reparation',
                f'etat_motif_{pret_id}': 'ne doit pas être appliqué',
            },
            follow_redirects=True,
        )

        materiel = self._materiel(self.materiel_a)
        self.assertEqual(materiel['etat'], 'disponible')
        self.assertEqual(materiel['etat_motif'], '')

    # ── Disponibilité et affichage ───────────────────────────
    def test_reservation_exclut_materiel_en_reparation_mais_garde_le_prete(self):
        conn = database.get_db()
        try:
            conn.execute("UPDATE inventaire SET etat = 'en_reparation' WHERE id = ?",
                         (self.materiel_b,))
            conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?",
                         (self.materiel_c,))
            conn.commit()
            capacites = _stock_capacities(conn)
        finally:
            conn.close()

        # A (disponible) + C (prêté, donc de retour un jour) ; B est exclu.
        self.assertEqual(capacites.get('Informatique'), 2)

    def test_scan_signale_le_materiel_en_reparation(self):
        conn = database.get_db()
        try:
            conn.execute("UPDATE inventaire SET etat = 'en_reparation' WHERE id = ?",
                         (self.materiel_a,))
            conn.commit()
        finally:
            conn.close()

        data = self.client.get('/api/scan?code=ETAT-A').get_json()
        self.assertTrue(data['found'])
        self.assertIn('En réparation', data['message'])
        self.assertIn('/inventaire/modifier/', data['url'])

    def test_migration_convertit_les_anciens_libelles(self):
        conn = database.get_db()
        try:
            conn.execute("UPDATE inventaire SET etat = 'en_panne' WHERE id = ?",
                         (self.materiel_a,))
            conn.execute("UPDATE inventaire SET etat = 'reforme' WHERE id = ?",
                         (self.materiel_c,))
            conn.commit()
        finally:
            conn.close()

        database.init_db()

        self.assertEqual(self._materiel(self.materiel_a)['etat'], 'en_reparation')
        self.assertEqual(self._materiel(self.materiel_c)['etat'], 'hors_service')

    def test_migration_ajoute_les_colonnes_sur_une_base_existante(self):
        conn = database.get_db()
        try:
            conn.execute('ALTER TABLE inventaire DROP COLUMN etat_motif')
            conn.execute('ALTER TABLE inventaire DROP COLUMN etat_modifie_at')
            conn.commit()
        finally:
            conn.close()

        database.init_db()

        conn = database.get_db()
        try:
            colonnes = {row[1] for row in conn.execute('PRAGMA table_info(inventaire)')}
        finally:
            conn.close()
        self.assertIn('etat_motif', colonnes)
        self.assertIn('etat_modifie_at', colonnes)

    def test_listes_affichent_les_etats_canoniques(self):
        conn = database.get_db()
        try:
            conn.execute("UPDATE inventaire SET etat = 'en_reparation' WHERE id = ?",
                         (self.materiel_a,))
            conn.execute("UPDATE inventaire SET etat = 'hors_service' WHERE id = ?",
                         (self.materiel_c,))
            conn.commit()
        finally:
            conn.close()

        for url in ('/inventaire', '/etiquettes'):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200, url)
            texte = page.get_data(as_text=True)
            self.assertIn('En réparation', texte, url)
            self.assertIn('Hors service', texte, url)

    def test_fiche_materiel_conserve_et_efface_le_motif(self):
        resp = self.client.post(
            f'/inventaire/modifier/{self.materiel_a}',
            data={
                'type_materiel': 'Informatique',
                'numero_inventaire': 'ETAT-A',
                'etat': 'en_reparation',
                'etat_motif': 'Batterie HS',
                'notes': '',
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        materiel = self._materiel(self.materiel_a)
        self.assertEqual(materiel['etat'], 'en_reparation')
        self.assertEqual(materiel['etat_motif'], 'Batterie HS')
        self.assertTrue(materiel['etat_modifie_at'])

        page = self.client.get(f'/inventaire/historique/{self.materiel_a}')
        texte = page.get_data(as_text=True)
        self.assertIn('Dernier état constaté', texte)
        self.assertIn('Batterie HS', texte)

        formulaire = self.client.get(f'/inventaire/modifier/{self.materiel_a}')
        self.assertEqual(formulaire.status_code, 200)
        self.assertIn('Batterie HS', formulaire.get_data(as_text=True))

        self.client.post(
            f'/inventaire/modifier/{self.materiel_a}',
            data={
                'type_materiel': 'Informatique',
                'numero_inventaire': 'ETAT-A',
                'etat': 'disponible',
                'etat_motif': 'Batterie HS',
                'notes': '',
            },
            follow_redirects=True,
        )
        materiel = self._materiel(self.materiel_a)
        self.assertEqual(materiel['etat'], 'disponible')
        self.assertEqual(materiel['etat_motif'], '')


if __name__ == '__main__':
    unittest.main()
