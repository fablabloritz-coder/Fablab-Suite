"""Tests du lot A/B : provenance, evolution entre scans et sante du parc."""
import io
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-insights-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402


def _payload(pc_name, scan_date, software, disks=None, network=None, services=None, warnings=None):
    return {
        "schema": 3,
        "script_version": "3.1.0",
        "generated_at": f"{scan_date.replace(' ', 'T')}:00",
        "pcName": pc_name,
        "date": scan_date,
        "system": {
            "os": "Microsoft Windows 11 Pro",
            "windows_version": "23H2 (build 22631.4169)",
            "cpu": "Intel Core i7-12700 (12 coeurs / 20 threads)",
            "ram": 16.0,
            "fabricant": "Dell Inc.",
            "num_serie": "SERIE-01",
            "domaine": "FABLAB",
        },
        "disks": disks or [],
        "network": network or [],
        "services": services or [],
        "software": software,
        "warnings": warnings or [],
    }


def _html(payload):
    body = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return f'<html><body><script id="inventoryData" type="application/json">{body}</script></body></html>'


class FleetInsightsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def _upload(self, payload, filename="inventaire.html"):
        return self.client.post(
            "/upload",
            data={"file": (io.BytesIO(_html(payload).encode("utf-8")), filename)},
            content_type="multipart/form-data",
        )

    def _master_id(self, pc_name):
        with app.app_context():
            db = get_db()
            return db.execute(
                "SELECT id FROM masters WHERE pc_name = ? AND workflow_type = 'inventory'",
                (pc_name,),
            ).fetchone()["id"]

    def test_provenance_et_avertissements_sont_stockes_et_affiches(self):
        payload = _payload(
            "PC-META",
            "26/11/2026 21:00",
            [{"n": "Firefox", "v": "128", "e": "Mozilla", "d": "-", "s": 0, "src": "Registre"}],
            warnings=["WMI Win32_BIOS indisponible : test"],
        )
        self.assertEqual(self._upload(payload).status_code, 302)

        master_id = self._master_id("PC-META")
        with app.app_context():
            db = get_db()
            snapshot = db.execute(
                "SELECT * FROM snapshots WHERE master_id = ? ORDER BY id DESC LIMIT 1",
                (master_id,),
            ).fetchone()
            self.assertEqual(snapshot["script_version"], "3.1.0")
            self.assertEqual(snapshot["scan_schema"], 3)
            self.assertIn("WMI Win32_BIOS", snapshot["warnings_json"])
            snapshot_id = snapshot["id"]

        page = self.client.get(f"/snapshot/{snapshot_id}").get_data(as_text=True)
        self.assertIn("Collecte partielle", page)
        self.assertIn("WMI Win32_BIOS", page)
        self.assertIn("Script 3.1.0", page)

    def test_evolution_compare_logiciels_disques_reseau_et_services(self):
        first = _payload(
            "PC-EVO",
            "26/11/2026 21:00",
            [{"n": "Firefox", "v": "128", "e": "Mozilla", "d": "-", "s": 0, "src": "Registre"}],
            disks=[{"label": "C:", "size_go": 500.0, "free_go": 100.0}],
            network=[{"name": "Ethernet", "ip": "192.168.1.20", "mac": "AA:BB:CC:DD:EE:01",
                      "dhcp": "Non", "gateway": "192.168.1.1", "dns": "1.1.1.1"}],
            services=[{"n": "Spouleur", "sc": "Spooler", "status": "Running", "start": "Auto"}],
        )
        second = _payload(
            "PC-EVO",
            "27/11/2026 21:00",
            [
                {"n": "Firefox", "v": "129", "e": "Mozilla", "d": "-", "s": 0, "src": "Registre"},
                {"n": "7-Zip", "v": "24.0", "e": "Igor Pavlov", "d": "-", "s": 2.0, "src": "Registre"},
            ],
            disks=[{"label": "C:", "size_go": 500.0, "free_go": 5.0}],
            network=[{"name": "Ethernet", "ip": "192.168.1.21", "mac": "AA:BB:CC:DD:EE:01",
                      "dhcp": "Oui", "gateway": "192.168.1.1", "dns": "1.1.1.1"}],
            services=[{"n": "Nouveau service", "sc": "NewSvc", "status": "Running", "start": "Auto"}],
            warnings=["Scan des dossiers limite"],
        )
        self.assertEqual(self._upload(first, "first.html").status_code, 302)
        self.assertEqual(self._upload(second, "second.html").status_code, 302)
        master_id = self._master_id("PC-EVO")

        page = self.client.get(f"/master/{master_id}/evolution")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Évolution de PC-EVO", html)
        self.assertIn("7-Zip", html)
        self.assertIn("Firefox", html)
        self.assertIn("seuil bas", html)
        self.assertIn("192.168.1.20", html)
        self.assertIn("192.168.1.21", html)
        self.assertIn("Spouleur", html)
        self.assertIn("Nouveau service", html)
        self.assertIn("Collecte partielle", html)

        other = _payload(
            "PC-OTHER",
            "27/11/2026 22:00",
            [{"n": "VLC", "v": "3.0", "e": "VideoLAN", "d": "-", "s": 0, "src": "Registre"}],
            network=[{"name": "Ethernet", "ip": "192.168.1.20", "mac": "AA:BB:CC:DD:EE:02",
                      "dhcp": "Oui", "gateway": "192.168.1.1", "dns": "1.1.1.1"}],
        )
        self.assertEqual(self._upload(other, "other.html").status_code, 302)

        health = self.client.get("/sante")
        self.assertEqual(health.status_code, 200)
        health_html = health.get_data(as_text=True)
        self.assertIn("Santé du parc", health_html)
        self.assertIn("disque bas", health_html)
        self.assertIn("IP dupliquées", health_html)

        registry = self.client.get("/reseau")
        self.assertEqual(registry.status_code, 200)
        registry_html = registry.get_data(as_text=True)
        self.assertIn("Registre réseau", registry_html)
        self.assertIn("192.168.1.20", registry_html)
        self.assertIn("doublon", registry_html)

    def test_ancien_rapport_reste_lisible_sans_metadonnees(self):
        payload = {
            "pcName": "PC-OLD",
            "date": "26/11/2026 21:00",
            "software": [{"n": "VLC", "v": "3.0", "e": "VideoLAN", "d": "-", "s": 0, "src": "Registre"}],
        }
        self.assertEqual(self._upload(payload, "old.html").status_code, 302)
        with app.app_context():
            db = get_db()
            snapshot = db.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(snapshot["warnings_json"], "[]")
            snapshot_id = snapshot["id"]
        page = self.client.get(f"/snapshot/{snapshot_id}")
        self.assertEqual(page.status_code, 200)


if __name__ == "__main__":
    unittest.main()
