"""Tests de la version du script d'inventaire (suite 3, partie applicative).

Verifie que la constante applicative reste alignee sur le script servi, que la
comparaison de versions est tolerante aux anciens formats, et que les badges et
l'empreinte SHA256 sont affiches sans modifier l'import existant.
"""
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-script-version-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"
os.environ["ADMIN_PASSWORD"] = "script-version-password"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    CURRENT_SCRIPT_VERSION,
    _is_old_script_version,
    app,
    get_db,
    init_db,
)

SCRIPT_PATH = Path(_APP_DIR) / "static" / "downloads" / "inventaire_master.ps1"


def _payload(pc_name, script_version):
    return {
        "schema": 3,
        "script_version": script_version,
        "generated_at": "2026-11-26T21:00:00",
        "pcName": pc_name,
        "date": "26/11/2026 21:00",
        "system": {"os": "Windows 11", "cpu": "CPU test", "ram": 16.0},
        "disks": [],
        "network": [],
        "services": [],
        "software": [
            {
                "n": "Firefox",
                "v": "128.0",
                "e": "Mozilla",
                "d": "-",
                "s": 1.0,
                "src": "Registre",
            }
        ],
        "warnings": [],
    }


def _html(payload):
    body = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return (
        '<html><body><script id="inventoryData" type="application/json">'
        f"{body}</script></body></html>"
    )


class ScriptVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)

    def _upload(self, pc_name, script_version):
        response = self.client.post(
            "/upload",
            data={
                "file": (
                    io.BytesIO(_html(_payload(pc_name, script_version)).encode("utf-8")),
                    "scan.html",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            row = get_db().execute(
                "SELECT id FROM masters WHERE pc_name = ?", (pc_name,)
            ).fetchone()
            return row["id"] if row else None

    def test_la_constante_correspond_au_script_servi(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        match = re.search(r'\$script:ScriptVersion\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(match, "version introuvable dans le script")
        self.assertEqual(match.group(1), CURRENT_SCRIPT_VERSION)

    def test_comparaison_des_versions(self):
        for value in ("3.0.9", "3.0", "2.9.9", "", None, "inconnue"):
            self.assertTrue(_is_old_script_version(value), value)
        for value in ("3.1.0", "3.1", "v3.1.0", "3.1.1", "3.2.0", "4.0"):
            self.assertFalse(_is_old_script_version(value), value)

    def test_fiche_master_signale_un_script_ancien(self):
        old_id = self._upload("PC-SCRIPT-ANCIEN", "3.0.9")
        current_id = self._upload("PC-SCRIPT-COURANT", "3.1.0")
        missing_id = self._upload("PC-SCRIPT-SANS-VERSION", "")

        old_page = self.client.get(f"/master/{old_id}").get_data(as_text=True)
        current_page = self.client.get(f"/master/{current_id}").get_data(as_text=True)
        missing_page = self.client.get(f"/master/{missing_id}").get_data(as_text=True)

        self.assertIn("Script 3.0.9", old_page)
        self.assertIn("scan avec un script ancien", old_page)
        self.assertNotIn("scan avec un script ancien", current_page)
        self.assertIn("scan avec un script ancien", missing_page)

    def test_sante_signale_les_scans_avec_un_script_ancien(self):
        self._upload("PC-SCRIPT-ANCIEN", "3.0.9")
        self._upload("PC-SCRIPT-COURANT", "3.1.0")
        self._upload("PC-SCRIPT-SANS-VERSION", "")

        # Un master sans scan ne doit ni compter ni porter le badge.
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES (?, ?, 'inventory')",
                ("PC-SANS-SCAN", "pc-sans-scan"),
            )
            db.commit()

        page = self.client.get("/sante").get_data(as_text=True)
        self.assertIn(
            "2 poste(s) scanné(s) avec un script antérieur à la version", page
        )
        self.assertIn("scan avec un script ancien", page)

    def test_page_de_telechargement_affiche_le_sha256_du_script(self):
        expected = hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()

        page = self.client.get("/").get_data(as_text=True)
        self.assertIn(expected, page)
        self.assertIn(f"Script v{CURRENT_SCRIPT_VERSION}", page)

        download = self.client.get("/download/master-script")
        self.assertEqual(download.status_code, 200)
        try:
            self.assertEqual(hashlib.sha256(download.data).hexdigest(), expected)
        finally:
            download.close()


if __name__ == "__main__":
    unittest.main()
