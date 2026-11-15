"""Tests : budget de requêtes SQL des API FabBoard (garde-fou anti N+1).

Le nombre d'instructions SQL exécutées pour servir une requête est compté avec
le trace callback de SQLite. Une API dont le coût dépend du nombre de slides,
de widgets ou de sources signale un N+1 : son budget est donc fixé, avec une
marge, juste au-dessus de la valeur mesurée.

Repères actuels : `/api/slides` exécute 4 requêtes, `/api/layouts` en exécute 3
et `/api/dashboard/data` en exécute 9, quel que soit le nombre de slides.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import models  # noqa: E402

BUDGETS = {
    '/api/slides': 10,
    '/api/layouts': 8,
    '/api/dashboard/data': 16,
}

# Charge utile Fabtrack figée : le tableau de bord ne doit joindre aucun service.
PAYLOAD_FABTRACK = (
    {
        'fabtrack_stats': {'total_interventions': 12, 'total_3d_grammes': 480},
        'activites': [{'id': index, 'projet_nom': f'Projet {index}'} for index in range(20)],
        'machines': [{'id': index, 'nom': f'Machine {index}'} for index in range(5)],
        'missions': [{'id': index, 'titre': f'Mission {index}'} for index in range(5)],
    },
    None,
)


class SqlQueryBudgetTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix='fabboard-sql-')
        cls._orig_db_path = getattr(models, 'DB_PATH', None)
        models.DB_PATH = os.path.join(cls._tmpdir, 'fabboard.db')
        models.init_db()
        models.migrate_db()

        # Neutraliser le worker de synchronisation (threads) avant l'import d'app.
        with mock.patch('sync_worker.start_sync_worker', return_value=None), \
             mock.patch('sync_worker.stop_sync_worker', return_value=None):
            import app as app_module
            app_module.app.config['TESTING'] = True
            app_module._db_initialized = True
            cls.client = app_module.app.test_client()

        cls._ajouter_slides(8)

    @classmethod
    def _ajouter_slides(cls, nombre):
        conn = models.get_db()
        try:
            layout = conn.execute("SELECT id FROM layouts ORDER BY id LIMIT 1").fetchone()['id']
            widgets = [row['id'] for row in conn.execute(
                "SELECT id FROM widgets_disponibles ORDER BY id LIMIT 3")]
            base = conn.execute("SELECT COUNT(*) AS c FROM slides").fetchone()['c']
            for index in range(nombre):
                slide = conn.execute(
                    "INSERT INTO slides (nom, layout_id, ordre, temps_affichage, actif) "
                    "VALUES (?, ?, ?, 30, 1)",
                    (f'Slide {base + index}', layout, base + index),
                ).lastrowid
                for position, widget in enumerate(widgets):
                    conn.execute(
                        "INSERT INTO slide_widgets (slide_id, widget_id, position, config_json) "
                        "VALUES (?, ?, ?, '{}')",
                        (slide, widget, position),
                    )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        if cls._orig_db_path is not None:
            models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _compter_requetes(self, chemin, iterations=1):
        compteur = {'n': 0}
        vrai_connect = sqlite3.connect

        def connect_compte(*args, **kwargs):
            conn = vrai_connect(*args, **kwargs)
            conn.set_trace_callback(lambda _sql: compteur.__setitem__('n', compteur['n'] + 1))
            return conn

        sqlite3.connect = connect_compte
        try:
            with mock.patch('routes.api_sources._extract_fabtrack_payload',
                            return_value=PAYLOAD_FABTRACK):
                self.client.get(chemin)
                compteur['n'] = 0
                reponse = self.client.get(chemin)
        finally:
            sqlite3.connect = vrai_connect

        self.assertEqual(reponse.status_code, 200, chemin)
        return compteur['n']

    def test_slides_respecte_son_budget(self):
        requetes = self._compter_requetes('/api/slides')
        self.assertLessEqual(requetes, BUDGETS['/api/slides'],
                             f"{requetes} requêtes SQL pour /api/slides")

    def test_layouts_respecte_son_budget(self):
        requetes = self._compter_requetes('/api/layouts')
        self.assertLessEqual(requetes, BUDGETS['/api/layouts'],
                             f"{requetes} requêtes SQL pour /api/layouts")

    def test_dashboard_respecte_son_budget(self):
        requetes = self._compter_requetes('/api/dashboard/data')
        self.assertLessEqual(requetes, BUDGETS['/api/dashboard/data'],
                             f"{requetes} requêtes SQL pour /api/dashboard/data")

    def test_cout_independant_du_volume(self):
        """Doubler les slides ne doit pas augmenter le nombre de requêtes."""
        avant = self._compter_requetes('/api/slides')
        self._ajouter_slides(8)
        apres = self._compter_requetes('/api/slides')
        self.assertLessEqual(apres, avant + 2,
                             f"{avant} requêtes puis {apres} après ajout de 8 slides")


if __name__ == '__main__':
    unittest.main()
