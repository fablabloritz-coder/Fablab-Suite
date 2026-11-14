"""Tests : cycle de vie admin d'une réservation (conversion, annulation, suppression)."""
import datetime
import json
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


class ReservationsAdminTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-resa-admin-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-resa-admin')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cls.personne_id = conn.execute(
                "INSERT INTO personnes (nom, prenom, categorie) VALUES ('Bernard', 'Hugo', 'enseignant')"
            ).lastrowid
            cls.materiel_id = conn.execute(
                "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
                "VALUES ('ADM-1', 'Videoprojecteur', 'disponible', 1)"
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

        # Chaque test part d'une base vierge : un prêt actif bloquerait les réservations.
        conn = database.get_db()
        try:
            # Ordre imposé par les clés étrangères : réservations -> prêts.
            conn.execute('DELETE FROM reservations')
            conn.execute('DELETE FROM pret_materiels')
            conn.execute('DELETE FROM prets')
            conn.execute("UPDATE inventaire SET etat = 'disponible'")
            conn.commit()
        finally:
            conn.close()

    # ── Helpers ──────────────────────────────────────────────
    def _creneau(self, jours):
        jour = (datetime.datetime.now() + datetime.timedelta(days=jours)).strftime('%Y-%m-%d')
        return f'{jour}T09:00', f'{jour}T17:00'

    def _creer_reservation(self, jours=2, **extra):
        debut, fin = self._creneau(jours)
        data = {'personne_id': str(self.personne_id), 'date_reservation': debut,
                'date_fin_reservation': fin, 'statut': 'confirmee',
                'res_items_description[]': 'Videoprojecteur (ADM-1)',
                'res_items_materiel_id[]': str(self.materiel_id)}
        data.update(extra)
        self.client.post('/reservations', data=data, follow_redirects=True)
        conn = database.get_db()
        try:
            reservation = conn.execute('SELECT * FROM reservations ORDER BY id DESC LIMIT 1').fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(reservation, 'la réservation de test doit être créée')
        return reservation

    def _reservation(self, reservation_id):
        conn = database.get_db()
        try:
            return conn.execute('SELECT * FROM reservations WHERE id = ?', (reservation_id,)).fetchone()
        finally:
            conn.close()

    def _prets(self):
        conn = database.get_db()
        try:
            return conn.execute('SELECT COUNT(*) FROM prets').fetchone()[0]
        finally:
            conn.close()

    # ── Conversion ───────────────────────────────────────────
    def test_conversion_refusee_avant_la_date_de_depart(self):
        reservation = self._creer_reservation(jours=3)
        resp = self.client.get(
            f"/reservations/{reservation['id']}/convertir?action=convert",
            follow_redirects=True,
        )
        self.assertIn("n'est pas encore atteinte", resp.get_data(as_text=True))
        self.assertNotIn('/nouveau-pret', resp.request.path)

    def test_conversion_forcee_puis_creation_du_pret(self):
        reservation = self._creer_reservation(jours=3)
        rid = reservation['id']

        redirection = self.client.get(f'/reservations/{rid}/convertir?action=force')
        self.assertEqual(redirection.status_code, 302)
        self.assertIn('/nouveau-pret', redirection.headers['Location'])

        apres_force = self._reservation(rid)
        self.assertLessEqual(apres_force['date_reservation'], datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        avant = self._prets()
        creation = self.client.post('/nouveau-pret', data={
            'reservation_id': str(rid),
            'personne_id': str(self.personne_id),
            'linked_items_description[]': 'Videoprojecteur (ADM-1)',
            'linked_items_materiel_id[]': str(self.materiel_id),
            'duree_type': 'jours', 'duree_jours': '7',
        })
        self.assertIn(creation.status_code, (200, 302), creation.data[:300])
        self.assertEqual(self._prets(), avant + 1)

        convertie = self._reservation(rid)
        self.assertEqual(convertie['statut'], 'convertie_en_pret')
        self.assertIsNotNone(convertie['pret_id'])

    # ── Annulation ───────────────────────────────────────────
    def test_annulation_puis_annulation_refusee(self):
        reservation = self._creer_reservation(jours=4)
        rid = reservation['id']

        resp = self.client.post(f'/reservations/{rid}/annuler', follow_redirects=True)
        self.assertIn('Réservation annulée', resp.get_data(as_text=True))
        self.assertEqual(self._reservation(rid)['statut'], 'annulee')

        encore = self.client.post(f'/reservations/{rid}/annuler', follow_redirects=True)
        self.assertIn('déjà clôturée', encore.get_data(as_text=True))

    # ── Suppression ──────────────────────────────────────────
    def test_suppression(self):
        reservation = self._creer_reservation(jours=5)
        rid = reservation['id']

        resp = self.client.post(f'/reservations/{rid}/supprimer', follow_redirects=True)
        self.assertIn('Réservation supprimée', resp.get_data(as_text=True))
        self.assertIsNone(self._reservation(rid))

    def test_suppression_refusee_si_liee_a_un_pret(self):
        reservation = self._creer_reservation(jours=6)
        rid = reservation['id']
        conn = database.get_db()
        try:
            pret_id = conn.execute(
                "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt) "
                "VALUES (?, 'Videoprojecteur', ?)",
                (self.personne_id, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            ).lastrowid
            conn.execute('UPDATE reservations SET pret_id = ? WHERE id = ?', (pret_id, rid))
            conn.commit()
        finally:
            conn.close()

        resp = self.client.post(f'/reservations/{rid}/supprimer', follow_redirects=True)
        self.assertIn('Impossible de supprimer une réservation liée à un prêt', resp.get_data(as_text=True))
        self.assertIsNotNone(self._reservation(rid))


if __name__ == '__main__':
    unittest.main()
