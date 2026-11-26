"""Tests du Lot 4 : integrite referentielle, seeds et auto-reparation."""
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot4-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import _ensure_software_index_ready, app, get_db, init_db  # noqa: E402


class IntegritySeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def test_cles_etrangeres_actives_et_cascade(self):
        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

            logiciel_id = db.execute("INSERT INTO logiciels (nom) VALUES ('CascadeApp')").lastrowid
            prof_id = db.execute(
                "INSERT INTO prof_tokens (email, nom, token) VALUES ('c@example.test', 'C', 'tok-c')"
            ).lastrowid
            version_id = db.execute(
                "INSERT INTO logiciel_versions (logiciel_id, numero_version) VALUES (?, '1.0')",
                (logiciel_id,),
            ).lastrowid
            plugin_id = db.execute(
                "INSERT INTO logiciel_plugins (logiciel_id, nom) VALUES (?, 'Plugin')",
                (logiciel_id,),
            ).lastrowid
            demande_id = db.execute(
                "INSERT INTO demandes (prof_token_id, logiciel_id, version_id) VALUES (?, ?, ?)",
                (prof_id, logiciel_id, version_id),
            ).lastrowid
            db.execute(
                "INSERT INTO demande_plugins (demande_id, plugin_id) VALUES (?, ?)",
                (demande_id, plugin_id),
            )
            db.execute(
                "INSERT INTO logiciel_guides (logiciel_id, guide_markdown) VALUES (?, '# Guide')",
                (logiciel_id,),
            )
            db.execute("DELETE FROM logiciels WHERE id = ?", (logiciel_id,))
            db.commit()

            for table in ("demandes", "logiciel_versions", "logiciel_plugins", "logiciel_guides", "demande_plugins"):
                count = db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                self.assertEqual(count, 0, table)

    def test_seed_initial_n_est_pas_rejoue(self):
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM admin_users WHERE username = 'admin'").fetchone()["c"],
                1,
            )
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM logiciels").fetchone()["c"], 3)
            db.execute("DELETE FROM admin_users WHERE username = 'admin'")
            db.execute("DELETE FROM logiciels")
            db.commit()

        init_db()

        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM admin_users").fetchone()["c"], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM logiciels").fetchone()["c"], 0)

    def test_healthz_repond(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_index_partiel_est_repare(self):
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES ('PC-INDEX', 'pc-index', 'inventory')"
            ).lastrowid
            software = [{"n": "AutoCAD Update", "v": "2026", "e": "Autodesk", "src": "Registre"}]
            db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '28/11/2026 10:00', ?, 1)",
                (master_id, json.dumps(software)),
            )
            db.commit()

            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM software_index").fetchone()["c"], 0
            )
            _ensure_software_index_ready(db)
            rows = db.execute(
                "SELECT software_name, software_category FROM software_index"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["software_name"], "AutoCAD Update")
            self.assertEqual(rows[0]["software_category"], "update")


if __name__ == "__main__":
    unittest.main()
