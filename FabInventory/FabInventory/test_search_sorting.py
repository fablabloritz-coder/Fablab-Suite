import os
import sqlite3
import tempfile
import unittest
import csv
import io
from pathlib import Path


class SearchSortingRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmp_dir.name)
        os.environ["DB_PATH"] = str(tmp_path / "fabinventory-test.db")
        os.environ["UPLOAD_FOLDER"] = str(tmp_path / "uploads")

        # Import after env setup so app.py initializes with test DB path.
        import app as fabinventory_app  # pylint: disable=import-outside-toplevel

        cls._module = fabinventory_app
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
        db.execute("DELETE FROM software_index")
        db.execute("DELETE FROM software_flags")
        db.execute("DELETE FROM snapshots")
        db.execute("DELETE FROM masters")
        db.commit()
        db.close()

    def _seed_results_for_pagination(self, count=30):
        db = sqlite3.connect(os.environ["DB_PATH"])
        cur = db.cursor()
        cur.execute("INSERT INTO masters (pc_name, label) VALUES (?, ?)", ("PC-01", "Bureau"))
        master_id = cur.lastrowid
        cur.execute(
            "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?)",
            (master_id, "2026-03-22", "[]", count),
        )
        snapshot_id = cur.lastrowid

        for i in range(count):
            name = f"Office Tool {i:02d}"
            version = f"{i}.0"
            editor = "FabSoft"
            source = "installer"
            blob = f"{name} {version} {editor} {source}".lower()
            cur.execute(
                """
                INSERT INTO software_index (
                    snapshot_id,
                    master_id,
                    software_name,
                    software_version,
                    software_editor,
                    software_source,
                    search_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, master_id, name, version, editor, source, blob),
            )

        db.commit()
        db.close()

    def _seed_named_software(self, software_names):
        db = sqlite3.connect(os.environ["DB_PATH"])
        cur = db.cursor()
        cur.execute("INSERT INTO masters (pc_name, label) VALUES (?, ?)", ("PC-CSV", "Export"))
        master_id = cur.lastrowid
        cur.execute(
            "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?)",
            (master_id, "2026-03-22", "[]", len(software_names)),
        )
        snapshot_id = cur.lastrowid

        for name in software_names:
            version = "1.0"
            editor = "FabSoft"
            source = "installer"
            blob = f"{name} {version} {editor} {source}".lower()
            cur.execute(
                """
                INSERT INTO software_index (
                    snapshot_id,
                    master_id,
                    software_name,
                    software_version,
                    software_editor,
                    software_source,
                    search_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, master_id, name, version, editor, source, blob),
            )

        db.commit()
        db.close()

    def test_search_uses_default_sort_when_query_params_invalid(self):
        response = self.client.get(
            "/search?q=office&scope=all&sort_by=drop_table&sort_dir=sideways"
        )
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn('option value="master" selected', html)
        self.assertIn('option value="asc" selected', html)

    def test_search_keeps_valid_sort_parameters(self):
        response = self.client.get(
            "/search?q=office&scope=latest&sort_by=date&sort_dir=desc"
        )
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn('option value="date" selected', html)
        self.assertIn('option value="desc" selected', html)

    def test_pagination_links_keep_sort_parameters(self):
        self._seed_results_for_pagination(count=30)

        response = self.client.get(
            "/search?q=office&scope=all&per_page=25&page=1&sort_by=software&sort_dir=desc"
        )
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn("page=2", html)
        self.assertIn("sort_by=software", html)
        self.assertIn("sort_dir=desc", html)

    def test_csv_export_respects_sorting_parameters(self):
        self._seed_named_software(["Office Alpha", "Office Charlie", "Office Bravo"])

        response = self.client.get(
            "/search?q=office&scope=all&sort_by=software&sort_dir=desc&export=csv"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)

        payload = response.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(payload), delimiter=";")
        rows = list(reader)

        self.assertGreaterEqual(len(rows), 4)
        software_names = [row[5] for row in rows[1:4]]
        self.assertEqual(
            software_names,
            ["Office Charlie", "Office Bravo", "Office Alpha"],
        )

    def test_search_page_contains_autosubmit_js_hooks(self):
        response = self.client.get("/search")
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn("function autoSubmitIfQueryPresent()", html)
        self.assertIn("scopeSelect.addEventListener('change', autoSubmitIfQueryPresent);", html)
        self.assertIn("perPageSelect.addEventListener('change', autoSubmitIfQueryPresent);", html)
        self.assertIn("sortBySelect.addEventListener('change', autoSubmitIfQueryPresent);", html)
        self.assertIn("sortDirSelect.addEventListener('change', autoSubmitIfQueryPresent);", html)

    def test_search_page_contains_reset_preferences_hooks(self):
        response = self.client.get("/search")
        self.assertEqual(response.status_code, 200)

        html = response.get_data(as_text=True)
        self.assertIn('id="resetSearchPreferences"', html)
        self.assertIn("localStorage.removeItem(key);", html)
        self.assertIn("'fabinventory.search.scope'", html)
        self.assertIn("'fabinventory.search.per_page'", html)
        self.assertIn("'fabinventory.search.sort_by'", html)
        self.assertIn("'fabinventory.search.sort_dir'", html)


if __name__ == "__main__":
    unittest.main()
