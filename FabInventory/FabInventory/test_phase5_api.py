"""Phase 5 Test: Software filtering API endpoint"""
import unittest, json
from app import app, init_db, get_db

class Phase5SoftwareFilterAPITests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            init_db()
            db = get_db()
            db.execute("INSERT INTO masters (pc_name, label, workflow_type) VALUES (?, ?, ?)",
                       ("TEST-001", "Test", "inventory"))
            db.commit()
            master = db.execute("SELECT id FROM masters WHERE pc_name = ?", ("TEST-001",)).fetchone()
            self.master_id = master["id"]
            db.execute("INSERT INTO snapshots (master_id, os_info, scan_date, software_json) VALUES (?, ?, ?, ?)",
                       (self.master_id, "Windows", "2026-03-23", "[]"))
            db.commit()
            snapshot = db.execute("SELECT id FROM snapshots WHERE master_id = ?", (self.master_id,)).fetchone()
            snapshot_id = snapshot["id"]
            test_software = [("App1", "1.0", "V1", "src1", "main"),
                           ("App2", "2.0", "V2", "src2", "main"),
                           ("App3", "3.0", "V3", "src3", "update"),
                           ("App4", "4.0", "V4", "src4", "composant")]
            for name, ver, ed, src, cat in test_software:
                db.execute("INSERT INTO software_index (master_id, snapshot_id, software_name, software_version, software_editor, software_source, software_category) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (self.master_id, snapshot_id, name, ver, ed, src, cat))
            db.commit()

    def test_01_api_software_no_filters(self):
        """Test: /api/software returns all software"""
        response = self.client.get("/api/software")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("software", data)
        self.assertIn("total", data)
        self.assertGreater(len(data["software"]), 0)

    def test_02_api_software_filter_by_category(self):
        """Test: /api/software?category=main filters correctly"""
        response = self.client.get(f"/api/software?category=main")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for item in data["software"]:
            self.assertEqual(item["category"], "main")

    def test_03_api_software_filter_by_master(self):
        """Test: /api/software?master_id=X filters by master"""
        response = self.client.get(f"/api/software?master_id={self.master_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for item in data["software"]:
            self.assertEqual(item["master_id"], self.master_id)

    def test_04_api_software_combined_filters(self):
        """Test: /api/software?category=main&master_id=X applies both filters"""
        response = self.client.get(f"/api/software?category=main&master_id={self.master_id}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        for item in data["software"]:
            self.assertEqual(item["category"], "main")
            self.assertEqual(item["master_id"], self.master_id)

    def test_05_api_software_invalid_category(self):
        """Test: /api/software?category=invalid returns 400"""
        response = self.client.get("/api/software?category=invalid")
        self.assertEqual(response.status_code, 400)

if __name__ == "__main__":
    unittest.main(verbosity=2)
