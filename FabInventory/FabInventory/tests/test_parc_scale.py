"""Tests : parc synthetique de 500 PC et budgets des pages critiques (suite 7).

Le jeu de donnees est genere a la demande par `tools/parc_scale.py` (jamais
commite). Ici, un parc de 500 masters / 2 000 snapshots suffit a prouver que le
cout des pages critiques ne depend ni du nombre de masters ni du nombre de
snapshots : chaque page a un budget SQL fixe et un temps de reponse borne.

Ce module s'execute dans son propre processus via `tests/run_all.py` : `app.py`
fige son chemin de base a l'import.
"""
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-parc-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from app import _normalize_software_name, app, get_db, init_db  # noqa: E402
from tests.support import reset_temp_db  # noqa: E402
from tools.parc_scale import MAX_MS, PAGES, executer, populate  # noqa: E402

MASTERS = 500
SNAPSHOTS_PAR_MASTER = 4


class ParcScaleTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)
        cls.client = app.test_client()
        with cls.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_user_id"] = 1
        with app.app_context():
            reset_temp_db(init_db)
            cls.volumes = populate(
                get_db(),
                _normalize_software_name,
                masters=MASTERS,
                snapshots_per_master=SNAPSHOTS_PAR_MASTER,
            )
        cls.resultats = executer(cls.client)

    def test_volume_genere(self):
        self.assertEqual(self.volumes["masters"], MASTERS)
        self.assertEqual(self.volumes["snapshots"], MASTERS * SNAPSHOTS_PAR_MASTER)
        self.assertEqual(
            self.volumes["software_index"],
            MASTERS * SNAPSHOTS_PAR_MASTER * 40,
        )
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"], MASTERS
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM masters WHERE workflow_type = 'inventory'"
                ).fetchone()["c"],
                MASTERS,
            )

    def test_pages_critiques_repondent(self):
        for resultat in self.resultats:
            with self.subTest(page=resultat["path"]):
                self.assertEqual(resultat["status"], 200)

    def test_budget_sql_fixe(self):
        """Un budget constant prouve qu'aucune requete n'est faite par master."""
        for resultat in self.resultats:
            with self.subTest(page=resultat["path"]):
                budget = PAGES[resultat["path"]]
                self.assertLessEqual(
                    resultat["sql"],
                    budget,
                    f"{resultat['sql']} requetes SQL pour {resultat['path']} "
                    f"(budget {budget})",
                )

    def test_temps_de_reponse_borne(self):
        for resultat in self.resultats:
            with self.subTest(page=resultat["path"]):
                self.assertLessEqual(
                    resultat["ms"],
                    MAX_MS,
                    f"{resultat['ms']} ms pour {resultat['path']} (seuil {MAX_MS} ms)",
                )

if __name__ == "__main__":
    unittest.main()
