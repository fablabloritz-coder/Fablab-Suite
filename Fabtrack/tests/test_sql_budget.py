"""Tests : budget de requêtes SQL des API de liste (garde-fou anti N+1).

Le nombre d'instructions SQL exécutées pour servir une requête est compté avec
le trace callback de SQLite. Une API dont le coût dépend du nombre de lignes
signale un N+1 : son budget est donc fixé, avec une marge, juste au-dessus de la
valeur mesurée.

Repère actuel : `/api/consommations` exécute 5 requêtes sur le jeu de
démonstration, quel que soit le volume d'historique.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import app as app_module  # noqa: E402
import models  # noqa: E402

# Budgets : marge d'environ 100 % au-dessus de la mesure de référence.
BUDGETS = {
    '/api/consommations': 10,
}


class SqlQueryBudgetTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix='fabtrack-sql-')

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabtrack_test.db')
        models.init_db()

        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

        # Jeu de démonstration : machines, référents, consommations, missions.
        models.generate_demo_data()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _compter_requetes(self, chemin):
        compteur = {'n': 0}
        vrai_connect = sqlite3.connect

        def connect_compte(*args, **kwargs):
            conn = vrai_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
            return conn

        sqlite3.connect = connect_compte
        try:
            # Premier appel : schéma, réglages et caches déjà chauds.
            self.client.get(chemin)
            compteur['n'] = 0
            reponse = self.client.get(chemin)
        finally:
            sqlite3.connect = vrai_connect

        self.assertEqual(reponse.status_code, 200, chemin)
        return compteur['n']

    def test_consommations_respecte_son_budget(self):
        requetes = self._compter_requetes('/api/consommations')
        budget = BUDGETS['/api/consommations']
        self.assertLessEqual(requetes, budget, f"{requetes} requêtes SQL pour /api/consommations")

    def test_cout_independant_du_volume(self):
        """Doubler l'historique ne doit pas augmenter le nombre de requêtes."""
        avant = self._compter_requetes('/api/consommations')
        models.generate_demo_data()
        apres = self._compter_requetes('/api/consommations')
        self.assertLessEqual(apres, avant + 2,
                             f"{avant} requêtes puis {apres} après un second jeu de démonstration")


if __name__ == '__main__':
    unittest.main()
