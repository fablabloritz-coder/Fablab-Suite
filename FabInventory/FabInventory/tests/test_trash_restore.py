"""Tests de la corbeille : suppression archivee des masters et des scans.

Chaque suppression doit conserver assez de donnees pour restaurer le master ou
le scan a l'identique, y compris les flags, la checklist, les absences et
l'index de recherche.
"""
import io
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-trash-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"
os.environ["ADMIN_PASSWORD"] = "trash-admin-password"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402


def _software(name="Firefox", version="128.0"):
    return {
        "n": name,
        "v": version,
        "e": "Mozilla",
        "d": "-",
        "s": 1.0,
        "src": "Registre",
    }


def _payload(pc_name, scan_date="26/11/2026 21:00", software=None):
    return {
        "schema": 3,
        "script_version": "3.1.0",
        "generated_at": "2026-11-26T21:00:00",
        "pcName": pc_name,
        "date": scan_date,
        "system": {"os": "Windows 11", "cpu": "CPU test", "ram": 16.0},
        "disks": [],
        "network": [],
        "services": [],
        "software": software if software is not None else [_software()],
        "warnings": [],
    }


def _html(payload):
    body = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return (
        '<html><body><script id="inventoryData" type="application/json">'
        f"{body}</script></body></html>"
    )


class TrashRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)

    def _upload(self, payload, filename="scan.html"):
        return self.client.post(
            "/upload",
            data={"file": (io.BytesIO(_html(payload).encode("utf-8")), filename)},
            content_type="multipart/form-data",
        )

    def _master_id(self, name):
        with app.app_context():
            row = get_db().execute(
                "SELECT id FROM masters WHERE pc_name = ?", (name,)
            ).fetchone()
            return row["id"] if row else None

    def _archive(self, kind):
        with app.app_context():
            return get_db().execute(
                "SELECT * FROM deleted_items WHERE kind = ? AND restored_at IS NULL "
                "ORDER BY id DESC",
                (kind,),
            ).fetchone()

    def test_suppression_master_puis_restauration_complete(self):
        self.assertEqual(self._upload(_payload("PC-CORBEILLE")).status_code, 302)
        master_id = self._master_id("PC-CORBEILLE")
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO software_flags (master_id, software_name, is_important) "
                "VALUES (?, 'Firefox', 1)",
                (master_id,),
            )
            db.execute(
                "INSERT INTO software_followups (master_id, software_key, software_name) "
                "VALUES (?, '7-zip', '7-Zip')",
                (master_id,),
            )
            db.commit()

        self.assertEqual(
            self.client.post(f"/master/{master_id}/delete").status_code, 302
        )
        archive = self._archive("master")
        self.assertIsNotNone(archive)
        self.assertEqual(archive["label"], "PC-CORBEILLE")

        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"], 0
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_followups WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                0,
            )

        trash = self.client.get("/admin/trash")
        self.assertEqual(trash.status_code, 200)
        self.assertIn("PC-CORBEILLE", trash.get_data(as_text=True))

        self.assertEqual(
            self.client.post(f"/admin/trash/{archive['id']}/restore").status_code, 302
        )
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM masters WHERE id = ?", (master_id,)
                ).fetchone()["c"],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM snapshots WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_flags WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_followups WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_index WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                1,
            )
            restored = db.execute(
                "SELECT restored_at FROM deleted_items WHERE id = ?", (archive["id"],)
            ).fetchone()
            self.assertIsNotNone(restored["restored_at"])

        self.assertIn("PC-CORBEILLE", self.client.get("/").get_data(as_text=True))
        self.assertIn(
            "Firefox",
            self.client.get("/search?q=firefox").get_data(as_text=True),
        )

    def test_suppression_scan_puis_restauration(self):
        self.assertEqual(
            self._upload(
                _payload("PC-SCAN", software=[_software("Firefox")])
            ).status_code,
            302,
        )
        self.assertEqual(
            self._upload(
                _payload(
                    "PC-SCAN",
                    scan_date="27/11/2026 09:00",
                    software=[_software("VLC", "3.0")],
                )
            ).status_code,
            302,
        )
        master_id = self._master_id("PC-SCAN")
        with app.app_context():
            snaps = get_db().execute(
                "SELECT id FROM snapshots WHERE master_id = ? ORDER BY id",
                (master_id,),
            ).fetchall()
        self.assertEqual(len(snaps), 2)
        removed_id = snaps[0]["id"]

        self.assertEqual(
            self.client.post(f"/snapshot/{removed_id}/delete").status_code, 302
        )
        archive = self._archive("snapshot")
        self.assertIsNotNone(archive)
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM snapshots WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_index WHERE snapshot_id = ?",
                    (removed_id,),
                ).fetchone()["c"],
                0,
            )

        self.assertEqual(
            self.client.post(f"/admin/trash/{archive['id']}/restore").status_code, 302
        )
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM snapshots WHERE id = ?", (removed_id,)
                ).fetchone()["c"],
                1,
            )
            self.assertGreater(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_index WHERE snapshot_id = ?",
                    (removed_id,),
                ).fetchone()["c"],
                0,
            )

    def test_restauration_refusee_si_le_nom_est_reutilise(self):
        payload = _payload("PC-DOUBLE")
        self.assertEqual(self._upload(payload).status_code, 302)
        master_id = self._master_id("PC-DOUBLE")
        self.assertEqual(
            self.client.post(f"/master/{master_id}/delete").status_code, 302
        )
        archive = self._archive("master")

        self.assertEqual(self._upload(payload).status_code, 302)
        self.assertNotEqual(self._master_id("PC-DOUBLE"), master_id)

        response = self.client.post(
            f"/admin/trash/{archive['id']}/restore", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("existe deja", response.get_data(as_text=True))

        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM masters WHERE pc_name = 'PC-DOUBLE'"
                ).fetchone()["c"],
                1,
            )
            pending = db.execute(
                "SELECT restored_at FROM deleted_items WHERE id = ?", (archive["id"],)
            ).fetchone()
            self.assertIsNone(pending["restored_at"])

    def test_corbeille_reservee_aux_admins(self):
        anonymous = app.test_client()
        response = anonymous.get("/admin/trash")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/login"))
        self.assertEqual(anonymous.post("/admin/trash/1/restore").status_code, 302)


if __name__ == "__main__":
    unittest.main()
