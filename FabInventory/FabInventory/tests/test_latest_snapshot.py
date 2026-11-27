"""Tests du Lot 1 : le dernier scan est choisi par date de scan, pas par insertion."""
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot1-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db, simuler_base_ancienne  # noqa: E402
from app import (  # noqa: E402
    _upsert_snapshot_software_index,
    app,
    get_db,
    init_db,
)


class LatestSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_user_id"] = 1

    def _seed_two_scans(self):
        """Le scan le plus recent est insere en premier, le plus ancien en dernier."""
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, workflow_type) VALUES ('PC-LOT1', 'inventory')"
            ).lastrowid
            newest = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '28/11/2026 10:00', ?, 1)",
                (master_id, json.dumps([{"n": "NewApp", "v": "2", "e": "Ed", "src": "Registre"}])),
            ).lastrowid
            _upsert_snapshot_software_index(
                db, newest, master_id, [{"n": "NewApp", "v": "2", "e": "Ed", "src": "Registre"}]
            )
            oldest = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '01/01/2020 10:00', ?, 1)",
                (master_id, json.dumps([{"n": "OldApp", "v": "1", "e": "Ed", "src": "Registre"}])),
            ).lastrowid
            _upsert_snapshot_software_index(
                db, oldest, master_id, [{"n": "OldApp", "v": "1", "e": "Ed", "src": "Registre"}]
            )
            db.commit()
            return master_id, newest, oldest

    def test_latest_snapshots_choisit_la_date_la_plus_recente(self):
        master_id, newest, oldest = self._seed_two_scans()
        with app.app_context():
            db = get_db()
            latest = db.execute(
                "SELECT id FROM latest_snapshots WHERE master_id = ?", (master_id,)
            ).fetchall()
        self.assertEqual([row["id"] for row in latest], [newest])

    def test_accueil_affiche_le_dernier_scan_par_date(self):
        self._seed_two_scans()
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("28/11/2026 10:00", html)
        self.assertNotIn("01/01/2020 10:00", html)

    def test_master_detail_affiche_le_logiciel_du_dernier_scan(self):
        master_id, _, _ = self._seed_two_scans()
        html = self.client.get(f"/master/{master_id}").get_data(as_text=True)
        self.assertIn("NewApp", html)
        self.assertNotIn("OldApp", html)

    def test_admin_masters_pointe_vers_le_dernier_scan(self):
        master_id, newest, _ = self._seed_two_scans()
        html = self.client.get("/admin/masters").get_data(as_text=True)
        self.assertIn(f'href="/snapshot/{newest}"', html)

    def test_compare_propose_le_dernier_snapshot(self):
        master_id, newest, _ = self._seed_two_scans()
        with app.app_context():
            db = get_db()
            other_id = db.execute(
                "INSERT INTO masters (pc_name, workflow_type) VALUES ('PC-AUTRE', 'inventory')"
            ).lastrowid
            other_snap = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '20/11/2026 10:00', '[]', 0)",
                (other_id,),
            ).lastrowid
            db.commit()
        html = self.client.get("/compare").get_data(as_text=True)
        self.assertIn(f'value="{newest}"', html)
        self.assertIn(f'value="{other_snap}"', html)

    def test_export_ne_prend_que_le_dernier_scan(self):
        master_id, _, _ = self._seed_two_scans()
        rows = (
            self.client.get(f"/master/{master_id}/export")
            .data.decode("utf-8-sig")
            .strip()
            .splitlines()
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("NewApp", rows[1])
        self.assertNotIn("OldApp", "\n".join(rows))

    def test_recherche_latest_et_all(self):
        self._seed_two_scans()
        latest = self.client.get("/search?q=App&scope=latest").get_data(as_text=True)
        self.assertIn("NewApp", latest)
        self.assertNotIn("OldApp", latest)

        all_scans = self.client.get("/search?q=App&scope=all").get_data(as_text=True)
        self.assertIn("NewApp", all_scans)
        self.assertIn("OldApp", all_scans)

    def test_backfill_remplit_scan_date_iso(self):
        _, newest, oldest = self._seed_two_scans()
        with app.app_context():
            db = get_db()
            simuler_base_ancienne(db)
            init_db()
            values = {
                row["id"]: row["scan_date_iso"]
                for row in db.execute(
                    "SELECT id, scan_date_iso FROM snapshots WHERE id IN (?, ?)",
                    (newest, oldest),
                ).fetchall()
            }
        self.assertEqual(values[newest], "2026-11-28 10:00:00")
        self.assertEqual(values[oldest], "2020-01-01 10:00:00")

    def test_dashboard_compte_un_master_obsolete(self):
        self._seed_two_scans()
        with app.app_context():
            db = get_db()
            old_id = db.execute(
                "INSERT INTO masters (pc_name, workflow_type) VALUES ('PC-2020', 'inventory')"
            ).lastrowid
            db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
                "VALUES (?, '01/01/2020 10:00', '[]', 0)",
                (old_id,),
            )
            db.commit()
        html = self.client.get("/admin/").get_data(as_text=True)
        self.assertIn("Scans &gt;30j", html)
        self.assertRegex(html, r">1</div>\s*<div class=\"small text-muted\">Scans &gt;30j")


if __name__ == "__main__":
    unittest.main()
