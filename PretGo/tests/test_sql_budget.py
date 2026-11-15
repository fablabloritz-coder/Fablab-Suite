"""Tests : budget de requêtes SQL des pages (garde-fou anti N+1).

Le nombre d'instructions SQL exécutées pour afficher une page est compté avec le
trace callback de SQLite. Une page dont le coût dépend du nombre de prêts ou de
réservations signale un N+1 : son budget est donc fixé, avec une marge, juste
au-dessus de la valeur attendue.

Repères sur le jeu de données de ce module (40 prêts, 40 réservations, 40 alertes) :
la version précédente de `get_reservation_risks` exécutait plus de 5 000 requêtes
par page ; l'attendu est d'une quarantaine, constante quel que soit le volume.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import json
import datetime

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ['TESTING'] = '1'

import database  # noqa: E402

# Budgets par page : marge d'environ 50 % au-dessus de la mesure de référence.
BUDGETS = {
    '/': 80,
    '/historique': 60,
}


class SqlQueryBudgetTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='pretgo-sql-budget-')
        cls._orig_db = database.DATABASE_PATH
        cls._orig_data = database.DATA_DIR
        database.DATA_DIR = cls._tmpdir
        database.DATABASE_PATH = os.path.join(cls._tmpdir, 'test.db')
        database.init_db()

        import app as app_module
        app_module.app.config.update(TESTING=True, SECRET_KEY='test-sql-budget')
        cls.client = app_module.app.test_client()

        conn = database.get_db()
        try:
            for i in range(40):
                conn.execute(
                    "INSERT INTO personnes (nom, prenom, classe, categorie, actif) "
                    "VALUES (?, ?, 'T1', 'eleve', 1)", (f'Nom{i}', f'Prenom{i}')
                )
            # Deux imprimantes pour des réservations qui en demandent trois :
            # la situation produit des alertes de capacité.
            for i in range(2):
                conn.execute(
                    "INSERT INTO inventaire (type_materiel, marque, modele, "
                    "numero_inventaire, actif) VALUES ('Imprimante 3D', 'M', 'X', ?, 1)",
                    (f'INV-{i}',)
                )
            maintenant = datetime.datetime.now()
            for i in range(40):
                pret_id = conn.execute(
                    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, "
                    "retour_confirme, type_duree) VALUES (?, ?, ?, 0, 'defaut')",
                    ((i % 40) + 1, f'Pret {i}',
                     (maintenant - datetime.timedelta(days=30 + i)).strftime('%Y-%m-%d %H:%M:%S'))
                ).lastrowid
                conn.execute(
                    "INSERT INTO pret_materiels (pret_id, materiel_id, description) "
                    "VALUES (?, ?, ?)", (pret_id, (i % 2) + 1, f'item {i}')
                )
            for i in range(40):
                conn.execute(
                    "INSERT INTO reservations (personne_id, materiel_id, date_reservation, "
                    "date_fin_reservation, statut, demande_categories_json) "
                    "VALUES (?, ?, ?, ?, 'confirmee', ?)",
                    ((i % 40) + 1, (i % 2) + 1,
                     (maintenant + datetime.timedelta(days=1 + i)).strftime('%Y-%m-%d %H:%M:%S'),
                     (maintenant + datetime.timedelta(days=3 + i)).strftime('%Y-%m-%d %H:%M:%S'),
                     json.dumps({'Imprimante 3D': 3}))
                )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        database.DATA_DIR = cls._orig_data
        database.DATABASE_PATH = cls._orig_db
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _compter_requetes(self, chemin):
        """Nombre d'instructions SQL d'un GET, cache d'alertes volontairement froid."""
        import utils

        compteur = {'n': 0}
        vrai_connect = sqlite3.connect

        def connect_compte(*args, **kwargs):
            conn = vrai_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
            return conn

        sqlite3.connect = connect_compte
        try:
            # Premier appel : schéma, réglages et caches de requête déjà chauds.
            self.client.get(chemin)
            utils._alertes_cache['expire_at'] = 0.0
            compteur['n'] = 0
            reponse = self.client.get(chemin)
        finally:
            sqlite3.connect = vrai_connect

        self.assertEqual(reponse.status_code, 200, chemin)
        return compteur['n']

    def test_alertes_de_capacite_bien_detectees(self):
        """Sans alerte à afficher, le budget de requêtes ne prouverait rien."""
        from reservations_logic import get_reservation_risks

        conn = database.get_db()
        try:
            self.assertGreaterEqual(len(get_reservation_risks(conn)), 10)
        finally:
            conn.close()

    def test_tableau_de_bord_respecte_son_budget(self):
        requetes = self._compter_requetes('/')
        self.assertLessEqual(requetes, BUDGETS['/'], f"{requetes} requêtes SQL pour /")

    def test_historique_respecte_son_budget(self):
        requetes = self._compter_requetes('/historique')
        self.assertLessEqual(requetes, BUDGETS['/historique'],
                             f"{requetes} requêtes SQL pour /historique")

    def test_cout_independant_du_nombre_de_lignes(self):
        """Doubler les prêts ne doit pas doubler le nombre de requêtes."""
        avant = self._compter_requetes('/historique')

        conn = database.get_db()
        try:
            maintenant = datetime.datetime.now()
            for i in range(40):
                conn.execute(
                    "INSERT INTO prets (personne_id, descriptif_objets, date_emprunt, "
                    "retour_confirme, type_duree) VALUES (?, ?, ?, 0, 'defaut')",
                    ((i % 40) + 1, f'Pret bis {i}',
                     (maintenant - datetime.timedelta(days=5 + i)).strftime('%Y-%m-%d %H:%M:%S'))
                )
            conn.commit()
        finally:
            conn.close()

        apres = self._compter_requetes('/historique')
        self.assertLessEqual(apres, avant + 5, f"{avant} requêtes puis {apres} après ajout de 40 prêts")


if __name__ == '__main__':
    unittest.main()
