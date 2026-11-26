"""Tests du Lot 2 : une seule fiche par master, quel que soit l'orthographe."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot2-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402


def _inventory_html(pc_name, scan_date, software_name="Firefox"):
    payload = {
        "pcName": pc_name,
        "date": scan_date,
        "software": [
            {
                "n": software_name,
                "v": "128",
                "e": "Mozilla",
                "d": scan_date,
                "s": 1,
                "src": "Registre",
            }
        ],
    }
    return (
        '<html><body><span class="label">OS</span><span>Windows 11</span>'
        '<script id="inventoryData" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></body></html>"
    )


class IdentityImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def _upload(self, html, filename="inventaire.html"):
        return self.client.post(
            "/upload",
            data={"file": (io.BytesIO(html.encode("utf-8")), filename)},
            content_type="multipart/form-data",
        )

    def test_fusionne_les_doublons_existants(self):
        with app.app_context():
            db = get_db()
            ids = []
            for index, name in enumerate(("PC-02", "PC-02 ", "pc-02")):
                master_id = db.execute(
                    "INSERT INTO masters (pc_name, workflow_type) VALUES (?, 'inventory')",
                    (name,),
                ).lastrowid
                ids.append(master_id)
                db.execute(
                    "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                    "VALUES (?, ?, '[]', 0)",
                    (master_id, f"0{index + 1}/01/2026 10:00"),
                )
                db.execute(
                    "INSERT INTO software_flags (master_id, software_name) VALUES (?, ?)",
                    (master_id, f"App{index}"),
                )
            db.execute(
                "INSERT INTO roadmap_items (master_id, software_name) VALUES (?, 'VLC')",
                (ids[0],),
            )
            db.commit()

        init_db()

        with app.app_context():
            db = get_db()
            masters = db.execute("SELECT id, pc_name, pc_name_key FROM masters").fetchall()
            self.assertEqual(len(masters), 1)
            self.assertEqual(masters[0]["pc_name"], "PC-02")
            self.assertEqual(masters[0]["pc_name_key"], "pc-02")
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 3
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM software_flags").fetchone()["c"], 3
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM roadmap_items").fetchone()["c"], 1
            )

            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                    "VALUES ('PC-02 bis', 'pc-02', 'inventory')"
                )

    def test_upload_normalise_et_ignore_le_doublon_identique(self):
        html = _inventory_html("PC-02", "28/11/2026 10:00")

        self.assertEqual(self._upload(html).status_code, 302)
        self.assertEqual(self._upload(html).status_code, 302)
        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 1)

        # Meme PC avec une casse/espace differents mais un scan different : meme fiche.
        html2 = _inventory_html(" pc-02 ", "29/11/2026 10:00", software_name="7-Zip")
        self.assertEqual(self._upload(html2).status_code, 302)
        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 2)

    def test_extension_html_majuscules_acceptee(self):
        html = _inventory_html("PC-MAJ", "28/11/2026 10:00")
        self.assertEqual(self._upload(html, filename="INVENTAIRE.HTML").status_code, 302)
        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 1)

    def test_renommage_met_a_jour_la_cle_et_refuse_les_collisions(self):
        self._upload(_inventory_html("PC-02", "28/11/2026 10:00"))
        self._upload(_inventory_html("PC-03", "28/11/2026 10:00"))
        with app.app_context():
            db = get_db()
            ids = {
                row["pc_name"]: row["id"]
                for row in db.execute("SELECT id, pc_name FROM masters ORDER BY id").fetchall()
            }

        response = self.client.post(
            f"/master/{ids['PC-02']}/edit",
            data={"pc_name": " PC-02 ", "label": "", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute("SELECT pc_name_key FROM masters WHERE id = ?", (ids["PC-02"],)).fetchone()["pc_name_key"],
                "pc-02",
            )

        self.client.post(
            f"/master/{ids['PC-03']}/edit",
            data={"pc_name": "pc-02", "label": "", "notes": ""},
        )
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute("SELECT pc_name FROM masters WHERE id = ?", (ids["PC-03"],)).fetchone()["pc_name"],
                "PC-03",
            )

    def test_master_new_refuse_les_variantes_de_casse(self):
        first = self.client.post("/master/new", data={"pc_name": "PREP-01"})
        second = self.client.post("/master/new", data={"pc_name": "prep-01 "})
        self.assertEqual(first.status_code, 302)
        # Le doublon est refuse et le formulaire est reaffiche (200), sans creation.
        self.assertEqual(second.status_code, 200)
        with app.app_context():
            db = get_db()
            masters = db.execute(
                "SELECT pc_name FROM masters WHERE workflow_type = 'preparation'"
            ).fetchall()
            self.assertEqual(len(masters), 1)
            self.assertEqual(masters[0]["pc_name"], "PREP-01")


if __name__ == "__main__":
    unittest.main()
