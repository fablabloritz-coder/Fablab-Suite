"""Tests : budget de requêtes SQL de FabInventory (garde-fou anti N+1).

Le nombre d'instructions SQL exécutées pour servir une page est compté avec le
trace callback de SQLite. Une page dont le coût dépend du nombre de masters ou
de snapshots signale un N+1 : son budget est donc fixé, avec une marge, juste
au-dessus de la valeur mesurée.

Repères actuels : l'accueil et `/api/software` exécutent 3 requêtes, quel que
soit le nombre de masters.

Ce module s'exécute dans son propre processus via `tests/run_all.py` : `app.py`
fige son chemin de base à l'import, il ne peut donc pas partager sa base avec
un autre module.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

_TMPDIR = tempfile.mkdtemp(prefix='fabinventory-sql-')
os.environ['DB_PATH'] = os.path.join(_TMPDIR, 'test.db')
os.environ['UPLOAD_FOLDER'] = os.path.join(_TMPDIR, 'uploads')
os.environ['SECRET_KEY'] = 'tests'

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from app import app, get_db, init_db  # noqa: E402

BUDGETS = {
    '/': 8,
    '/api/software': 8,
}


class SqlQueryBudgetTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        with app.app_context():
            init_db()
            cls._ajouter_masters(12)
        cls.client = app.test_client()

    @classmethod
    def _ajouter_masters(cls, nombre):
        db = get_db()
        base = db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()['c']
        for index in range(nombre):
            master_id = db.execute(
                "INSERT INTO masters (pc_name, workflow_type) VALUES (?, 'inventory')",
                (f'PC-{base + index:03d}',),
            ).lastrowid
            for numero in range(3):
                db.execute(
                    "INSERT INTO snapshots (master_id, scan_date, total_software) "
                    "VALUES (?, datetime('now'), ?)",
                    (master_id, 40 + numero),
                )
        db.commit()

    def _compter_requetes(self, chemin):
        compteur = {'n': 0}
        vrai_connect = sqlite3.connect

        def connect_compte(*args, **kwargs):
            conn = vrai_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
            return conn

        sqlite3.connect = connect_compte
        try:
            self.client.get(chemin)
            compteur['n'] = 0
            reponse = self.client.get(chemin)
        finally:
            sqlite3.connect = vrai_connect

        self.assertEqual(reponse.status_code, 200, chemin)
        return compteur['n']

    def test_accueil_respecte_son_budget(self):
        requetes = self._compter_requetes('/')
        self.assertLessEqual(requetes, BUDGETS['/'], f"{requetes} requêtes SQL pour /")

    def test_api_software_respecte_son_budget(self):
        requetes = self._compter_requetes('/api/software')
        self.assertLessEqual(requetes, BUDGETS['/api/software'],
                             f"{requetes} requêtes SQL pour /api/software")

    def test_cout_independant_du_volume(self):
        """Doubler les masters ne doit pas augmenter le nombre de requêtes."""
        avant = self._compter_requetes('/')
        with app.app_context():
            self._ajouter_masters(12)
        apres = self._compter_requetes('/')
        self.assertLessEqual(apres, avant + 2,
                             f"{avant} requêtes puis {apres} après ajout de 12 masters")


if __name__ == '__main__':
    unittest.main()
