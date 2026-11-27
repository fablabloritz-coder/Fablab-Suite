"""Tests du Lot 8 : journal d'audit des ecritures.

Chaque action journalisee doit garder l'auteur, la cible et un resume sans
recopier les notes de licence ni les mots de passe. La page admin, l'export
CSV et la retention sont couverts.
"""
import io
import json
import os
import sys
import tempfile
import unittest

from werkzeug.security import generate_password_hash

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot8-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests-secrets-lot8"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402


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


class AuditLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _upload(self, pc_name="M1", scan_date="28/11/2026 10:00", software=None):
        html = _inventory_html(
            pc_name, scan_date, software or [("Firefox", "129"), ("7-Zip", "24.0")]
        )
        response = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(html.encode("utf-8")), "scan.html")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            return get_db().execute(
                "SELECT id FROM masters WHERE pc_name = ? AND workflow_type = 'inventory'",
                (pc_name,),
            ).fetchone()["id"]

    def _entries(self, action=None):
        with app.app_context():
            db = get_db()
            if action:
                return db.execute(
                    "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
                ).fetchall()
            return db.execute("SELECT * FROM audit_log ORDER BY id").fetchall()

    def _actions(self):
        return [row["action"] for row in self._entries()]

    # ── ecritures journalisees ───────────────────────────────────────────────

    def test_import_et_flag_journalises_sans_note(self):
        self._upload()
        response = self.client.post(
            "/api/flag",
            json={
                "master_id": self._upload(pc_name="M2", scan_date="29/11/2026 10:00"),
                "name": "Firefox",
                "important": 1,
                "is_licensed": 1,
                "license_type": "reseau",
                "note": "mot-de-passe-tres-secret",
            },
        )
        self.assertEqual(response.status_code, 200)

        actions = self._actions()
        self.assertEqual(actions.count("import"), 2)
        self.assertEqual(actions.count("flag"), 1)

        imported = self._entries("import")[0]
        self.assertEqual(imported["username"], "admin")
        self.assertEqual(imported["target_type"], "parc")
        self.assertEqual(imported["ip"], "127.0.0.1")
        self.assertIn("1 master(s) créé(s)", imported["summary"])

        flag = self._entries("flag")[0]
        self.assertEqual(flag["target_label"], "Firefox")
        self.assertIn("important=oui", flag["summary"])
        self.assertIn("type reseau", flag["summary"])
        self.assertNotIn("mot-de-passe-tres-secret", flag["summary"])
        self.assertNotIn("mot-de-passe-tres-secret", " ".join(
            row["summary"] for row in self._entries()
        ))

    def test_suppression_restauration_et_purge(self):
        master_id = self._upload()
        self.client.post(f"/master/{master_id}/delete")
        self.assertIn("master_delete", self._actions())

        with app.app_context():
            archive_id = get_db().execute(
                "SELECT id FROM deleted_items WHERE kind = 'master' AND restored_at IS NULL"
            ).fetchone()["id"]
        self.client.post(f"/admin/trash/{archive_id}/restore")
        self.assertIn("trash_restore", self._actions())

        self.client.post("/admin/trash/purge")
        self.assertIn("trash_purge", self._actions())
        restored = self._entries("trash_restore")[0]
        self.assertEqual(restored["target_type"], "master")
        self.assertEqual(restored["target_label"], "M1")

    def test_modification_master_et_suppression_scan(self):
        master_id = self._upload(scan_date="28/11/2026 10:00")
        self._upload(scan_date="29/11/2026 10:00")

        response = self.client.post(
            f"/master/{master_id}/edit",
            data={"pc_name": "M1-BIS", "label": "", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        edited = self._entries("master_edit")[0]
        self.assertEqual(edited["target_label"], "M1-BIS")
        self.assertIn("M1 → M1-BIS", edited["summary"])

        with app.app_context():
            snap_id = get_db().execute(
                "SELECT id FROM snapshots WHERE master_id = ? ORDER BY id LIMIT 1",
                (master_id,),
            ).fetchone()["id"]
        self.client.post(f"/snapshot/{snap_id}/delete")
        deleted = self._entries("snapshot_delete")[0]
        self.assertEqual(deleted["target_id"], snap_id)
        self.assertIn("M1-BIS", deleted["target_label"])

    def test_checklist_et_decision_absence(self):
        response = self.client.post(
            "/master/new", data={"pc_name": "PREP-01", "label": "", "notes": ""}
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            prep_id = get_db().execute(
                "SELECT id FROM masters WHERE pc_name = 'PREP-01'"
            ).fetchone()["id"]
        self.assertIn("master_create", self._actions())

        self.client.post(
            f"/master/{prep_id}/roadmap/add",
            data={"software_name": "LibreOffice", "install_url": "", "note": ""},
        )
        with app.app_context():
            item_id = get_db().execute(
                "SELECT id FROM roadmap_items WHERE master_id = ?", (prep_id,)
            ).fetchone()["id"]
        self.client.post(f"/master/{prep_id}/roadmap/{item_id}/toggle")
        self.client.post(f"/master/{prep_id}/roadmap/{item_id}/delete")
        self.assertEqual(
            [a for a in self._actions() if a.startswith("roadmap")],
            ["roadmap_add", "roadmap_toggle", "roadmap_delete"],
        )

        master_id = self._upload()
        with app.app_context():
            db = get_db()
            followup_id = db.execute(
                "INSERT INTO software_followups (master_id, software_key, software_name) "
                "VALUES (?, 'firefox', 'Firefox')",
                (master_id,),
            ).lastrowid
            db.commit()
        self.client.post(
            f"/master/{master_id}/absences",
            data={"followup_id": followup_id, "status": "intentional"},
        )
        followup = self._entries("followup")[0]
        self.assertEqual(followup["target_label"], "Firefox")
        self.assertIn("Retiré volontairement", followup["summary"])

    def test_parametres_journalises_sans_mot_de_passe(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "UPDATE admin_users SET password_hash = ? WHERE id = 1",
                (generate_password_hash("ancien-mot-de-passe"),),
            )
            db.commit()
        response = self.client.post(
            "/admin/settings",
            data={
                "old_password": "ancien-mot-de-passe",
                "new_password": "nouveau-mot-de-passe",
                "confirm_password": "nouveau-mot-de-passe",
            },
        )
        self.assertEqual(response.status_code, 302)
        entries = self._entries("settings")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["summary"], "Mot de passe modifié")
        self.assertNotIn("nouveau-mot-de-passe", entries[0]["summary"])

    # ── consultation, filtres, export, retention ────────────────────────────

    def test_page_filtres_et_export_csv(self):
        master_id = self._upload()
        self.client.post(
            "/api/flag",
            json={"master_id": master_id, "name": "Firefox", "important": 1},
        )

        body = self.client.get("/admin/journal").get_data(as_text=True)
        self.assertIn("Journal d'audit", body)
        self.assertIn("2 entrée(s)", body)
        self.assertIn("Import d&#39;inventaire", body)

        only_flags = self.client.get("/admin/journal?action=flag").get_data(as_text=True)
        self.assertIn("1 entrée(s)", only_flags)
        self.assertIn("Flag logiciel", only_flags)
        self.assertNotIn("1 master(s) créé(s)", only_flags)

        response = self.client.get("/admin/journal/export.csv?action=import")
        self.assertEqual(response.status_code, 200)
        self.assertIn("FabInventory-Journal-Audit.csv", response.headers["Content-Disposition"])
        lines = response.data.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(
            lines[0].split(";"),
            [
                "horodatage",
                "utilisateur",
                "action",
                "type_cible",
                "cible",
                "resume",
                "adresse_ip",
            ],
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("Import d'inventaire", lines[1])

    def test_retention_supprime_les_anciennes_entrees(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO audit_log (created_at, username, action, target_label, summary) "
                "VALUES (datetime('now', '-400 days'), 'ancien', 'import', 'Vieux', 'Ancienne entrée')"
            )
            db.commit()

        self.client.post("/admin/journal", data={"retention_days": "30"})
        with app.app_context():
            db = get_db()
            self.assertIsNone(
                db.execute("SELECT id FROM audit_log WHERE username = 'ancien'").fetchone()
            )
            self.assertEqual(
                db.execute(
                    "SELECT value FROM app_meta WHERE key = 'audit_retention_days'"
                ).fetchone()["value"],
                "30",
            )
        self.assertIn("settings", self._actions())

        # 0 conserve tout : l'entree ancienne reste apres une nouvelle ecriture.
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO audit_log (created_at, username, action, summary) "
                "VALUES (datetime('now', '-400 days'), 'ancien', 'import', 'Conservée')"
            )
            db.commit()
        self.client.post("/admin/journal", data={"retention_days": "0"})
        with app.app_context():
            db = get_db()
            self.assertIsNotNone(
                db.execute("SELECT id FROM audit_log WHERE username = 'ancien'").fetchone()
            )

    def test_journal_reserve_aux_admins_et_pages_intactes(self):
        anonymous = app.test_client()
        response = anonymous.get("/admin/journal")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

        for path in ("/", "/sante", "/admin/journal"):
            self.assertEqual(self.client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()
