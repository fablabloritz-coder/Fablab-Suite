"""Tests du suivi des logiciels absents d'un master."""
import io
import json
import os
import re
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-followups-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402


def _software(name, version="1.0"):
    return {"n": name, "v": version, "e": "Éditeur", "d": "-", "s": 1.0, "src": "Registre"}


def _payload(date, software):
    return {
        "schema": 3,
        "script_version": "3.1.0",
        "generated_at": "2026-11-26T21:00:00",
        "pcName": "PC-ABSENCES",
        "date": date,
        "system": {"os": "Windows 11", "cpu": "CPU test", "ram": 16},
        "disks": [],
        "network": [],
        "services": [],
        "software": software,
        "warnings": [],
    }


def _html(payload):
    body = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return f'<html><body><script id="inventoryData" type="application/json">{body}</script></body></html>'


class SoftwareFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def _upload_initial(self, software):
        response = self.client.post(
            "/upload",
            data={
                "file": (
                    io.BytesIO(_html(_payload("26/11/2026 21:00", software)).encode("utf-8")),
                    "initial.html",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            db = get_db()
            return db.execute(
                "SELECT id FROM masters WHERE pc_name = 'PC-ABSENCES'"
            ).fetchone()["id"]

    def _preview_update(self, master_id, date, software):
        response = self.client.post(
            f"/master/{master_id}/update",
            data={
                "file": (
                    io.BytesIO(_html(_payload(date, software)).encode("utf-8")),
                    "update.html",
                ),
                "preview_limit": "10",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        token = re.search(r'name="confirm_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(token)
        return response.get_data(as_text=True), token.group(1)

    def _confirm(self, master_id, token, **decisions):
        data = {"confirm_token": token, "preview_limit": "10"}
        data.update(decisions)
        response = self.client.post(f"/master/{master_id}/update", data=data)
        self.assertEqual(response.status_code, 302)

    def _followups(self, master_id):
        with app.app_context():
            db = get_db()
            return db.execute(
                "SELECT * FROM software_followups WHERE master_id = ? ORDER BY id",
                (master_id,),
            ).fetchall()

    def test_absence_par_defaut_et_liste_ponctuelle(self):
        master_id = self._upload_initial([_software("Firefox"), _software("7-Zip", "24.0")])
        html, token = self._preview_update(
            master_id, "27/11/2026 21:00", [_software("Firefox", "129.0")]
        )
        self.assertIn("À vérifier", html)
        self.assertIn("Retiré volontairement", html)
        self.assertIn("À installer", html)
        self.assertIn("7-Zip", html)

        self._confirm(master_id, token)
        followups = self._followups(master_id)
        self.assertEqual(len(followups), 1)
        self.assertEqual(followups[0]["software_name"], "7-Zip")
        self.assertEqual(followups[0]["status"], "pending")
        self.assertEqual(followups[0]["old_version"], "24.0")

        page = self.client.get(f"/master/{master_id}/absences")
        self.assertEqual(page.status_code, 200)
        page_html = page.get_data(as_text=True)
        self.assertIn("7-Zip", page_html)
        self.assertIn("À vérifier", page_html)

        # La décision peut être changée depuis la liste, sans toucher au snapshot.
        change = self.client.post(
            f"/master/{master_id}/absences",
            data={"followup_id": followups[0]["id"], "status": "to_install"},
        )
        self.assertEqual(change.status_code, 302)
        self.assertEqual(self._followups(master_id)[0]["status"], "to_install")

    def test_choix_a_installer_et_resolution_automatique(self):
        master_id = self._upload_initial([_software("Firefox"), _software("7-Zip", "24.0")])
        _html_preview, token = self._preview_update(
            master_id, "27/11/2026 21:00", [_software("Firefox", "129.0")]
        )
        self._confirm(master_id, token, decision_0="to_install")
        followups = self._followups(master_id)
        self.assertEqual(len(followups), 1)
        self.assertEqual(followups[0]["status"], "to_install")

        page = self.client.get(f"/master/{master_id}")
        self.assertIn("Absences (1)", page.get_data(as_text=True))
        self.assertIn("à installer", page.get_data(as_text=True))

        # Le logiciel réapparaît : le suivi doit être résolu automatiquement.
        _html_preview, token = self._preview_update(
            master_id,
            "28/11/2026 21:00",
            [_software("Firefox", "129.0"), _software("7-Zip", "24.0")],
        )
        self._confirm(master_id, token)
        followups = self._followups(master_id)
        self.assertEqual(followups[0]["status"], "resolved")
        self.assertIsNotNone(followups[0]["resolved_snapshot_id"])

        page = self.client.get(f"/master/{master_id}/absences")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("7-Zip", page.get_data(as_text=True))

    def test_apercu_conserve_les_decisions(self):
        master_id = self._upload_initial([_software("Firefox"), _software("VLC")])
        _html_preview, token = self._preview_update(
            master_id, "27/11/2026 21:00", [_software("Firefox")]
        )

        preview_again = self.client.post(
            f"/master/{master_id}/update",
            data={
                "confirm_token": token,
                "preview_only": "1",
                "preview_limit": "25",
                "decision_0": "to_install",
            },
        )
        self.assertEqual(preview_again.status_code, 200)
        self.assertIn("value=\"to_install\" selected", preview_again.get_data(as_text=True))

        self._confirm(master_id, token)
        followups = self._followups(master_id)
        self.assertEqual(followups[0]["status"], "to_install")

    def test_un_scan_importe_resout_le_suivi(self):
        master_id = self._upload_initial([_software("Firefox"), _software("VLC")])
        self.client.post(
            "/upload",
            data={
                "file": (
                    io.BytesIO(_html(_payload("27/11/2026 21:00", [_software("Firefox")])).encode("utf-8")),
                    "scan-27.html",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(self._followups(master_id)[0]["status"], "pending")

        self.client.post(
            "/upload",
            data={
                "file": (
                    io.BytesIO(
                        _html(
                            _payload(
                                "28/11/2026 21:00",
                                [_software("Firefox"), _software("VLC")],
                            )
                        ).encode("utf-8")
                    ),
                    "scan-28.html",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(self._followups(master_id)[0]["status"], "resolved")

    def test_retrait_volontaire_conserve_la_decision(self):
        master_id = self._upload_initial([_software("Firefox"), _software("VLC")])
        _html_preview, token = self._preview_update(
            master_id, "27/11/2026 21:00", [_software("Firefox")]
        )
        self._confirm(master_id, token, decision_0="intentional")

        followups = self._followups(master_id)
        self.assertEqual(len(followups), 1)
        self.assertEqual(followups[0]["status"], "intentional")

        # Un nouveau scan toujours sans VLC ne doit pas créer un doublon.
        _html_preview, token = self._preview_update(
            master_id, "28/11/2026 21:00", [_software("Firefox")]
        )
        self._confirm(master_id, token)
        followups = self._followups(master_id)
        self.assertEqual(len(followups), 1)
        self.assertEqual(followups[0]["status"], "intentional")


if __name__ == "__main__":
    unittest.main()
