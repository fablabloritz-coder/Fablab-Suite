"""Contrat du script d'inventaire : JSON enrichi, repli HTML et schema stocke."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot6-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    _snapshot_content_hash,
    app,
    extract_disks_network,
    extract_services,
    extract_system_info,
    get_db,
    init_db,
    parse_inventory_html,
)

SCRIPT_PATH = _APP_DIR / "static" / "downloads" / "inventaire_master.ps1"


def _payload(include_enriched=True):
    payload = {
        "schema": 3,
        "script_version": "3.1.0",
        "pcName": "PC-SCRIPT",
        "date": "26/11/2026 21:00",
        "software": [
            {"n": 'Guillemet " & <tag>', "v": "1.0", "e": "Atelier", "d": "12/03/2024", "s": 12.5, "src": "Registre"},
            {"n": "Fin </script> test", "v": "2.0", "e": "Test", "d": "-", "s": 0, "src": "Dossier"},
        ],
    }
    if include_enriched:
        payload.update({
            "system": {
                "os": "Microsoft Windows 11 Pro",
                "windows_version": "23H2 (build 22631.4169)",
                "cpu": "Intel Core i7-12700 (12 coeurs / 20 threads)",
                "ram": 15.9,
                "fabricant": "Dell Inc.",
                "num_serie": "SERIE-01",
                "domaine": "FABLAB",
            },
            "disks": [
                {"label": "C:", "volume": "Systeme", "size_go": 500.0, "free_go": 125.0, "used_go": 375.0}
            ],
            "network": [
                {"name": "Intel Ethernet", "ip": "192.168.1.20", "mac": "AA:BB:CC:DD:EE:FF",
                 "dhcp": "Non", "gateway": "192.168.1.1", "dns": "1.1.1.1, 8.8.8.8"}
            ],
            "services": [
                {"n": "Spouleur d'impression", "sc": "Spooler", "status": "Running", "start": "Auto"}
            ],
        })
    return payload


def _html(payload, include_legacy=False):
    json_text = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    legacy = ""
    if include_legacy:
        legacy = (
            '<span class="label">OS</span><span>Windows 10</span>'
            '<span class="label">CPU</span><span>Ancien CPU</span>'
            '<span class="label">RAM</span><span>8 Go</span>'
            "<h2>Disques</h2><table>"
            "<tr><th>Lettre</th><th>Taille</th><th>Libre</th></tr>"
            "<tr><td>D:</td><td>1000 Go</td><td>400 Go</td></tr>"
            "</table><h2>Reseau</h2><table>"
            "<tr><th>Carte</th><th>IP</th><th>MAC</th></tr>"
            "<tr><td>Ancienne carte</td><td>10.0.0.2</td><td>11:22:33:44:55:66</td></tr>"
            "</table>"
        )
    return (
        "<html><body>" + legacy
        + '<script id="inventoryData" type="application/json">'
        + json_text
        + "</script></body></html>"
    )


class InventoryScriptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)

    def test_json_enrichi_prime_sur_le_html_historique(self):
        payload = _payload()
        html = _html(payload, include_legacy=True)

        sys_info = extract_system_info(html, payload)
        disks, network = extract_disks_network(html, payload)
        services = extract_services(payload)

        self.assertEqual(sys_info["os"], "Microsoft Windows 11 Pro")
        self.assertEqual(sys_info["ram"], 15.9)
        self.assertEqual(disks, [{"label": "C:", "size_go": 500.0, "free_go": 125.0}])
        self.assertEqual(network[0]["name"], "Intel Ethernet")
        self.assertEqual(network[0]["gateway"], "192.168.1.1")
        self.assertEqual(services[0]["sc"], "Spooler")

    def test_ancien_format_reste_importable(self):
        payload = _payload(include_enriched=False)
        html = _html(payload, include_legacy=True)

        sys_info = extract_system_info(html, payload)
        disks, network = extract_disks_network(html, payload)

        self.assertEqual(sys_info["os"], "Windows 10")
        self.assertEqual(sys_info["cpu"], "Ancien CPU")
        self.assertEqual(sys_info["ram"], 8.0)
        self.assertEqual(disks, [{"label": "D:", "size_go": 1000.0, "free_go": 400.0}])
        self.assertEqual(network[0]["ip"], "10.0.0.2")
        self.assertEqual(extract_services(payload), [])

    def test_upload_stocke_systeme_disques_reseau_et_services(self):
        payload = _payload()
        response = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(_html(payload).encode("utf-8")), "poste.html")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            db = get_db()
            snapshot = db.execute("SELECT * FROM snapshots").fetchone()
            self.assertEqual(snapshot["services_json"], json.dumps(payload["services"], ensure_ascii=False))
            self.assertEqual(json.loads(snapshot["disks_json"])[0]["label"], "C:")
            self.assertEqual(json.loads(snapshot["network_json"])[0]["gateway"], "192.168.1.1")
            self.assertEqual(json.loads(snapshot["network_json"])[0]["dns"], "1.1.1.1, 8.8.8.8")
            self.assertIn("Windows 11 Pro", snapshot["os_info"])
            snapshot_id = snapshot["id"]

        html = self.client.get(f"/snapshot/{snapshot_id}").get_data(as_text=True)
        self.assertIn("Services automatiques", html)
        self.assertIn("Spouleur", html)
        self.assertIn("192.168.1.1", html)
        self.assertIn("1.1.1.1, 8.8.8.8", html)

    def test_json_avec_balise_fermante_est_analyse_sans_casser_le_block(self):
        payload = _payload(include_enriched=False)
        html = _html(payload)
        parsed = parse_inventory_html(html)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["software"][1]["n"], "Fin </script> test")

    def test_empreinte_reste_compatible_avec_les_anciens_rapports(self):
        common = ("PC-SCRIPT", "26/11/2026 21:00", [{"n": "Firefox"}], {"os": "Windows 11"}, [], [])
        self.assertEqual(
            _snapshot_content_hash(*common),
            _snapshot_content_hash(*common, services=[]),
        )
        self.assertNotEqual(
            _snapshot_content_hash(*common, services=[{"n": "Spooler"}]),
            _snapshot_content_hash(*common, services=[]),
        )

    def test_mise_a_jour_master_utilise_le_meme_chemin_snapshot(self):
        initial = _payload()
        self.assertEqual(
            self.client.post(
                "/upload",
                data={"file": (io.BytesIO(_html(initial).encode("utf-8")), "initial.html")},
                content_type="multipart/form-data",
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            master_id = db.execute("SELECT id FROM masters").fetchone()["id"]

        updated = _payload()
        updated["date"] = "27/11/2026 22:00"
        updated["software"] = [{"n": "7-Zip", "v": "24.0", "e": "Igor Pavlov", "d": "-", "s": 2.0, "src": "Registre"}]
        preview = self.client.post(
            f"/master/{master_id}/update",
            data={
                "file": (io.BytesIO(_html(updated).encode("utf-8")), "updated.html"),
                "preview_limit": "10",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        token = re.search(r'name="confirm_token" value="([^"]+)"', preview.get_data(as_text=True))
        self.assertIsNotNone(token)

        confirm = self.client.post(
            f"/master/{master_id}/update",
            data={"confirm_token": token.group(1), "preview_limit": "10"},
        )
        self.assertEqual(confirm.status_code, 302)

        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 2)
            latest = db.execute(
                "SELECT services_json FROM snapshots ORDER BY scan_date_iso DESC, id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(json.loads(latest["services_json"])[0]["sc"], "Spooler")

    def test_script_utilise_une_serialisation_json_unique(self):
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("ConvertTo-Json -InputObject $Payload -Depth 12 -Compress", script)
        self.assertIn("-SelfTest", script)
        self.assertIn("services", script)
        self.assertIn("schema        = 3", script)
        self.assertNotIn("$jsonSw", script)
        self.assertIn("Get-CimInstance @params", script)
        self.assertNotIn("Get-CimInstance -ClassName $ClassName", script)

        # Garde-fou de compatibilite Windows PowerShell 5.1 : ces formes ne
        # doivent pas apparaitre dans le script livre aux postes.
        for unsupported in ("??", "?.", "ForEach-Object -Parallel", "ConvertFrom-Json -AsHashtable"):
            self.assertNotIn(unsupported, script)

        mirror = _APP_DIR.parent / "usb_v3" / "inventaire.ps1"
        if mirror.is_file():
            self.assertEqual(mirror.read_bytes(), SCRIPT_PATH.read_bytes(), "le miroir usb_v3 a diverge")

    def test_parseur_powershell_et_autotest_si_disponible(self):
        pwsh = os.environ.get("FABINVENTORY_PWSH") or shutil.which("pwsh")
        if not pwsh:
            self.skipTest("PowerShell absent : validation Windows a faire sur un poste")
        parse = subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-Command",
                (
                    "$tokens=$null; $errors=$null; "
                    f"[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT_PATH}', [ref]$tokens, [ref]$errors) | Out-Null; "
                    "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(parse.returncode, 0, parse.stdout + parse.stderr)

        output = Path(_MODULE_TMP) / "selftest.html"
        run = subprocess.run(
            [pwsh, "-NoLogo", "-NoProfile", "-File", str(SCRIPT_PATH), "-SelfTest", "-NoGui", "-OutputPath", str(output)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(output.is_file())
        csv_path = output.with_suffix(".csv")
        self.assertTrue(csv_path.is_file())
        csv_bytes = csv_path.read_bytes()
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"), "le CSV doit avoir un BOM UTF-8")
        csv_text = csv_bytes.decode("utf-8-sig")
        self.assertTrue(csv_text.startswith('"pc_name";"scan_date";"name";"version";"editor";"install_date";"size_mb";"source";"category"'))
        parsed = parse_inventory_html(output.read_text(encoding="utf-8"))
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["services"]), 2)
        self.assertGreaterEqual(len(parsed["software"]), 3)


if __name__ == "__main__":
    unittest.main()
