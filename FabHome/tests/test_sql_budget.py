"""Tests : budget de requêtes SQL des pages FabHome (garde-fou anti N+1).

Le nombre d'instructions SQL exécutées pour servir une page est compté avec le
trace callback de SQLite. Une page dont le coût dépend du nombre de groupes, de
liens, de profils ou d'applications signale un N+1 : son budget est donc fixé,
avec une marge, juste au-dessus de la valeur mesurée.

Repères actuels : l'accueil exécute 37 requêtes, `/api/suite/apps` en exécute 4,
et ces valeurs ne bougent pas quand on ajoute des groupes, des liens, des
profils ou des services.
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

# La base doit être redirigée avant l'import de models puis de app.
_TMPDIR = tempfile.mkdtemp(prefix='fabhome-sql-')
os.environ['FABHOME_DB'] = os.path.join(_TMPDIR, 'fabhome_test.db')
os.environ['FABHOME_DATA'] = _TMPDIR

import models  # noqa: E402
import app as app_module  # noqa: E402

BUDGETS = {
    '/': 50,
    '/api/suite/apps': 8,
}


class SqlQueryBudgetTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        models.DB_PATH = os.environ['FABHOME_DB']
        models.init_db()

        app_module.app.config['TESTING'] = True
        cls.client = app_module.app.test_client()

        # Contenu réaliste : un profil, deux pages, groupes, liens, widgets,
        # applications de la suite.
        page = models.create_page('Atelier')
        for groupe in range(4):
            groupe_id = models.create_group(f'Groupe {groupe}', page_id=page)
            for lien in range(5):
                models.create_link(groupe_id, f'Lien {groupe}-{lien}', 'https://example.com')
            models.create_grid_widget(page, 'horloge', {})
        for index in range(6):
            models.create_suite_app(
                f'http://app{index}.local:5000',
                {'name': f'Application {index}', 'version': '1.0', 'app_id': f'app{index}'},
            )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMPDIR, ignore_errors=True)

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

    def test_liste_des_applications_respecte_son_budget(self):
        requetes = self._compter_requetes('/api/suite/apps')
        self.assertLessEqual(requetes, BUDGETS['/api/suite/apps'],
                             f"{requetes} requêtes SQL pour /api/suite/apps")

    def test_cout_independant_du_volume(self):
        """Ajouter des groupes, liens et applications ne doit rien coûter de plus."""
        avant = self._compter_requetes('/')

        page = models.get_pages()[0]['id']
        for groupe in range(10):
            groupe_id = models.create_group(f'Volume {groupe}', page_id=page)
            for lien in range(5):
                models.create_link(groupe_id, f'Volume {groupe}-{lien}', 'https://example.com')
        for index in range(10):
            models.create_suite_app(
                f'http://volume{index}.local:5000',
                {'name': f'Volume {index}', 'version': '1.0'},
            )

        apres = self._compter_requetes('/')
        self.assertLessEqual(apres, avant + 2,
                             f"{avant} requêtes puis {apres} après ajout de 10 groupes et 10 applications")


if __name__ == '__main__':
    unittest.main()
