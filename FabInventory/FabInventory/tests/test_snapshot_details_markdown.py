"""Tests du Lot 7 : disques/reseau importes et guide Markdown rendu sans XSS."""
import io
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot7-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import app, extract_disks_network, get_db, init_db  # noqa: E402


def _html_with_details():
    payload = {
        "pcName": "PC-DETAILS",
        "date": "28/11/2026 10:00",
        "software": [{"n": "Firefox", "v": "128", "e": "Mozilla", "src": "Registre"}],
    }
    return (
        "<html><body>"
        '<span class="label">OS</span><span>Windows 11</span>'
        "<h2>Disques</h2><table>"
        "<tr><th>Lettre</th><th>Taille</th><th>Libre</th><th>Utilise</th></tr>"
        "<tr><td>C:</td><td>500 Go</td><td>125 Go</td><td>375 Go</td></tr>"
        "</table>"
        "<h2>Réseau</h2><table>"
        "<tr><th>Carte</th><th>IP</th><th>MAC</th><th>DHCP</th></tr>"
        "<tr><td>Intel Ethernet</td><td>192.168.1.20</td><td>AA:BB:CC:DD:EE:FF</td><td>Oui</td></tr>"
        "</table>"
        '<script id="inventoryData" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></body></html>"
    )


class SnapshotDetailsMarkdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def test_extract_disks_network(self):
        disks, network = extract_disks_network(_html_with_details())
        self.assertEqual(disks, [{"label": "C:", "size_go": 500.0, "free_go": 125.0}])
        self.assertEqual(
            network,
            [{"name": "Intel Ethernet", "ip": "192.168.1.20", "mac": "AA:BB:CC:DD:EE:FF"}],
        )

    def test_upload_stocke_et_affiche_disques_reseau(self):
        response = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(_html_with_details().encode("utf-8")), "poste.html")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            db = get_db()
            snapshot_id = db.execute("SELECT id FROM snapshots").fetchone()["id"]
            disks = db.execute("SELECT disks_json FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()["disks_json"]
            network = db.execute("SELECT network_json FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()["network_json"]
        self.assertEqual(json.loads(disks)[0]["label"], "C:")
        self.assertEqual(json.loads(network)[0]["ip"], "192.168.1.20")

        html = self.client.get(f"/snapshot/{snapshot_id}").get_data(as_text=True)
        self.assertIn("Disques", html)
        self.assertIn("C:", html)
        self.assertIn("Intel Ethernet", html)
        self.assertIn("192.168.1.20", html)

    def test_guide_markdown_est_rendu_et_filtre(self):
        with app.app_context():
            db = get_db()
            prof_id = db.execute(
                "INSERT INTO prof_tokens (email, nom, token) VALUES ('g@example.test', 'G', 'tok-guide')"
            ).lastrowid
            logiciel_id = db.execute("INSERT INTO logiciels (nom) VALUES ('GuideApp')").lastrowid
            db.execute(
                "INSERT INTO logiciel_guides (logiciel_id, guide_markdown) VALUES (?, ?)",
                (logiciel_id, "# Guide\n\n**Important**\n\n<script>alert(1)</script>"),
            )
            db.commit()
            self.assertTrue(prof_id)

        html = self.client.get(f"/prof/tok-guide/guide/{logiciel_id}").get_data(as_text=True)
        self.assertIn("marked.parse", html)
        self.assertIn("DOMPurify.sanitize", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)


if __name__ == "__main__":
    unittest.main()
