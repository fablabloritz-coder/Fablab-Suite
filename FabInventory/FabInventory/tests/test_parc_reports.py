"""Tests du Lot 6 : rapports parc (versions, licences, logiciels importants).

Les rapports lisent le dernier scan des masters inventorie seulement : un scan
ancien ou un master de preparation ne doit jamais apparaitre dans les comptes.
"""
import io
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot6-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    _parc_important_rows,
    _parc_licence_rows,
    _parc_version_rows,
    app,
    get_db,
    init_db,
)


def _inventory_html(pc_name, scan_date, software):
    payload = {
        "pcName": pc_name,
        "date": scan_date,
        "software": [
            {
                "n": name,
                "v": version,
                "e": "",
                "d": scan_date,
                "s": 1,
                "src": "Registre",
            }
            for name, version in software
        ],
    }
    return (
        '<html><body><span class="label">OS</span><span>Windows 11</span>'
        '<script id="inventoryData" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></body></html>"
    )


class ParcReportsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)
        self._seed()

    # ── jeu de donnees ───────────────────────────────────────────────────────

    def _upload(self, pc_name, scan_date, software):
        html = _inventory_html(pc_name, scan_date, software)
        response = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(html.encode("utf-8")), "scan.html")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)

    def _seed(self):
        # M1 a deux scans : seul le dernier compte (Firefox 129).
        self._upload("M1", "28/11/2026 10:00", [("Firefox", "128")])
        self._upload("M1", "29/11/2026 10:00", [("Firefox", "129")])
        self._upload(
            "M2",
            "28/11/2026 10:00",
            [("Firefox", "129"), ("7-Zip", "24.0"), ("Java", "17")],
        )
        self._upload(
            "M3", "28/11/2026 10:00", [("Firefox", "128"), ("7-Zip", "24.0")]
        )

        with app.app_context():
            db = get_db()
            self.ids = {
                row["pc_name"]: row["id"]
                for row in db.execute("SELECT id, pc_name FROM masters").fetchall()
            }
            flags = [
                ("M1", "Firefox", 1, 1, "Site", "Licence site"),
                ("M2", "7-Zip", 1, 1, "", ""),
                ("M3", "Firefox", 0, 1, "", ""),
            ]
            for pc_name, software, important, licensed, license_type, note in flags:
                db.execute(
                    "INSERT OR REPLACE INTO software_flags "
                    "(master_id, software_name, is_important, note, is_licensed, license_type) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        self.ids[pc_name],
                        software,
                        important,
                        note,
                        licensed,
                        license_type,
                    ),
                )
            prep_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES ('PREP-01', 'prep-01', 'preparation')"
            ).lastrowid
            db.execute(
                "INSERT INTO software_flags "
                "(master_id, software_name, is_important, is_licensed) "
                "VALUES (?, 'Firefox', 1, 1)",
                (prep_id,),
            )
            db.commit()

    def _csv(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.mimetype)
        return response, response.data.decode("utf-8-sig").strip().splitlines()

    # ── versions ─────────────────────────────────────────────────────────────

    def test_versions_du_dernier_scan_par_logiciel(self):
        with app.app_context():
            rows = _parc_version_rows(get_db())

        counts = {(row["software_name"], row["version"]): row["masters"] for row in rows}
        self.assertEqual(counts[("Firefox", "129")], 2)
        self.assertEqual(counts[("Firefox", "128")], 1)
        self.assertEqual(counts[("7-Zip", "24.0")], 2)
        self.assertEqual(counts[("Java", "17")], 1)
        # Le premier scan de M1 (Firefox 128) est remplace par son dernier scan.
        self.assertEqual(len([r for r in rows if r["software_name"] == "Firefox"]), 2)

        latest = {
            (row["software_name"], row["version"]): row["is_latest"] for row in rows
        }
        self.assertTrue(latest[("Firefox", "129")])
        self.assertFalse(latest[("Firefox", "128")])
        self.assertTrue(latest[("7-Zip", "24.0")])

        body = self.client.get("/rapports").get_data(as_text=True)
        self.assertIn("Rapports parc", body)
        self.assertIn("Firefox", body)
        self.assertIn("Ancienne", body)
        self.assertIn("Derniere observee", body)

    # ── licences ─────────────────────────────────────────────────────────────

    def test_licences_sans_information(self):
        with app.app_context():
            rows = _parc_licence_rows(get_db())

        by_name = {row["software_name"]: row for row in rows}
        self.assertEqual(set(by_name), {"Firefox", "7-Zip"})
        self.assertEqual(by_name["Firefox"]["masters"], ["M1", "M3"])
        self.assertEqual(by_name["Firefox"]["missing"], 1)
        self.assertTrue(by_name["Firefox"]["without_info"])
        self.assertEqual(by_name["Firefox"]["license_label"], "Site")
        self.assertEqual(by_name["7-Zip"]["license_label"], "—")
        self.assertTrue(by_name["7-Zip"]["without_info"])
        # Le master de preparation est exclu.
        self.assertNotIn("PREP-01", by_name["Firefox"]["masters"])

        body = self.client.get("/rapports").get_data(as_text=True)
        self.assertIn("Sans information", body)
        self.assertIn("7-Zip", body)

    def test_export_csv_licences(self):
        response, lines = self._csv("/rapports/export/licences.csv")
        self.assertIn("FabInventory-Licences.csv", response.headers["Content-Disposition"])
        self.assertEqual(lines[0].split(";"), [
            "master_id",
            "master",
            "logiciel",
            "type_licence",
            "information_manquante",
            "note",
        ])
        self.assertEqual(len(lines), 4)

        by_master = {}
        for line in lines[1:]:
            cells = line.split(";")
            by_master[(cells[1], cells[2])] = cells
        self.assertEqual(by_master[("M1", "Firefox")][3], "Site")
        self.assertEqual(by_master[("M1", "Firefox")][4], "non")
        self.assertEqual(by_master[("M2", "7-Zip")][3], "")
        self.assertEqual(by_master[("M2", "7-Zip")][4], "oui")
        self.assertEqual(by_master[("M3", "Firefox")][4], "oui")

    # ── logiciels importants ─────────────────────────────────────────────────

    def test_logiciels_importants_et_export_csv(self):
        with app.app_context():
            rows = _parc_important_rows(get_db())

        by_name = {row["software_name"]: row for row in rows}
        self.assertEqual(set(by_name), {"Firefox", "7-Zip"})
        self.assertEqual(len(by_name["Firefox"]["masters"]), 1)
        self.assertEqual(by_name["Firefox"]["masters"][0]["pc_name"], "M1")
        self.assertEqual(by_name["Firefox"]["masters"][0]["version"], "129")
        self.assertEqual(by_name["Firefox"]["notes"], ["Licence site"])
        self.assertEqual(by_name["7-Zip"]["versions"], ["24.0"])
        self.assertTrue(by_name["7-Zip"]["licensed"])

        body = self.client.get("/rapports").get_data(as_text=True)
        self.assertIn("Logiciels importants", body)
        self.assertIn("Licence site", body)

        response, lines = self._csv("/rapports/export/importants.csv")
        self.assertIn(
            "FabInventory-Logiciels-Importants.csv",
            response.headers["Content-Disposition"],
        )
        self.assertEqual(
            lines[0].split(";"),
            [
                "master_id",
                "master",
                "logiciel",
                "version",
                "categorie",
                "note",
                "sous_licence",
                "type_licence",
            ],
        )
        self.assertEqual(len(lines), 3)
        exported = "\n".join(lines)
        self.assertIn("Firefox", exported)
        self.assertIn("129", exported)
        self.assertIn("Licence site", exported)
        self.assertNotIn("PREP-01", exported)

    # ── non-regression ───────────────────────────────────────────────────────

    def test_rapports_ne_cassent_pas_les_pages_existantes(self):
        for path in ("/", "/sante", "/rapports"):
            self.assertEqual(self.client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()
