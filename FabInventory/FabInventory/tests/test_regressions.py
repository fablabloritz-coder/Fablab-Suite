"""Tests de regression du Lot 0 : corrections immediates sans migration.

Chaque test couvre un defaut constate pendant l'audit, pour eviter qu'il
revienne lors des lots suivants (snapshot courant, identite, securite).
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot0-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"
os.environ["ADMIN_PASSWORD"] = "regression-admin-password"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    _parse_scan_date_iso,
    _upsert_snapshot_software_index,
    app,
    get_db,
    init_db,
)


class Lot0RegressionTests(unittest.TestCase):
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

    def _seed_inventory(self, pc_name="PC-01", scan_date="20/11/2026 10:00", software=None):
        software = software or [
            {"n": "Firefox", "v": "128", "e": "Mozilla", "d": "20/11/2026", "s": 1, "src": "Registre", "cat": "main"},
        ]
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, label, workflow_type) VALUES (?, '', 'inventory')",
                (pc_name,),
            ).lastrowid
            snapshot_id = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?)",
                (master_id, scan_date, json.dumps(software), len(software)),
            ).lastrowid
            _upsert_snapshot_software_index(db, snapshot_id, master_id, software)
            db.commit()
            return master_id, snapshot_id

    def test_admin_masters_links_resolvent(self):
        master_id, snapshot_id = self._seed_inventory()
        with app.app_context():
            db = get_db()
            prep_id = db.execute(
                "INSERT INTO masters (pc_name, label, workflow_type) VALUES ('PREP-01', '', 'preparation')"
            ).lastrowid
            db.execute(
                "INSERT INTO roadmap_items (master_id, software_name) VALUES (?, 'VLC')",
                (prep_id,),
            )
            db.commit()

        html = self.client.get("/admin/masters").get_data(as_text=True)
        self.assertIn(f'href="/master/{master_id}"', html)
        self.assertIn(f'href="/snapshot/{snapshot_id}"', html)
        self.assertIn(f'href="/master/{prep_id}/roadmap"', html)
        self.assertNotIn("/masters/", html)
        self.assertNotIn("/snapshots/", html)
        self.assertNotIn("/roadmaps/", html)

        self.assertEqual(self.client.get(f"/master/{master_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/snapshot/{snapshot_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/master/{prep_id}/roadmap").status_code, 200)

    def test_export_ne_contient_que_le_dernier_scan(self):
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, workflow_type) VALUES ('PC-EXPORT', 'inventory')"
            ).lastrowid
            scans = [
                ("01/01/2020 10:00", [{"n": "Firefox", "v": "100", "e": "Mozilla", "s": 1, "src": "Registre"}]),
                ("20/11/2026 10:00", [{"n": "7-Zip", "v": "26", "e": "Igor Pavlov", "s": 1, "src": "Registre"}]),
            ]
            for scan_date, software in scans:
                snapshot_id = db.execute(
                    "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?)",
                    (master_id, scan_date, json.dumps(software), len(software)),
                ).lastrowid
                _upsert_snapshot_software_index(db, snapshot_id, master_id, software)
            db.commit()

        response = self.client.get(f"/master/{master_id}/export")
        self.assertEqual(response.status_code, 200)
        rows = response.data.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(len(rows), 2)  # en-tete + un logiciel
        self.assertIn("7-Zip", rows[1])
        self.assertNotIn("Firefox", "\n".join(rows))

    def test_tableau_master_aautant_de_colonnes_que_son_entete(self):
        master_id, _ = self._seed_inventory()
        html = self.client.get(f"/master/{master_id}").get_data(as_text=True)
        self.assertNotIn('colspan="2">Source', html)

    def test_badge_ancien_recent_utilise_une_vraie_date(self):
        self._seed_inventory(pc_name="PC-VIEUX", scan_date="01/01/2020 10:00")
        self._seed_inventory(pc_name="PC-RECENT", scan_date="28/11/2026 10:00")

        html = self.client.get("/admin/masters").get_data(as_text=True)
        self.assertEqual(html.count("Ancien"), 1)
        self.assertEqual(html.count("Récent"), 1)

    def test_patch_demande_sans_notes_ne_les_efface_pas(self):
        with app.app_context():
            db = get_db()
            logiciel_id = db.execute("INSERT INTO logiciels (nom) VALUES ('Firefox')").lastrowid
            prof_id = db.execute(
                "INSERT INTO prof_tokens (email, nom, token) VALUES ('p@example.test', 'Prof', 'tok')"
            ).lastrowid
            demande_id = db.execute(
                "INSERT INTO demandes (prof_token_id, logiciel_id, notes_internes) VALUES (?, ?, ?)",
                (prof_id, logiciel_id, "note importante"),
            ).lastrowid
            db.commit()

        response = self.client.patch(f"/api/admin/demandes/{demande_id}", json={"statut": "Validé"})
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            db = get_db()
            notes = db.execute(
                "SELECT notes_internes FROM demandes WHERE id = ?", (demande_id,)
            ).fetchone()["notes_internes"]
        self.assertEqual(notes, "note importante")

    def test_api_flag_valide_les_entrees(self):
        master_id, _ = self._seed_inventory()

        self.assertEqual(
            self.client.post("/api/flag", json={"master_id": master_id, "name": "Firefox", "is_licensed": "abc"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/flag", json={"master_id": master_id, "is_licensed": 0}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/flag", json={"master_id": 99999, "name": "Firefox"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/flag",
                json={"master_id": master_id, "name": "Firefox", "important": 1, "is_licensed": 1, "license_type": "reseau"},
            ).status_code,
            200,
        )

    def test_curseur_keyset_invalide_ne_renvoie_pas_500(self):
        for raw in (b'"abc"', json.dumps({"value": "x", "row_id": "abc"}).encode()):
            cursor = base64.urlsafe_b64encode(raw).decode("ascii")
            response = self.client.get(f"/search?q=firefox&paging=keyset&cursor={cursor}")
            self.assertEqual(response.status_code, 200)

    def test_api_software_borne_limit_et_offset(self):
        for query in ("limit=-1", "limit=501", "offset=-1"):
            self.assertEqual(self.client.get(f"/api/software?{query}").status_code, 400)
        self.assertEqual(self.client.get("/api/software?limit=500&offset=0").status_code, 200)

    def test_reindex_exige_une_session_admin(self):
        anonymous = app.test_client()
        response = anonymous.post("/admin/reindex-software")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/login"))

        response = self.client.post("/admin/reindex-software")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_compare_ne_propose_que_des_masters_inventories(self):
        self._seed_inventory(pc_name="PC-COMPARE")
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO masters (pc_name, workflow_type) VALUES ('PREP-COMPARE', 'preparation')"
            )
            db.commit()

        html = self.client.get("/compare").get_data(as_text=True)
        self.assertIn("Comparaison indisponible", html)

    def test_parse_scan_date_iso_accepte_les_formats_connus(self):
        self.assertEqual(_parse_scan_date_iso("23/03/2026 16:58"), "2026-03-23 16:58:00")
        self.assertEqual(_parse_scan_date_iso("23/03/2026"), "2026-03-23 00:00:00")
        self.assertEqual(_parse_scan_date_iso("2026-03-23 16:58:00"), "2026-03-23 16:58:00")
        self.assertEqual(_parse_scan_date_iso("pas une date"), "")

    def test_login_avec_cle_secrete_vide_est_explicite(self):
        original = app.secret_key
        app.secret_key = ""
        try:
            with self.assertRaises(RuntimeError):
                app.test_client().post(
                    "/admin/login",
                    data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]},
                )
        finally:
            app.secret_key = original


if __name__ == "__main__":
    unittest.main()
