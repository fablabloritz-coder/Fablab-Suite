"""Phase 3 CSV Export with Categories - Simple Tests"""
import unittest, io, csv
from app import app, init_db, get_db

class CSVExportPhase3Tests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            init_db()
            db = get_db()
            db.execute("INSERT INTO masters (pc_name, label, workflow_type) VALUES (?, ?, ?)",
                       ("TEST-001", "Test Master 001", "inventory"))
            db.commit()
            master = db.execute("SELECT id FROM masters WHERE pc_name = ?", ("TEST-001",)).fetchone()
            self.master_id = master["id"]
            db.execute("INSERT INTO snapshots (master_id, os_info, scan_date, software_json) VALUES (?, ?, ?, ?)",
                       (self.master_id, "Windows 10", "2026-03-23 10:00:00", "[]"))
            db.commit()
            snapshot = db.execute("SELECT id FROM snapshots WHERE master_id = ?", (self.master_id,)).fetchone()
            snapshot_id = snapshot["id"]
            test_software = [("Python", "3.11", "Python.org", "python.org", "main"),
                           ("7-Zip", "26.00", "Igor Pavlov", "7-zip.org", "main"),
                           ("Git", "2.45", "GitHub", "git-scm.com", "composant")]
            for name, version, editor, source, category in test_software:
                db.execute("INSERT INTO software_index (master_id, snapshot_id, software_name, software_version, software_editor, software_source, software_category) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (self.master_id, snapshot_id, name, version, editor, source, category))
            db.commit()

    def test_01_master_export_endpoint_works(self):
        """Test: Master CSV export endpoint returns 200"""
        response = self.client.get(f"/master/{self.master_id}/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/csv; charset=utf-8")

    def test_02_master_export_has_category_column(self):
        """Test: Master CSV export includes software_category column"""
        response = self.client.get(f"/master/{self.master_id}/export")
        csv_content = response.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(csv_content), delimiter=";")
        self.assertIn("software_category", reader.fieldnames)

    def test_03_master_export_has_data_with_categories(self):
        """Test: Master CSV export data contains category values"""
        response = self.client.get(f"/master/{self.master_id}/export")
        csv_content = response.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(csv_content), delimiter=";")
        rows = list(reader)
        self.assertGreater(len(rows), 0)
        categories = [row["software_category"] for row in rows]
        self.assertIn("main", categories)
        self.assertIn("composant", categories)

    def test_04_master_export_filename_correct(self):
        """Test: CSV export filename includes master pc_name"""
        response = self.client.get(f"/master/{self.master_id}/export")
        disposition = response.headers.get("Content-Disposition", "")
        self.assertIn("FabInventory-TEST-001", disposition)
        self.assertIn(".csv", disposition)

if __name__ == "__main__":
    unittest.main(verbosity=2)
