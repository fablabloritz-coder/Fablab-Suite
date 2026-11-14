"""Tests : réservations hybrides (catégorie seule, conflits, capacité réelle)."""
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


class ReservationsHybridesTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-reservations-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-reservations')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            cls.personne_id = conn.execute(
                "INSERT INTO personnes (nom, prenom, categorie) VALUES ('Martin', 'Lea', 'enseignant')"
            ).lastrowid
            for numero, categorie in [('PC-1', 'PC portable'), ('PC-2', 'PC portable'),
                                      ('TAB-1', 'Tablette'), ('TAB-2', 'Tablette'),
                                      ('TAB-3', 'Tablette'), ('TAB-4', 'Tablette'),
                                      ('TAB-5', 'Tablette'), ('IMP-1', 'Imprimante')]:
                conn.execute(
                    "INSERT INTO inventaire (numero_inventaire, type_materiel, etat, actif) "
                    "VALUES (?, ?, 'disponible', 1)", (numero, categorie)
                )
            conn.commit()
            cls.materiel = {
                row['numero_inventaire']: row['id']
                for row in conn.execute('SELECT id, numero_inventaire FROM inventaire').fetchall()
            }
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    # ── Helpers ──────────────────────────────────────────────
    def _creneau(self, jours, heure_debut=9, heure_fin=17):
        jour = (datetime.datetime.now() + datetime.timedelta(days=jours)).strftime('%Y-%m-%d')
        return f'{jour}T{heure_debut:02d}:00', f'{jour}T{heure_fin:02d}:00'

    def _reserver(self, debut, fin, **extra):
        data = {'personne_id': str(self.personne_id), 'date_reservation': debut,
                'date_fin_reservation': fin, 'statut': 'demande'}
        data.update(extra)
        return self.client.post('/reservations', data=data, follow_redirects=True)

    def _reservations(self):
        conn = database.get_db()
        try:
            return conn.execute(
                'SELECT * FROM reservations ORDER BY id ASC'
            ).fetchall()
        finally:
            conn.close()

    # ── Catégorie seule ──────────────────────────────────────
    def test_reservation_categorie_seule(self):
        """Une réservation sans objet, avec catégorie + quantité, est acceptée."""
        debut, fin = self._creneau(2)
        resp = self._reserver(debut, fin, **{
            'res_category_name[]': 'PC portable', 'res_category_qty[]': '1',
        })
        html = resp.get_data(as_text=True)
        self.assertIn('Réservation enregistrée', html)

        reservation = self._reservations()[-1]
        self.assertIsNone(reservation['materiel_id'], 'aucun matériel précis pour une demande par catégorie')
        demandes = json.loads(reservation['demande_categories_json'])
        self.assertEqual(demandes, [{'category': 'PC portable', 'quantity': 1}])

    # ── Capacité ─────────────────────────────────────────────
    def test_stock_insuffisant(self):
        """Demander plus que le stock total d'une catégorie est refusé."""
        debut, fin = self._creneau(4)
        avant = len(self._reservations())
        resp = self._reserver(debut, fin, **{
            'res_category_name[]': 'Imprimante', 'res_category_qty[]': '2',
        })
        self.assertIn('stock insuffisant', resp.get_data(as_text=True))
        self.assertEqual(len(self._reservations()), avant)

    def test_capacite_insuffisante_apres_cumul(self):
        """Le stock restant tient compte des réservations qui chevauchent la période."""
        debut, fin = self._creneau(5)
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO reservations (personne_id, date_reservation, date_fin_reservation, "
                "statut, demande_categories_json) VALUES (?, ?, ?, 'confirmee', ?)",
                (self.personne_id,
                 f"{debut.replace('T', ' ')}:00", f"{fin.replace('T', ' ')}:00",
                 json.dumps([{'category': 'Tablette', 'quantity': 4}])),
            )
            conn.commit()
        finally:
            conn.close()

        avant = len(self._reservations())
        refus = self._reserver(debut, fin, **{
            'res_category_name[]': 'Tablette', 'res_category_qty[]': '2',
        })
        self.assertIn('capacité insuffisante', refus.get_data(as_text=True))
        self.assertEqual(len(self._reservations()), avant, 'aucune réservation au-delà de la capacité')

        accepte = self._reserver(debut, fin, **{
            'res_category_name[]': 'Tablette', 'res_category_qty[]': '1',
        })
        self.assertIn('Réservation enregistrée', accepte.get_data(as_text=True))
        self.assertEqual(len(self._reservations()), avant + 1)

    # ── Conflits ─────────────────────────────────────────────
    def test_conflit_objet_deja_reserve(self):
        """Un objet déjà réservé sur un créneau qui chevauche est refusé."""
        debut, fin = self._creneau(6)
        premier = self._reserver(debut, fin, **{
            'res_items_description[]': 'PC portable (PC-1)',
            'res_items_materiel_id[]': str(self.materiel['PC-1']),
        })
        self.assertIn('Réservation enregistrée', premier.get_data(as_text=True))

        avant = len(self._reservations())
        debut_chevauchant, fin_chevauchant = self._creneau(6, 10, 12)
        second = self._reserver(debut_chevauchant, fin_chevauchant, **{
            'res_items_description[]': 'PC portable (PC-1)',
            'res_items_materiel_id[]': str(self.materiel['PC-1']),
        })
        self.assertIn('réservation existe déjà', second.get_data(as_text=True))
        self.assertEqual(len(self._reservations()), avant)

    def test_conflit_avec_reservation_historique(self):
        """Une réservation à l'ancien format (items_json seul) bloque toujours l'objet."""
        debut, fin = self._creneau(7, 9, 17)
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO reservations (personne_id, materiel_id, date_reservation, "
                "date_fin_reservation, statut, items_json) VALUES (?, NULL, ?, ?, 'confirmee', ?)",
                (self.personne_id, f"{debut.replace('T', ' ')}:00", f"{fin.replace('T', ' ')}:00",
                 json.dumps([{'description': 'PC portable (PC-2)',
                              'materiel_id': self.materiel['PC-2']}])),
            )
            conn.commit()
        finally:
            conn.close()

        avant = len(self._reservations())
        resp = self._reserver(debut, fin, **{
            'res_items_description[]': 'PC portable (PC-2)',
            'res_items_materiel_id[]': str(self.materiel['PC-2']),
        })
        self.assertIn('réservation existe déjà', resp.get_data(as_text=True))
        self.assertEqual(len(self._reservations()), avant)


if __name__ == '__main__':
    unittest.main()
