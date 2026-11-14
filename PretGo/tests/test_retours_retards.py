"""Tests : retour d'un prêt, sortie d'un objet d'un prêt en cours, prolongation et retards.

PretGo ne gère pas de retour partiel objet par objet : un retour clôt le prêt et
libère tous ses objets. Rendre un seul objet se fait en modifiant le prêt pour
retirer cet objet (test_retrait_d_un_objet_libere_seulement_celui_ci).
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
from utils import lister_prets_pour_rappel_mail  # noqa: E402


class RetoursRetardsTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-retours-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-retours')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cls.personne_id = conn.execute(
                "INSERT INTO personnes (nom, prenom, email, categorie) "
                "VALUES ('Moreau', 'Lea', 'lea.moreau@example.org', 'eleve')"
            ).lastrowid
            cls.materiel_a = conn.execute(
                "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
                "VALUES ('RET-A', 'Informatique', 'disponible', 1)"
            ).lastrowid
            cls.materiel_b = conn.execute(
                "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
                "VALUES ('RET-B', 'Informatique', 'disponible', 1)"
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

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
            conn.execute("UPDATE inventaire SET etat = 'disponible'")
            conn.commit()
        finally:
            conn.close()

    # ── Helpers ──────────────────────────────────────────────
    def _inserer_pret(self, objets, debut, jours=7):
        """Crée un prêt directement en base pour maîtriser la date d'emprunt."""
        conn = database.get_db()
        try:
            pret_id = conn.execute(
                "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, "
                "duree_pret_jours, type_duree, retour_confirme) VALUES (?, ?, ?, ?, 'jours', 0)",
                (self.personne_id,
                 ' + '.join(f'Informatique ({num})' for num, _ in objets),
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

    def _pret(self, pret_id):
        conn = database.get_db()
        try:
            return conn.execute('SELECT * FROM prets WHERE id = ?', (pret_id,)).fetchone()
        finally:
            conn.close()

    def _etats(self):
        conn = database.get_db()
        try:
            return {row['numero_inventaire']: row['etat'] for row in conn.execute(
                'SELECT numero_inventaire, etat FROM inventaire'
            ).fetchall()}
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

    def _items_du_pret(self, pret_id):
        conn = database.get_db()
        try:
            return [row['materiel_id'] for row in conn.execute(
                'SELECT materiel_id FROM pret_materiels WHERE pret_id = ?', (pret_id,)
            ).fetchall()]
        finally:
            conn.close()

    # ── Retour complet ───────────────────────────────────────
    def test_retour_confirme_et_libere_tous_les_objets(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a), ('RET-B', self.materiel_b)],
            datetime.datetime.now(),
        )

        resp = self.client.post(f'/retour/{pret_id}', data={'signature': 'Lea Moreau'},
                                follow_redirects=True)
        self.assertIn('Retour confirmé avec succès', resp.get_data(as_text=True))

        pret = self._pret(pret_id)
        self.assertEqual(pret['retour_confirme'], 1)
        self.assertIsNotNone(pret['date_retour'])
        self.assertEqual(pret['signature_retour'], 'Lea Moreau')
        self.assertEqual(self._etats(), {'RET-A': 'disponible', 'RET-B': 'disponible'})

    def test_retour_masse_confirme_plusieurs_prets(self):
        premier = self._inserer_pret([('RET-A', self.materiel_a)], datetime.datetime.now())
        second = self._inserer_pret([('RET-B', self.materiel_b)], datetime.datetime.now())

        resp = self.client.post('/retour/masse',
                                data={'pret_ids': [str(premier), str(second)]},
                                follow_redirects=True)
        self.assertIn('2 retour(s) confirmé(s)', resp.get_data(as_text=True))
        self.assertEqual(self._pret(premier)['retour_confirme'], 1)
        self.assertEqual(self._pret(second)['retour_confirme'], 1)
        self.assertEqual(self._etats(), {'RET-A': 'disponible', 'RET-B': 'disponible'})

    # ── Retour partiel objet par objet ───────────────────────
    def test_retour_partiel_garde_le_pret_ouvert(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a), ('RET-B', self.materiel_b)],
            datetime.datetime.now(),
        )
        pm_a = self._pm(pret_id, self.materiel_a)['id']

        fiche = self.client.get(f'/pret/{pret_id}')
        self.assertEqual(fiche.status_code, 200)
        self.assertEqual(fiche.get_data(as_text=True).count('Rendre cet objet'), 2)

        resp = self.client.post(f'/pret/{pret_id}/retour-objet/{pm_a}', follow_redirects=True)
        texte = resp.get_data(as_text=True)
        self.assertIn('1 objet(s) encore emprunté(s)', texte)
        self.assertIn('Rendu', texte)
        self.assertEqual(texte.count('Rendre cet objet'), 1)

        pret = self._pret(pret_id)
        self.assertEqual(pret['retour_confirme'], 0)
        self.assertIsNone(pret['date_retour'])
        self.assertEqual(self._etats(), {'RET-A': 'disponible', 'RET-B': 'prete'})
        self.assertEqual(self._pm(pret_id, self.materiel_a)['retour_confirme'], 1)
        self.assertIsNotNone(self._pm(pret_id, self.materiel_a)['date_retour'])
        self.assertEqual(self._pm(pret_id, self.materiel_b)['retour_confirme'], 0)

    def test_retour_du_dernier_objet_cloture_le_pret(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a), ('RET-B', self.materiel_b)],
            datetime.datetime.now(),
        )
        pm_a = self._pm(pret_id, self.materiel_a)['id']
        pm_b = self._pm(pret_id, self.materiel_b)['id']

        self.client.post(f'/pret/{pret_id}/retour-objet/{pm_a}', follow_redirects=True)
        resp = self.client.post(f'/pret/{pret_id}/retour-objet/{pm_b}', follow_redirects=True)
        self.assertIn('le prêt est clôturé', resp.get_data(as_text=True))

        pret = self._pret(pret_id)
        self.assertEqual(pret['retour_confirme'], 1)
        self.assertIsNotNone(pret['date_retour'])
        self.assertEqual(self._etats(), {'RET-A': 'disponible', 'RET-B': 'disponible'})

    def test_retour_d_un_objet_deja_rendu_refuse(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a), ('RET-B', self.materiel_b)],
            datetime.datetime.now(),
        )
        pm_a = self._pm(pret_id, self.materiel_a)['id']

        self.client.post(f'/pret/{pret_id}/retour-objet/{pm_a}', follow_redirects=True)
        resp = self.client.post(f'/pret/{pret_id}/retour-objet/{pm_a}', follow_redirects=True)
        self.assertIn('déjà été rendu', resp.get_data(as_text=True))

        self.assertEqual(self._pret(pret_id)['retour_confirme'], 0)
        self.assertEqual(self._etats(), {'RET-A': 'disponible', 'RET-B': 'prete'})

    # ── Sortie d'un objet seul par modification du prêt ──────
    def test_retrait_d_un_objet_libere_seulement_celui_ci(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a), ('RET-B', self.materiel_b)],
            datetime.datetime.now(),
        )

        resp = self.client.post(f'/pret/modifier/{pret_id}', data={
            'personne_id': str(self.personne_id),
            'linked_items_description[]': 'Informatique (RET-A)',
            'linked_items_materiel_id[]': str(self.materiel_a),
            'duree_type': 'jours',
            'duree_jours': '7',
        }, follow_redirects=True)
        self.assertIn('Prêt modifié avec succès', resp.get_data(as_text=True))

        self.assertEqual(self._pret(pret_id)['retour_confirme'], 0)
        self.assertEqual(self._items_du_pret(pret_id), [self.materiel_a])
        self.assertEqual(self._etats(), {'RET-A': 'prete', 'RET-B': 'disponible'})

    # ── Prolongation via la modification du prêt ─────────────
    def test_prolongation_repousse_l_echeance_et_sort_du_retard(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a)],
            datetime.datetime.now() - datetime.timedelta(days=10),
            jours=7,
        )
        conn = database.get_db()
        try:
            avant = lister_prets_pour_rappel_mail(conn, pret_ids=[pret_id])
        finally:
            conn.close()
        self.assertEqual([c['reminder_kind'] for c in avant], ['overdue'])

        resp = self.client.post(f'/pret/modifier/{pret_id}', data={
            'personne_id': str(self.personne_id),
            'linked_items_description[]': 'Informatique (RET-A)',
            'linked_items_materiel_id[]': str(self.materiel_a),
            'duree_type': 'jours',
            'duree_jours': '30',
        }, follow_redirects=True)
        self.assertIn('Prêt modifié avec succès', resp.get_data(as_text=True))
        self.assertEqual(self._pret(pret_id)['duree_pret_jours'], 30)

        conn = database.get_db()
        try:
            apres = lister_prets_pour_rappel_mail(conn, pret_ids=[pret_id])
        finally:
            conn.close()
        self.assertEqual(apres, [])

    # ── Calcul des retards ───────────────────────────────────
    def test_pret_en_retard_liste_pour_rappel(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a)],
            datetime.datetime.now() - datetime.timedelta(days=10),
            jours=7,
        )
        conn = database.get_db()
        try:
            candidats = lister_prets_pour_rappel_mail(conn, pret_ids=[pret_id])
        finally:
            conn.close()

        self.assertEqual(len(candidats), 1)
        candidat = candidats[0]
        self.assertEqual(candidat['reminder_kind'], 'overdue')
        self.assertEqual(candidat['reminder_label'], 'En retard')
        self.assertGreater(candidat['delta_heures'], 48)
        self.assertIn('jour', candidat['delta_label'])

    def test_pret_non_echu_absent_des_retards(self):
        pret_id = self._inserer_pret(
            [('RET-A', self.materiel_a)],
            datetime.datetime.now() - datetime.timedelta(days=1),
            jours=7,
        )
        conn = database.get_db()
        try:
            candidats = lister_prets_pour_rappel_mail(conn, pret_ids=[pret_id])
        finally:
            conn.close()

        self.assertEqual(candidats, [])


if __name__ == '__main__':
    unittest.main()
