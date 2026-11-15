"""Tests des contrôles de création (prêt et réservation).

Couvre ce qui n'était pas testé avant : les conflits détectés à la création
d'un prêt, la lecture des demandes portées par `materiel_id` historique et par
`items_json` multi-matériels, et le coût en requêtes SQL de ces vérifications.
"""
import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ['TESTING'] = '1'

import database  # noqa: E402
import reservations_logic as logic  # noqa: E402


def _ajouter_materiel(conn, type_materiel, numero):
    return conn.execute(
        "INSERT INTO inventaire (type_materiel, marque, modele, numero_inventaire, actif) "
        "VALUES (?, 'Bench', 'X', ?, 1)",
        (type_materiel, numero),
    ).lastrowid


class CreationConflictTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-conflits-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        cls.maintenant = datetime.datetime.now().replace(microsecond=0)
        maintenant = cls.maintenant
        conn = database.get_db()
        try:
            cls.personne = conn.execute(
                "INSERT INTO personnes (nom, prenom, categorie) VALUES ('Test', 'Test', 'eleve')"
            ).lastrowid
            cls.imprimante_1 = _ajouter_materiel(conn, 'Imprimante 3D', 'INV-1')
            cls.imprimante_2 = _ajouter_materiel(conn, 'Imprimante 3D', 'INV-2')
            cls.arduino = _ajouter_materiel(conn, 'Arduino', 'INV-3')

            def _reservation(**champs):
                valeurs = {
                    'personne_id': cls.personne,
                    'materiel_id': None,
                    'items_json': None,
                    'demande_categories_json': None,
                    'date_reservation': None,
                    'date_fin_reservation': None,
                    'statut': 'confirmee',
                }
                valeurs.update(champs)
                for colonne in ('date_reservation', 'date_fin_reservation'):
                    if valeurs[colonne] is not None:
                        valeurs[colonne] = logic.format_db_datetime(valeurs[colonne])
                colonnes = list(valeurs)
                placeholders = ','.join('?' * len(colonnes))
                return conn.execute(
                    f"INSERT INTO reservations ({','.join(colonnes)}) VALUES ({placeholders})",
                    [valeurs[colonne] for colonne in colonnes],
                ).lastrowid

            # Réservation historique : un seul matériel précis, dans la fenêtre de verrouillage.
            cls.reservation_verrou = _reservation(
                materiel_id=cls.imprimante_1,
                date_reservation=maintenant + datetime.timedelta(hours=2),
                date_fin_reservation=maintenant + datetime.timedelta(hours=4),
            )
            # Réservation multi-matériels via items_json, plus loin dans le temps.
            cls.reservation_items = _reservation(
                items_json=json.dumps([
                    {'materiel_id': cls.imprimante_1},
                    {'materiel_id': cls.imprimante_2},
                ]),
                date_reservation=maintenant + datetime.timedelta(days=5),
                date_fin_reservation=maintenant + datetime.timedelta(days=7),
            )
            # Réservation par catégorie qui dépasse le stock disponible.
            cls.reservation_categorie = _reservation(
                demande_categories_json=json.dumps([{'category': 'Arduino', 'quantity': 5}]),
                date_reservation=maintenant + datetime.timedelta(days=6),
                date_fin_reservation=maintenant + datetime.timedelta(days=8),
            )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _connexion(self):
        return database.get_db()

    def test_verrou_exact_sur_materiel_historique(self):
        """Un matériel précis réservé reste bloquant pour un prêt qui le prend."""
        conn = self._connexion()
        try:
            conflits = logic.find_reservation_conflicts_for_loan(
                conn,
                [self.imprimante_1],
                self.maintenant + datetime.timedelta(hours=30),
                now_dt=self.maintenant,
            )
        finally:
            conn.close()
        messages = " ".join(conflit['message'] for conflit in conflits)
        self.assertIn('est réservé précisément et bloqué', messages)
        raisons = {conflit['reason'] for conflit in conflits}
        self.assertIn('exact_lock_window', raisons)

    def test_marge_de_retour_sur_materiel_items_json(self):
        """Une demande via items_json déclenche aussi le conflit de marge."""
        conn = self._connexion()
        try:
            conflits = logic.find_reservation_conflicts_for_loan(
                conn,
                [self.imprimante_2],
                self.maintenant + datetime.timedelta(days=10),
                now_dt=self.maintenant,
            )
        finally:
            conn.close()
        raisons = {conflit['reason'] for conflit in conflits}
        self.assertIn('exact_buffer_window', raisons)

    def test_capacite_depuis_items_json_multiples(self):
        """Deux matériels de même catégorie dans items_json comptent deux unités."""
        conn = self._connexion()
        try:
            messages = logic.find_creation_conflicts_for_category_reservation(
                conn,
                'Imprimante 3D',
                2,
                self.maintenant + datetime.timedelta(days=5, hours=1),
                self.maintenant + datetime.timedelta(days=6),
                now_dt=self.maintenant,
            )
        finally:
            conn.close()
        texte = " ".join(messages)
        self.assertIn('capacité insuffisante', texte)
        self.assertIn('(0/2', texte)

    def test_stock_insuffisant_sur_demande_par_categorie(self):
        conn = self._connexion()
        try:
            messages = logic.find_creation_conflicts_for_category_reservation(
                conn,
                'Arduino',
                5,
                self.maintenant + datetime.timedelta(days=6, hours=1),
                self.maintenant + datetime.timedelta(days=7),
                now_dt=self.maintenant,
            )
        finally:
            conn.close()
        self.assertTrue(any('stock insuffisant' in message for message in messages))

    def test_controles_de_creation_a_cout_borne(self):
        """Les contrôles de création ne doivent pas relancer une requête par ligne."""
        compteur = {'n': 0}
        vrai_connect = sqlite3.connect

        def connect_compte(*args, **kwargs):
            conn = vrai_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
            return conn

        sqlite3.connect = connect_compte
        try:
            conn = database.get_db()
            try:
                compteur['n'] = 0
                logic.find_reservation_conflicts_for_loan(
                    conn,
                    [self.imprimante_1, self.imprimante_2, self.arduino],
                    self.maintenant + datetime.timedelta(days=10),
                    now_dt=self.maintenant,
                )
                requetes_pret = compteur['n']

                compteur['n'] = 0
                logic.find_creation_conflicts_for_category_reservation(
                    conn,
                    'Imprimante 3D',
                    2,
                    self.maintenant + datetime.timedelta(days=5, hours=1),
                    self.maintenant + datetime.timedelta(days=6),
                    now_dt=self.maintenant,
                )
                requetes_categorie = compteur['n']
            finally:
                conn.close()
        finally:
            sqlite3.connect = vrai_connect

        # Mesure de référence : 9 requêtes. L'implémentation précédente en
        # exécutait 18 sur ce même jeu et 78 après ajout de 20 lignes de chaque.
        self.assertLessEqual(requetes_pret, 12, f"{requetes_pret} requêtes pour un contrôle de prêt")
        self.assertLessEqual(requetes_categorie, 12,
                             f"{requetes_categorie} requêtes pour un contrôle de réservation")

    def _cout_formulaire(self, items, demandes):
        """Requêtes SQL d'une soumission complète, contexte initial compris."""
        from routes.reservations import (
            _reservation_conflicts_for_categories,
            _reservation_conflicts_for_items,
        )

        compteur = {'n': 0}
        vrai_connect = sqlite3.connect

        def connect_compte(*args, **kwargs):
            conn = vrai_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
            return conn

        sqlite3.connect = connect_compte
        try:
            conn = database.get_db()
            try:
                debut = self.maintenant + datetime.timedelta(days=5, hours=1)
                fin = debut + datetime.timedelta(days=1)
                compteur['n'] = 0
                contexte = logic.build_creation_conflicts_context(conn, self.maintenant)
                messages = _reservation_conflicts_for_items(
                    conn, items, debut, fin, self.maintenant, contexte=contexte)
                messages += _reservation_conflicts_for_categories(
                    conn, demandes, debut, fin, self.maintenant, contexte=contexte)
                requetes = compteur['n']
            finally:
                conn.close()
        finally:
            sqlite3.connect = vrai_connect

        return requetes, messages

    def test_formulaire_partage_un_seul_contexte(self):
        """Un formulaire ne relit pas le contexte pour chaque matériel et catégorie."""
        items_courts = [{'materiel_id': self.imprimante_1}, {'materiel_id': self.imprimante_2}]
        demandes_courtes = [{'category': 'Imprimante 3D', 'quantity': 2}]
        items_longs = items_courts + [
            {'materiel_id': self.arduino}, {'materiel_id': self.imprimante_1},
            {'materiel_id': self.imprimante_2}, {'materiel_id': self.arduino},
        ]
        demandes_longues = demandes_courtes + [
            {'category': 'Arduino', 'quantity': 1},
            {'category': 'Camera', 'quantity': 1},
        ]

        courtes, messages_courts = self._cout_formulaire(items_courts, demandes_courtes)
        longues, messages_longs = self._cout_formulaire(items_longs, demandes_longues)

        self.assertLessEqual(courtes, 15, f"{courtes} requêtes pour un petit formulaire")
        self.assertLessEqual(longues, courtes + 6,
                             f"{courtes} requêtes puis {longues} en ajoutant 3 matériels et 2 catégories")
        # Le formulaire long examine plus de couples matériel/réservation :
        # il doit remonter au moins autant de messages, sans perdre les courts.
        self.assertGreaterEqual(len(messages_longs), len(messages_courts))

    def test_z_cout_independant_du_volume(self):
        """Doubler les réservations et les prêts ne doit pas doubler les requêtes."""
        conn = self._connexion()
        ajouts = []
        try:
            for i in range(20):
                ajouts.append(conn.execute(
                    "INSERT INTO reservations (personne_id, materiel_id, date_reservation, "
                    "date_fin_reservation, statut, demande_categories_json) VALUES (?,?,?,?,'confirmee',?)",
                    (self.personne, self.imprimante_1,
                     logic.format_db_datetime(self.maintenant + datetime.timedelta(days=9)),
                     logic.format_db_datetime(self.maintenant + datetime.timedelta(days=11)),
                     json.dumps([{'category': 'Imprimante 3D', 'quantity': 1}])),
                ).lastrowid)
                pret_id = conn.execute(
                    "INSERT INTO prets (personne_id, materiel_id, descriptif_objets, date_emprunt, "
                    "duree_pret_jours, retour_confirme, type_duree) VALUES (?,?,?,?,14,0,'defaut')",
                    (self.personne, self.imprimante_2,
                     f'Pret volume {i}', logic.format_db_datetime(self.maintenant)),
                ).lastrowid
                conn.execute(
                    "INSERT INTO pret_materiels (pret_id, materiel_id, description) VALUES (?,?,?)",
                    (pret_id, self.imprimante_2, f'item {i}'),
                )
            conn.commit()
        finally:
            conn.close()

        try:
            compteur = {'n': 0}
            vrai_connect = sqlite3.connect

            def connect_compte(*args, **kwargs):
                conn_local = vrai_connect(*args, **kwargs)
                conn_local.set_trace_callback(
                    lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
                return conn_local

            sqlite3.connect = connect_compte
            try:
                conn = database.get_db()
                try:
                    compteur['n'] = 0
                    logic.find_reservation_conflicts_for_loan(
                        conn,
                        [self.imprimante_1, self.imprimante_2, self.arduino],
                        self.maintenant + datetime.timedelta(days=10),
                        now_dt=self.maintenant,
                    )
                    requetes = compteur['n']
                finally:
                    conn.close()
            finally:
                sqlite3.connect = vrai_connect

            self.assertLessEqual(requetes, 18,
                                 f"{requetes} requêtes après ajout de 20 réservations et 20 prêts")
        finally:
            conn = self._connexion()
            try:
                conn.execute("DELETE FROM pret_materiels WHERE pret_id IN "
                             "(SELECT id FROM prets WHERE descriptif_objets LIKE 'Pret volume %')")
                conn.execute("DELETE FROM prets WHERE descriptif_objets LIKE 'Pret volume %'")
                conn.execute("DELETE FROM reservations WHERE id IN "
                             f"({','.join(str(ajout) for ajout in ajouts)})")
                conn.commit()
            finally:
                conn.close()


if __name__ == '__main__':
    unittest.main()
