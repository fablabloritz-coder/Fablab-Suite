"""Phase 3 Test: CSV Export with Software Categories"""
import unittest, json, io, csv
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
            master_id = master["id"]
            db.execute("INSERT INTO snapshots (master_id, os_info, scan_date, software_json) VALUES (?, ?, ?, ?)",
                       (master_id, "Windows 10", "2026-03-23 10:00:00", "[]"))
            db.commit()
            snapshot = db.execute("SELECT id FROM snapshots WHERE master_id = ?", (master_id,)).fetchone()
            snapshot_id = snapshot["id"]
            test_software = [("Python", "3.11", "Python.org", "python.org", "main"), ("7-Zip", "26.00", "Igor Pavlov", "7-zip.org", "main"),
                           ("Git", "2.45", "GitHub", "git-scm.com", "composant"), ("Node.js", "18.0.0", "OpenJS Foundation", "nodejs.org", "composant"),
                           ("VSCode", "1.90", "Microsoft", "vs-code", "update"), ("Duplicate App", "1.0", "Vendor", "vendor.com", "doublon")]
            for name, version, editor, source, category in test_software:
                db.execute("INSERT INTO software_index (master_id, snapshot_id, software_name, software_version, software_editor, software_source, software_category) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (master_id, snapshot_id, name, version, editor, source, category))
            db.commit()

    def test_search_csv_includes_category(self):
        response = self.client.get("/search?query=Python&export=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"text/csv", response.content_type)
        csv_content = response.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(csv_content), delimiter=";")
        self.assertIn("software_category", reader.fieldnames)

    def test_master_export_exists(self):
        with self.app.app_context():
            db = get_db()
            master = db.execute("SELECT id FROM masters WHERE pc_name = ?", ("TEST-001",)).fetchone()
            response = self.client.get(f"/master/{master['id']}/export")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"text/csv", response.content_type)

    def test_master_export_has_categories(self):
        with self.app.app_context():
            db = get_db()
            master = db.execute("SELECT id FROM masters WHERE pc_name = ?", ("TEST-001",)).fetchone()
            response = self.client.get(f"/master/{master['id']}/export")
            csv_content = response.data.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(csv_content), delimiter=";")
            rows = list(reader)
            self.assertEqual(len(rows), 6)
            categories = [row["software_category"] for row in rows]
            self.assertIn("main", categories)
            self.assertIn("composant", categories)

if __name__ == "__main__":
    unittest.main()
