import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class RoadmapFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmp_dir.name)
        os.environ["DB_PATH"] = str(tmp_path / "fabinventory-roadmap-test.db")
        os.environ["UPLOAD_FOLDER"] = str(tmp_path / "uploads")

        import app as fabinventory_app  # pylint: disable=import-outside-toplevel

        fabinventory_app.DB_PATH = os.environ["DB_PATH"]
        fabinventory_app.UPLOAD_FOLDER = os.environ["UPLOAD_FOLDER"]
        os.makedirs(fabinventory_app.UPLOAD_FOLDER, exist_ok=True)
        fabinventory_app.init_db()

        cls.app = fabinventory_app.app
        cls.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp_dir.cleanup()

    def setUp(self):
        self.client = self.app.test_client()
        self._reset_db()

    def _reset_db(self):
        db = sqlite3.connect(os.environ["DB_PATH"])
        db.execute("DELETE FROM roadmap_items")
        db.execute("DELETE FROM roadmap_minimal_pack")
        db.execute("DELETE FROM software_index")
        db.execute("DELETE FROM software_flags")
        db.execute("DELETE FROM snapshots")
        db.execute("DELETE FROM masters")
        db.commit()
        db.close()

    def test_create_master_preloads_minimal_pack_and_custom_items(self):
        db = sqlite3.connect(os.environ["DB_PATH"])
        db.execute(
            "INSERT INTO roadmap_minimal_pack (software_name, note) VALUES (?, ?)",
            ("VLC", "https://www.videolan.org"),
        )
        db.commit()
        db.close()

        response = self.client.post(
            "/master/new",
            data={
                "pc_name": "MASTER-01",
                "label": "Salle A",
                "notes": "Master test",
                "use_minimal_pack": "1",
                "roadmap_software[]": ["7zip"],
                "roadmap_note[]": ["winget install 7zip.7zip"],
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Checklist logiciels", response.get_data(as_text=True))

        db = sqlite3.connect(os.environ["DB_PATH"])
        db.row_factory = sqlite3.Row
        master = db.execute("SELECT id FROM masters WHERE pc_name = ?", ("MASTER-01",)).fetchone()
        self.assertIsNotNone(master)

        rows = db.execute(
            "SELECT software_name, source FROM roadmap_items WHERE master_id = ? ORDER BY software_name",
            (master["id"],),
        ).fetchall()
        db.close()

        names = [row["software_name"] for row in rows]
        sources = {row["software_name"]: row["source"] for row in rows}

        self.assertIn("VLC", names)
        self.assertIn("7zip", names)
        self.assertEqual(sources["VLC"], "minimal")
        self.assertEqual(sources["7zip"], "custom")

    def test_apply_minimal_pack_to_existing_master(self):
        db = sqlite3.connect(os.environ["DB_PATH"])
        cur = db.cursor()
        cur.execute("INSERT INTO masters (pc_name, label, notes) VALUES (?, ?, ?)", ("MASTER-02", "Salle B", ""))
        master_id = cur.lastrowid

        cur.execute(
            "INSERT INTO roadmap_minimal_pack (software_name, note) VALUES (?, ?)",
            ("SumatraPDF", "https://www.sumatrapdfreader.org"),
        )
        cur.execute(
            "INSERT INTO roadmap_minimal_pack (software_name, note) VALUES (?, ?)",
            ("VLC", "https://www.videolan.org"),
        )
        db.commit()
        db.close()

        response = self.client.post(f"/master/{master_id}/roadmap/apply-minimal-pack", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

        db = sqlite3.connect(os.environ["DB_PATH"])
        db.row_factory = sqlite3.Row
        items = db.execute(
            "SELECT software_name, source FROM roadmap_items WHERE master_id = ? ORDER BY software_name",
            (master_id,),
        ).fetchall()
        db.close()

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["software_name"], "SumatraPDF")
        self.assertEqual(items[1]["software_name"], "VLC")
        self.assertTrue(all(row["source"] == "minimal" for row in items))

    def test_roadmap_print_page_renders_html_for_printing(self):
        db = sqlite3.connect(os.environ["DB_PATH"])
        cur = db.cursor()
        cur.execute("INSERT INTO masters (pc_name, label, notes) VALUES (?, ?, ?)", ("MASTER-PRINT", "Salle C", ""))
        master_id = cur.lastrowid
        cur.execute(
            "INSERT INTO roadmap_items (master_id, software_name, note, is_done, source) VALUES (?, ?, ?, ?, ?)",
            (master_id, "VLC", "https://www.videolan.org", 0, "minimal"),
        )
        db.commit()
        db.close()

        response = self.client.get(f"/master/{master_id}/roadmap/print")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Checklist d'installation", html)
        self.assertIn("MASTER-PRINT", html)
        self.assertIn("VLC", html)

    def test_minimal_pack_page_available(self):
        response = self.client.get("/roadmap/minimal-pack")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Pack minimal commun", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
