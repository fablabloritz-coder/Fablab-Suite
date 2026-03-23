#!/usr/bin/env python3
"""
Test v3 category integration
Validates that software categories are properly parsed, stored, and retrieved from v3 HTML reports.
"""

import os
import sys
import json
import sqlite3
import unittest
import tempfile
from pathlib import Path

# Setup environment BEFORE importing app
sys.path.insert(0, os.path.dirname(__file__))

# Create temp directory for DB
temp_dir = tempfile.mkdtemp()
temp_db = os.path.join(temp_dir, "test.db")
os.environ["DB_PATH"] = temp_db

from app import app, parse_inventory_html, _normalize_software_fields, _upsert_snapshot_software_index, init_db, get_db


class V3CategoryIntegrationTests(unittest.TestCase):
    """Tests for v3 software category parsing and storage."""

    def setUp(self):
        """Initialize test database."""
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        init_db()

    def tearDown(self):
        """Clean up test context."""
        self.ctx.pop()

    def test_normalize_software_fields_extracts_category(self):
        """Test that _normalize_software_fields extracts and validates category."""
        # Test with valid categories
        sw = {"n": "Firefox", "v": "128.0", "e": "Mozilla", "src": "Registre", "cat": "main"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "main")

        sw = {"n": "Update", "v": "1.0", "e": "Autodesk", "src": "Registre", "cat": "update"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "update")

        # Test fallback to "main" when category missing
        sw = {"n": "Legacy Software", "v": "1.0", "e": "Old Inc", "src": "Registre"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "main")

        # Test validation against invalid categories
        sw = {"n": "Bad", "v": "1.0", "e": "X", "src": "Registre", "cat": "invalid_cat"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "main")  # Should fallback to inferred/main

        # Test alternate category keys from third-party snapshot generators
        sw = {"n": "SDK Tool", "v": "1.0", "e": "Vendor", "src": "Registre", "category": "composant"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "composant")

        # Test heuristic inference when no explicit category exists
        sw = {"n": "Windows Cumulative Update KB5030211", "v": "1.0", "e": "Microsoft", "src": "Registre"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "update")

        sw = {"n": "Microsoft Visual C++ 2015-2022 Redistributable", "v": "14.0", "e": "Microsoft", "src": "Registre"}
        name, version, editor, source, category = _normalize_software_fields(sw)
        self.assertEqual(category, "composant")

    def test_software_stored_with_category(self):
        """Test that software is stored in DB with category column."""
        db = get_db()
        
        # Insert test snapshot
        master_id = 1
        snapshot_id = 1
        db.execute(
            "INSERT INTO masters (id, pc_name) VALUES (?, ?)",
            (master_id, "TEST-PC"),
        )
        db.execute(
            "INSERT INTO snapshots (id, master_id, scan_date, software_json) VALUES (?, ?, ?, ?)",
            (snapshot_id, master_id, "2026-03-23", "[]"),
        )
        
        # Create software list mix: main + update + composant
        software = [
            {"n": "Firefox", "v": "128", "e": "Mozilla", "src": "Registre", "cat": "main"},
            {"n": "AutoCAD Update", "v": "2026.1", "e": "Autodesk", "src": "Registre", "cat": "update"},
            {"n": ".NET Framework", "v": "4.8", "e": "Microsoft", "src": "Registre", "cat": "composant"},
        ]
        
        _upsert_snapshot_software_index(db, snapshot_id, master_id, software)
        db.commit()
        
        # Verify categories stored
        rows = db.execute(
            "SELECT software_name, software_category FROM software_index WHERE snapshot_id = ? ORDER BY software_name",
            (snapshot_id,),
        ).fetchall()
        
        self.assertEqual(len(rows), 3)
        categories = {row["software_name"]: row["software_category"] for row in rows}
        
        self.assertEqual(categories[".NET Framework"], "composant")
        self.assertEqual(categories["AutoCAD Update"], "update")
        self.assertEqual(categories["Firefox"], "main")

    def test_backward_compatibility_old_snapshots(self):
        """Test that old snapshots without category are given 'main' as default."""
        db = get_db()
        
        # Insert master + snapshot
        master_id = 2
        snapshot_id = 2
        db.execute(
            "INSERT INTO masters (id, pc_name) VALUES (?, ?)",
            (master_id, "OLD-PC"),
        )
        
        # Old software JSON (no "cat" field)
        old_software_json = json.dumps([
            {"n": "Old App", "v": "1.0", "e": "OldCorp", "src": "Registre"},
            {"n": "Legacy Tool", "v": "2.0", "e": "Legacy Inc", "src": "Utilisateur"},
        ])
        
        db.execute(
            "INSERT INTO snapshots (id, master_id, scan_date, software_json) VALUES (?, ?, ?, ?)",
            (snapshot_id, master_id, "2026-01-01", old_software_json),
        )
        db.commit()
        
        # Parse and index
        software_list = json.loads(old_software_json)
        _upsert_snapshot_software_index(db, snapshot_id, master_id, software_list)
        db.commit()
        
        # Verify categories assigned to "main"
        rows = db.execute(
            "SELECT software_category FROM software_index WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["software_category"], "main")

    def test_category_counts_by_class(self):
        """Test ability to query and count software by category."""
        db = get_db()
        
        master_id = 3
        snapshot_id = 3
        db.execute(
            "INSERT INTO masters (id, pc_name) VALUES (?, ?)",
            (master_id, "STATS-PC"),
        )
        
        # Mix of categories
        software = [
            {"n": f"Main-{i}", "v": "1.0", "e": "X", "src": "Registre", "cat": "main"}
            for i in range(5)
        ] + [
            {"n": f"Update-{i}", "v": "1.0", "e": "X", "src": "Registre", "cat": "update"}
            for i in range(3)
        ] + [
            {"n": f"Component-{i}", "v": "1.0", "e": "X", "src": "Registre", "cat": "composant"}
            for i in range(2)
        ]
        
        db.execute(
            "INSERT INTO snapshots (id, master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, master_id, "2026-03-23", json.dumps(software), len(software)),
        )
        db.commit()
        
        _upsert_snapshot_software_index(db, snapshot_id, master_id, software)
        db.commit()
        
        # Query counts by category
        stats = {}
        for cat in ("main", "update", "composant", "doublon"):
            count = db.execute(
                "SELECT COUNT(*) AS c FROM software_index WHERE snapshot_id = ? AND software_category = ?",
                (snapshot_id, cat),
            ).fetchone()["c"]
            stats[cat] = count
        
        self.assertEqual(stats["main"], 5)
        self.assertEqual(stats["update"], 3)
        self.assertEqual(stats["composant"], 2)
        self.assertEqual(stats["doublon"], 0)

    def test_admin_reindex_software_recomputes_categories(self):
        """Admin reindex should recompute categories for existing snapshots."""
        db = get_db()

        master_id = 4
        snapshot_id = 4
        db.execute(
            "INSERT INTO masters (id, pc_name) VALUES (?, ?)",
            (master_id, "REINDEX-PC"),
        )

        # Legacy payload: no cat field, should be inferred during reindex.
        legacy_software = [
            {"n": "Windows Cumulative Update KB5030211", "v": "1.0", "e": "Microsoft", "src": "Registre"},
            {"n": "Microsoft Visual C++ 2015-2022 Redistributable", "v": "14.0", "e": "Microsoft", "src": "Registre"},
        ]

        db.execute(
            "INSERT INTO snapshots (id, master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, master_id, "2026-03-23", json.dumps(legacy_software), len(legacy_software)),
        )
        db.commit()

        response = self.client.post("/admin/reindex-software", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

        rows = db.execute(
            "SELECT software_name, software_category FROM software_index WHERE snapshot_id = ? ORDER BY software_name",
            (snapshot_id,),
        ).fetchall()

        by_name = {row["software_name"]: row["software_category"] for row in rows}
        self.assertEqual(by_name["Windows Cumulative Update KB5030211"], "update")
        self.assertEqual(by_name["Microsoft Visual C++ 2015-2022 Redistributable"], "composant")


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        # Cleanup temp DB and directory
        try:
            if os.path.exists(temp_db):
                os.remove(temp_db)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except:
            pass
