"""Tests du Lot 3 : une seule regle de categorie et une couverture au dernier scan."""
import json
import os
import re
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot3-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    _software_masters_count,
    _upsert_snapshot_software_index,
    app,
    get_db,
    init_db,
)


class CategoryConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
            db = get_db()
            self.master_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES ('PC-CAT', 'pc-cat', 'inventory')"
            ).lastrowid

            old_software = [{"n": "VLC", "v": "3", "e": "VideoLAN", "src": "Registre", "cat": "main"}]
            old_id = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '01/01/2026 10:00', ?, 1)",
                (self.master_id, json.dumps(old_software)),
            ).lastrowid
            _upsert_snapshot_software_index(db, old_id, self.master_id, old_software)

            # Pas de champ cat : la regle de nom doit classer en "update".
            new_software = [
                {
                    "n": "Windows Cumulative Update KB5030211",
                    "v": "1",
                    "e": "Microsoft",
                    "src": "Registre",
                }
            ]
            new_id = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '28/11/2026 10:00', ?, 1)",
                (self.master_id, json.dumps(new_software)),
            ).lastrowid
            _upsert_snapshot_software_index(db, new_id, self.master_id, new_software)
            db.commit()

    def test_master_affiche_la_categorie_de_l_index(self):
        html = self.client.get(f"/master/{self.master_id}").get_data(as_text=True)
        self.assertRegex(html, r'"cat"\s*:\s*"update"')

    def test_category_stats_ne_compte_que_le_dernier_scan(self):
        data = self.client.get("/api/category-stats").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["categories"].get("update"), 1)
        self.assertFalse(data["categories"].get("main"))

    def test_couverture_ignore_les_logiciels_absents_du_dernier_scan(self):
        with app.app_context():
            db = get_db()
            self.assertEqual(_software_masters_count(db, "VLC"), 0)
            self.assertEqual(_software_masters_count(db, "KB5030211"), 1)


if __name__ == "__main__":
    unittest.main()
