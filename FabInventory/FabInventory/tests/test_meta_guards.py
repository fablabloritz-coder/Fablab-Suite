"""Garde-fous transverses : schema, vues, templates et decouverte des tests."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-meta-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests import run_all  # noqa: E402
from tests.support import reset_temp_db, simuler_base_ancienne  # noqa: E402
from app import APP_VERSION, app, get_db, init_db  # noqa: E402


class MetaGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def test_tous_les_templates_compilent(self):
        for name in app.jinja_env.list_templates():
            with self.subTest(template=name):
                app.jinja_env.get_template(name)

    def test_run_all_decouvre_tous_les_modules(self):
        attendus = {chemin.stem for chemin in Path(run_all.TESTS_DIR).glob("test_*.py")}
        self.assertEqual(set(run_all.modules()), attendus)

    def test_schema_et_garde_fous_presents(self):
        with app.app_context():
            db = get_db()
            snapshot_cols = {row[1] for row in db.execute("PRAGMA table_info(snapshots)").fetchall()}
            master_cols = {row[1] for row in db.execute("PRAGMA table_info(masters)").fetchall()}
            self.assertIn("scan_date_iso", snapshot_cols)
            self.assertIn("content_hash", snapshot_cols)
            self.assertIn("services_json", snapshot_cols)
            self.assertIn("warnings_json", snapshot_cols)
            self.assertIn("script_version", snapshot_cols)
            self.assertIn("scan_schema", snapshot_cols)
            self.assertIn("generated_at", snapshot_cols)
            self.assertIn("pc_name_key", master_cols)

            views = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'view'"
                ).fetchall()
            }
            self.assertIn("latest_snapshots", views)
            self.assertIn("snapshot_keys", views)
            tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("software_followups", tables)
            self.assertIn(
                "ux_software_followups_active",
                {
                    row["name"]
                    for row in db.execute("PRAGMA index_list(software_followups)").fetchall()
                },
            )

            indexes = {
                row["name"]
                for row in db.execute("PRAGMA index_list(masters)").fetchall()
            }
            self.assertIn("idx_masters_workflow_key", indexes)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

            seed = db.execute(
                "SELECT value FROM app_meta WHERE key = 'initial_seed'"
            ).fetchone()
            self.assertEqual(seed["value"], "1")

    def test_migration_ajoute_le_suivi_des_absences(self):
        """Une base existante sans cette table doit etre migree par init_db()."""
        with app.app_context():
            db = get_db()
            db.execute("DROP TABLE IF EXISTS software_followups")
            db.commit()
            simuler_base_ancienne(db)
            init_db()
            tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("software_followups", tables)
            self.assertIn(
                "ux_software_followups_active",
                {
                    row["name"]
                    for row in db.execute("PRAGMA index_list(software_followups)").fetchall()
                },
            )

    def test_manifeste_expose_la_version(self):
        response = self.client.get("/api/fabsuite/manifest")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("version"), APP_VERSION)
        self.assertEqual(data.get("app"), "fabinventory")

    def test_nom_de_fichier_export_sans_caractere_d_en_tete(self):
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES ('PC;X\"y', 'pc;x\"y', 'inventory')"
            ).lastrowid
            db.commit()

        response = self.client.get(f"/master/{master_id}/export")
        self.assertEqual(response.status_code, 200)
        disposition = response.headers["Content-Disposition"]
        self.assertIn("FabInventory-PC-X-y.csv", disposition)
        self.assertNotIn("PC;X", disposition)


if __name__ == "__main__":
    unittest.main()
