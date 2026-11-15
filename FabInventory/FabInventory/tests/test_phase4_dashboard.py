"""Phase 4 Test: Category Distribution API and Dashboard Widget"""
import unittest, json
# Isole la base et les uploads de ce module : les tests peuvent tourner en CI
# sans /data, et chaque fichier dispose de sa propre base temporaire.
import os as _os
import sys as _sys
import tempfile as _tempfile

_APP_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db

_MODULE_TMP = _tempfile.mkdtemp(prefix="fabinventory-tests-")
_os.environ["DB_PATH"] = _os.path.join(_MODULE_TMP, "test.db")
_os.environ["UPLOAD_FOLDER"] = _os.path.join(_MODULE_TMP, "uploads")
_os.environ["SECRET_KEY"] = "tests"

from app import app, init_db, get_db

class Phase4CategoryDashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            reset_temp_db(init_db)
            db = get_db()
            db.execute("INSERT INTO masters (pc_name, label, workflow_type) VALUES (?, ?, ?)",
                       ("TEST-001", "Test Master", "inventory"))
            db.commit()
            master = db.execute("SELECT id FROM masters WHERE pc_name = ?", ("TEST-001",)).fetchone()
            master_id = master["id"]
            db.execute("INSERT INTO snapshots (master_id, os_info, scan_date, software_json) VALUES (?, ?, ?, ?)",
                       (master_id, "Windows 10", "2026-03-23 10:00:00", "[]"))
            db.commit()
            snapshot = db.execute("SELECT id FROM snapshots WHERE master_id = ?", (master_id,)).fetchone()
            snapshot_id = snapshot["id"]
            test_software = [("App1", "1.0", "Vendor", "vendor.com", "main"),
                           ("App2", "2.0", "Vendor", "vendor.com", "main"),
                           ("App3", "3.0", "Vendor", "vendor.com", "update"),
                           ("App4", "4.0", "Vendor", "vendor.com", "composant"),
                           ("App5", "5.0", "Vendor", "vendor.com", "doublon")]
            for name, version, editor, source, category in test_software:
                db.execute("INSERT INTO software_index (master_id, snapshot_id, software_name, software_version, software_editor, software_source, software_category) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (master_id, snapshot_id, name, version, editor, source, category))
            db.commit()

    def test_01_api_category_stats_endpoint(self):
        """Test: /api/category-stats endpoint returns category distribution"""
        response = self.client.get("/api/category-stats")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("categories", data)
        self.assertIn("total", data)
        self.assertIn("percentage", data)

    def test_02_api_returns_correct_counts(self):
        """Test: Category stats reflect actual database counts"""
        response = self.client.get("/api/category-stats")
        data = response.get_json()
        self.assertEqual(data["categories"]["main"], 2)
        self.assertEqual(data["categories"]["update"], 1)
        self.assertEqual(data["categories"]["composant"], 1)
        self.assertEqual(data["categories"]["doublon"], 1)
        self.assertEqual(data["total"], 5)

    def test_03_api_returns_percentages(self):
        """Test: Category percentages calculated correctly"""
        response = self.client.get("/api/category-stats")
        data = response.get_json()
        self.assertEqual(data["percentage"]["main"], 40.0)
        self.assertEqual(data["percentage"]["update"], 20.0)
        self.assertEqual(data["percentage"]["composant"], 20.0)
        self.assertEqual(data["percentage"]["doublon"], 20.0)

    def test_04_dashboard_renders(self):
        """Test: Dashboard page renders without errors"""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Distribution des categories", response.data)
        self.assertIn(b"categoryChart", response.data)
        self.assertIn(b"stat-main", response.data)

if __name__ == "__main__":
    unittest.main(verbosity=2)
