"""Tests : conflits de réservations au moment de créer un prêt.

Couvre l'objet déjà prêté, la marge de sécurité avant une réservation précise,
la fenêtre de verrouillage (réservation imminente) et la capacité par catégorie.
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


class ConflitsPretTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-conflits-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-conflits')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cls.personne_id = conn.execute(
                "INSERT INTO personnes (nom, prenom, categorie) VALUES ('Leroy', 'Ines', 'eleve')"
            ).lastrowid
            cls.materiel_a = conn.execute(
                "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
                "VALUES ('CFL-A', 'Informatique', 'disponible', 1)"
            ).lastrowid
            cls.materiel_b = conn.execute(
                "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
                "VALUES ('CFL-B', 'Informatique', 'disponible', 1)"
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

        # Base vierge + politique de réservation connue (défauts du produit).
        conn = database.get_db()
        try:
            conn.execute('DELETE FROM reservations')
            conn.execute('DELETE FROM pret_materiels')
            conn.execute('DELETE FROM prets')
            conn.execute("UPDATE inventaire SET etat = 'disponible'")
            conn.commit()
            database.set_setting('reservation_buffer_hours', '24', conn=conn)
            database.set_setting('reservation_lock_window_hours', '48', conn=conn)
        finally:
            conn.close()

    # ── Helpers ──────────────────────────────────────────────
    def _creneau(self, heures):
        debut = datetime.datetime.now() + datetime.timedelta(hours=heures)
        fin = debut + datetime.timedelta(hours=2)
        return debut.strftime('%Y-%m-%dT%H:%M'), fin.strftime('%Y-%m-%dT%H:%M')

    def _reserver_objet(self, heures, materiel_id=None, numero='CFL-A'):
        materiel_id = materiel_id or self.materiel_a
        debut, fin = self._creneau(heures)
        self.client.post('/reservations', data={
            'personne_id': str(self.personne_id),
            'date_reservation': debut,
            'date_fin_reservation': fin,
            'statut': 'confirmee',
            'res_items_description[]': f'Informatique ({numero})',
            'res_items_materiel_id[]': str(materiel_id),
        }, follow_redirects=True)

    def _reserver_categorie(self, heures, quantite):
        debut, fin = self._creneau(heures)
        self.client.post('/reservations', data={
            'personne_id': str(self.personne_id),
            'date_reservation': debut,
            'date_fin_reservation': fin,
            'statut': 'confirmee',
            'res_category_name[]': 'Informatique',
            'res_category_qty[]': str(quantite),
        }, follow_redirects=True)

    def _preter(self, materiel_id=None, numero='CFL-A', heures=None, jours=None):
        materiel_id = materiel_id or self.materiel_a
        data = {
            'personne_id': str(self.personne_id),
            'linked_items_description[]': f'Informatique ({numero})',
            'linked_items_materiel_id[]': str(materiel_id),
            'duree_type': 'heures' if heures is not None else 'jours',
            'duree_heures': str(heures or ''),
            'duree_jours': str(jours or ''),
        }
        return self.client.post('/nouveau-pret', data=data, follow_redirects=True)

    def _prets(self):
        conn = database.get_db()
        try:
            return conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0]
        finally:
            conn.close()

    # ── Objet déjà prêté ─────────────────────────────────────
    def test_pret_refuse_si_objet_deja_prete(self):
        conn = database.get_db()
        try:
            conn.execute("UPDATE inventaire SET etat = 'prete' WHERE id = ?", (self.materiel_a,))
            conn.commit()
        finally:
            conn.close()

        resp = self._preter(jours=1)
        self.assertIn("déjà emprunté(s) ou indisponible(s)", resp.get_data(as_text=True))
        self.assertEqual(self._prets(), 0)

    # ── Marge de sécurité (réservation précise) ──────────────
    def test_retour_tardif_bloque_par_la_marge(self):
        self._reserver_objet(heures=120)
        resp = self._preter(jours=7)
        self.assertIn("ne peut pas être prêté si tard", resp.get_data(as_text=True))
        self.assertIn('Prêt refusé: conflit avec une réservation future', resp.get_data(as_text=True))
        self.assertEqual(self._prets(), 0)

    def test_retour_avant_la_marge_accepte(self):
        self._reserver_objet(heures=120)
        resp = self._preter(jours=1)
        self.assertIn('Prêt enregistré avec succès', resp.get_data(as_text=True))
        self.assertEqual(self._prets(), 1)

    # ── Réservation imminente (fenêtre de verrouillage) ──────
    def test_reservation_imminente_bloque_meme_un_pret_court(self):
        self._reserver_objet(heures=3)
        resp = self._preter(heures=1)
        self.assertIn('est réservé précisément et bloqué', resp.get_data(as_text=True))
        self.assertEqual(self._prets(), 0)

    # ── Capacité par catégorie ───────────────────────────────
    def test_capacite_insuffisante_dans_la_fenetre_de_verrouillage(self):
        self._reserver_categorie(heures=6, quantite=2)
        resp = self._preter(heures=1)
        self.assertIn('capacité insuffisante pour la réservation proche', resp.get_data(as_text=True))
        self.assertEqual(self._prets(), 0)

    def test_capacite_insuffisante_dans_la_marge(self):
        self._reserver_categorie(heures=120, quantite=2)
        resp = self._preter(jours=7)
        texte = resp.get_data(as_text=True)
        self.assertIn('retour prévu trop tard pour la réservation du', texte)
        self.assertIn('marge 24h', texte)
        self.assertEqual(self._prets(), 0)


if __name__ == '__main__':
    unittest.main()
