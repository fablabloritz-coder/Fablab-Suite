"""Smoke test des routes et parcours critiques.

Filet de securite rapide : chaque route critique doit repondre, et chaque
parcours structurant doit aller jusqu'au bout. Les regles de detail
(deduplication, decisions d'absence, validation des entrees, rendu) restent
dans leurs modules dedies ; ce module ne les duplique pas.
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-smoke-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    APP_VERSION,
    _invalidate_counts_cache,
    app,
    get_db,
    init_db,
)


def _software(name, version="1.0"):
    return {"n": name, "v": version, "e": "Editeur test", "d": "-", "s": 1.0, "src": "Registre"}


def _payload(pc_name="PC-SMOKE", scan_date="26/11/2026 21:00", software=None):
    return {
        "schema": 3,
        "script_version": "3.1.0",
        "generated_at": "2026-11-26T21:00:00",
        "pcName": pc_name,
        "date": scan_date,
        "system": {
            "os": "Microsoft Windows 11 Pro",
            "cpu": "CPU test",
            "ram": 16.0,
            "num_serie": "SERIE-SMOKE",
        },
        "disks": [],
        "network": [],
        "services": [],
        "software": software if software is not None else [_software("Firefox")],
        "warnings": [],
    }


def _html(payload):
    body = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return f'<html><body><script id="inventoryData" type="application/json">{body}</script></body></html>'


class SmokeAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
            _invalidate_counts_cache()
        # Les parcours critiques sont ceux d'un technicien connecte.
        self._login()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _login(self, client=None):
        client = client or self.client
        response = client.post(
            "/admin/login",
            data={"username": "admin", "password": "admin"},
        )
        self.assertEqual(response.status_code, 302)
        return client

    def _upload(self, payload, filename="scan.html"):
        return self.client.post(
            "/upload",
            data={"file": (io.BytesIO(_html(payload).encode("utf-8")), filename)},
            content_type="multipart/form-data",
        )

    def _master_id(self, pc_name):
        with app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT id FROM masters WHERE pc_name = ?",
                (pc_name,),
            ).fetchone()
            return row["id"] if row else None

    def _snapshots(self, master_id):
        with app.app_context():
            db = get_db()
            return db.execute(
                "SELECT id FROM snapshots WHERE master_id = ? ORDER BY id",
                (master_id,),
            ).fetchall()

    def _count(self, table, column, value):
        with app.app_context():
            db = get_db()
            return db.execute(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ?",
                (value,),
            ).fetchone()["c"]

    def _seed_inventories(self):
        self.assertEqual(
            self._upload(
                _payload(
                    "PC-ALPHA",
                    software=[_software("Firefox", "128.0"), _software("7-Zip")],
                )
            ).status_code,
            302,
        )
        self.assertEqual(
            self._upload(
                _payload("PC-BETA", software=[_software("VLC", "3.0")])
            ).status_code,
            302,
        )

    def _seed_prep(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO roadmap_minimal_pack (software_name) VALUES ('VLC')"
            )
            prep_id = db.execute(
                "INSERT INTO masters (pc_name, label, workflow_type) "
                "VALUES ('PREP-SMOKE', 'Salle test', 'preparation')"
            ).lastrowid
            db.execute(
                "INSERT INTO roadmap_items (master_id, software_name) "
                "VALUES (?, 'Firefox')",
                (prep_id,),
            )
            db.commit()
            return prep_id

    def _roadmap_item_id(self, master_id, name):
        with app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT id FROM roadmap_items WHERE master_id = ? AND software_name = ?",
                (master_id, name),
            ).fetchone()
            return row["id"] if row else None

    # ── Routes de lecture et API ─────────────────────────────────────────────

    def test_routes_de_lecture_et_api(self):
        self._seed_inventories()
        prep_id = self._seed_prep()
        alpha = self._master_id("PC-ALPHA")
        snapshot_id = self._snapshots(alpha)[0]["id"]

        routes = [
            "/",
            "/sante",
            "/reseau",
            "/upload",
            "/master/new",
            "/roadmaps",
            f"/master/{alpha}",
            f"/master/{alpha}/absences",
            f"/master/{alpha}/evolution",
            f"/master/{alpha}/export",
            f"/master/{alpha}/update",
            f"/master/{prep_id}/roadmap",
            f"/master/{prep_id}/roadmap/print",
            "/roadmap/minimal-pack",
            f"/snapshot/{snapshot_id}",
            "/compare",
            "/search?q=firefox",
            "/healthz",
            "/api/category-stats",
            "/api/software",
            "/api/fabsuite/manifest",
            "/api/fabsuite/health",
            "/api/fabsuite/status",
            "/api/fabsuite/widget/masters-count",
            "/api/fabsuite/widget/snapshots-count",
            "/api/fabsuite/widget/inventory-overview",
            "/api/fabsuite/notifications",
            "/admin/",
            "/admin/masters",
            "/admin/demandes",
            "/admin/logiciels",
            "/admin/profs",
            "/admin/wiki",
            "/admin/export-pdf",
            "/admin/settings",
        ]
        for path in routes:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)

        self.assertIn("PC-ALPHA", self.client.get("/").get_data(as_text=True))
        self.assertIn(
            "Firefox",
            self.client.get(f"/master/{alpha}").get_data(as_text=True),
        )

        export = self.client.get(f"/master/{alpha}/export")
        self.assertEqual(export.mimetype, "text/csv")
        self.assertIn("Firefox", export.get_data(as_text=True))

        self.assertIn(
            "Firefox",
            self.client.get("/search?q=firefox").get_data(as_text=True),
        )

        manifest = self.client.get("/api/fabsuite/manifest").get_json()
        self.assertEqual(manifest["app"], "fabinventory")
        self.assertEqual(manifest["version"], APP_VERSION)
        self.assertEqual(
            self.client.get("/api/fabsuite/health").get_json()["status"],
            "ok",
        )
        self.assertEqual(self.client.options("/api/fabsuite/anything").status_code, 204)

        # Téléchargements du compagnon PowerShell.
        script = self.client.get("/download/master-script")
        self.assertEqual(script.status_code, 200)
        self.assertIn(b"inventaire", script.data.lower())
        script.close()

        launcher = self.client.get("/download/master-launcher")
        self.assertEqual(launcher.status_code, 200)
        launcher.close()

        bundle = self.client.get("/download/master-bundle")
        self.assertEqual(bundle.status_code, 200)
        self.assertEqual(bundle.mimetype, "application/zip")
        with zipfile.ZipFile(io.BytesIO(bundle.data)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"inventaire_master.ps1", "lancer_inventaire_master.bat"},
            )
        bundle.close()

    def test_connexion_admin_et_protection(self):
        anonymous = app.test_client()
        response = anonymous.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/admin/login"))

        self._login(anonymous)
        self.assertEqual(anonymous.get("/admin/").status_code, 200)

        self.assertEqual(anonymous.get("/admin/logout").status_code, 302)
        self.assertEqual(anonymous.get("/admin/").status_code, 302)

    # ── Import et mise à jour ────────────────────────────────────────────────

    def test_import_et_doublon(self):
        first = _payload("PC-IMPORT")
        self.assertEqual(self._upload(first).status_code, 302)
        self.client.get("/")  # consomme le message de succes

        master_id = self._master_id("PC-IMPORT")
        self.assertEqual(len(self._snapshots(master_id)), 1)

        self.assertEqual(self._upload(first, "doublon.html").status_code, 302)
        self.assertIn("identique", self.client.get("/").get_data(as_text=True))
        self.assertEqual(len(self._snapshots(master_id)), 1)

    def test_mise_a_jour_apercu_et_confirmation(self):
        self.assertEqual(
            self._upload(
                _payload(
                    "PC-UPDATE",
                    software=[_software("Firefox", "128.0"), _software("7-Zip")],
                )
            ).status_code,
            302,
        )
        master_id = self._master_id("PC-UPDATE")

        updated = _payload(
            "PC-UPDATE",
            scan_date="27/11/2026 21:00",
            software=[_software("Firefox", "129.0")],
        )
        preview = self.client.post(
            f"/master/{master_id}/update",
            data={
                "file": (io.BytesIO(_html(updated).encode("utf-8")), "update.html"),
                "preview_limit": "10",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        preview_html = preview.get_data(as_text=True)
        self.assertIn("7-Zip", preview_html)
        token = re.search(r'name="confirm_token" value="([^"]+)"', preview_html)
        self.assertIsNotNone(token)

        confirm = self.client.post(
            f"/master/{master_id}/update",
            data={"confirm_token": token.group(1)},
        )
        self.assertEqual(confirm.status_code, 302)
        self.assertEqual(len(self._snapshots(master_id)), 2)

    # ── Flags, roadmap et pack minimal ───────────────────────────────────────

    def test_flags_roadmap_et_pack_minimal(self):
        self.assertEqual(self._upload(_payload("PC-FLAGS")).status_code, 302)
        master_id = self._master_id("PC-FLAGS")

        self.assertEqual(
            self.client.post(
                "/api/flag",
                json={
                    "master_id": master_id,
                    "name": "Firefox",
                    "important": 1,
                    "note": "Licence site",
                },
            ).status_code,
            200,
        )
        self.assertIn(
            "Licence site",
            self.client.get(f"/master/{master_id}").get_data(as_text=True),
        )

        prep_id = self._seed_prep()
        self.assertEqual(
            self.client.post(
                f"/master/{prep_id}/roadmap/add",
                data={"software_name": "ExtraApp"},
            ).status_code,
            302,
        )
        extra_id = self._roadmap_item_id(prep_id, "ExtraApp")
        self.assertIsNotNone(extra_id)

        self.assertEqual(
            self.client.post(
                f"/master/{prep_id}/roadmap/{extra_id}/update",
                data={"software_name": "ExtraApp-2"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(f"/master/{prep_id}/roadmap/{extra_id}/toggle").status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            item = db.execute(
                "SELECT software_name, is_done FROM roadmap_items WHERE id = ?",
                (extra_id,),
            ).fetchone()
        self.assertEqual(item["software_name"], "ExtraApp-2")
        self.assertEqual(item["is_done"], 1)

        self.assertEqual(
            self.client.post(f"/master/{prep_id}/roadmap/{extra_id}/delete").status_code,
            302,
        )
        self.assertEqual(self._count("roadmap_items", "id", extra_id), 0)

        self.assertEqual(
            self.client.post(
                "/roadmap/minimal-pack/add",
                data={"software_name": "GlobalApp"},
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            global_id = db.execute(
                "SELECT id FROM roadmap_minimal_pack WHERE software_name = 'GlobalApp'"
            ).fetchone()["id"]

        self.assertEqual(
            self.client.post(
                f"/roadmap/minimal-pack/{global_id}/update",
                data={"software_name": "GlobalApp-2"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(f"/roadmap/minimal-pack/{global_id}/delete").status_code,
            302,
        )
        self.assertEqual(self._count("roadmap_minimal_pack", "id", global_id), 0)

    # ── Édition et suppressions ──────────────────────────────────────────────

    def test_edition_et_suppressions_en_cascade(self):
        self.assertEqual(self._upload(_payload("PC-EDIT")).status_code, 302)
        edit_id = self._master_id("PC-EDIT")
        self.assertEqual(
            self.client.post(
                f"/master/{edit_id}/edit",
                data={"pc_name": "PC-EDIT-2", "label": "Salle B", "notes": "Notes"},
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            edited = db.execute(
                "SELECT pc_name, label, notes FROM masters WHERE id = ?",
                (edit_id,),
            ).fetchone()
        self.assertEqual(
            (edited["pc_name"], edited["label"], edited["notes"]),
            ("PC-EDIT-2", "Salle B", "Notes"),
        )

        self.assertEqual(self._upload(_payload("PC-SNAP")).status_code, 302)
        self.assertEqual(
            self._upload(
                _payload(
                    "PC-SNAP",
                    scan_date="27/11/2026 21:00",
                    software=[_software("Firefox", "129.0")],
                )
            ).status_code,
            302,
        )
        snap_id = self._master_id("PC-SNAP")
        snaps = self._snapshots(snap_id)
        self.assertEqual(len(snaps), 2)
        self.assertEqual(
            self.client.post(f"/snapshot/{snaps[0]['id']}/delete").status_code,
            302,
        )
        self.assertEqual(len(self._snapshots(snap_id)), 1)

        self.assertEqual(self._upload(_payload("PC-DELETE")).status_code, 302)
        delete_id = self._master_id("PC-DELETE")
        self.assertEqual(
            self.client.post(
                "/api/flag",
                json={"master_id": delete_id, "name": "Firefox", "important": 1},
            ).status_code,
            200,
        )
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO software_followups (master_id, software_key, software_name) "
                "VALUES (?, 'firefox', 'Firefox')",
                (delete_id,),
            )
            db.commit()

        self.assertEqual(self.client.post(f"/master/{delete_id}/delete").status_code, 302)
        checks = (
            ("masters", "id"),
            ("snapshots", "master_id"),
            ("software_index", "master_id"),
            ("software_flags", "master_id"),
            ("software_followups", "master_id"),
        )
        for table, column in checks:
            with self.subTest(table=table):
                self.assertEqual(self._count(table, column, delete_id), 0)

    # ── Recherche, export et comparaison ─────────────────────────────────────

    def test_recherche_export_et_comparaison(self):
        self._seed_inventories()
        alpha = self._master_id("PC-ALPHA")
        beta = self._master_id("PC-BETA")
        snap_alpha = self._snapshots(alpha)[0]["id"]
        snap_beta = self._snapshots(beta)[0]["id"]

        search = self.client.get("/search?q=firefox")
        self.assertEqual(search.status_code, 200)
        self.assertIn("Firefox", search.get_data(as_text=True))

        exported = self.client.get("/search?q=firefox&export=csv")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.mimetype, "text/csv")
        self.assertIn("Firefox", exported.get_data(as_text=True))

        comparison = self.client.post(
            "/api/compare",
            json={"a": snap_alpha, "b": snap_beta},
        )
        self.assertEqual(comparison.status_code, 200)
        names = {row["name"] for row in comparison.get_json()["results"]}
        self.assertIn("Firefox", names)
        self.assertIn("VLC", names)

    # ── Portail professeur et gestion des demandes ──────────────────────────

    def test_portail_prof_et_demandes(self):
        with app.app_context():
            db = get_db()
            logiciel_id = db.execute(
                "INSERT INTO logiciels (nom, categorie) VALUES ('ProfApp', 'Test')"
            ).lastrowid
            version_id = db.execute(
                "INSERT INTO logiciel_versions (logiciel_id, numero_version) VALUES (?, '2.0')",
                (logiciel_id,),
            ).lastrowid
            plugin_id = db.execute(
                "INSERT INTO logiciel_plugins (logiciel_id, nom) VALUES (?, 'Option Prof')",
                (logiciel_id,),
            ).lastrowid
            db.execute(
                "INSERT INTO prof_tokens (email, nom, token) "
                "VALUES ('prof@example.test', 'Prof Test', 'prof-token')"
            )
            db.commit()

        catalogue = self.client.get("/prof/prof-token")
        self.assertEqual(catalogue.status_code, 200)
        self.assertIn("ProfApp", catalogue.get_data(as_text=True))
        self.assertEqual(
            self.client.get(f"/prof/prof-token/guide/{logiciel_id}").status_code,
            200,
        )
        self.assertEqual(self.client.get("/prof/token-inconnu").status_code, 403)

        demande = self.client.post(
            "/api/prof/prof-token/demandes",
            json={
                "logiciel_id": logiciel_id,
                "version_id": version_id,
                "plugin_ids": [plugin_id],
            },
        )
        self.assertEqual(demande.status_code, 200)
        demande_id = demande.get_json()["demande_id"]

        details = self.client.get(f"/api/admin/demandes/{demande_id}/details")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.get_json()["logiciel_nom"], "ProfApp")

        self.assertEqual(
            self.client.patch(
                f"/api/admin/demandes/{demande_id}",
                json={"statut": "Validé"},
            ).status_code,
            200,
        )
        self.assertIn(
            "ProfApp",
            self.client.get("/admin/demandes").get_data(as_text=True),
        )
        self.assertIn(
            "ProfApp",
            self.client.get(
                "/admin/export-pdf",
                query_string={"statut": "Validé"},
            ).get_data(as_text=True),
        )

        self.assertEqual(
            self.client.post(f"/api/prof/prof-token/demandes/{demande_id}/cancel").status_code,
            200,
        )
        self.assertEqual(self._count("demandes", "id", demande_id), 0)

    # ── CRUD admin ───────────────────────────────────────────────────────────

    def test_crud_admin_logiciels_profs_et_wiki(self):
        self.assertEqual(
            self.client.post(
                "/admin/logiciels/add",
                data={"nom": "SmokeCRUD", "categorie": "Test"},
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            logiciel_id = db.execute(
                "SELECT id FROM logiciels WHERE nom = 'SmokeCRUD'"
            ).fetchone()["id"]
        self.assertIn(
            "SmokeCRUD",
            self.client.get("/admin/logiciels").get_data(as_text=True),
        )

        self.assertEqual(
            self.client.post(
                f"/admin/logiciels/{logiciel_id}/versions/add",
                data={"numero_version": "3.1"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/logiciels/{logiciel_id}/plugins/add",
                data={"nom": "Plugin CRUD"},
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            version_id = db.execute(
                "SELECT id FROM logiciel_versions WHERE logiciel_id = ?",
                (logiciel_id,),
            ).fetchone()["id"]
            plugin_id = db.execute(
                "SELECT id FROM logiciel_plugins WHERE logiciel_id = ?",
                (logiciel_id,),
            ).fetchone()["id"]

        self.assertEqual(
            self.client.post(
                f"/admin/wiki/{logiciel_id}/save",
                data={
                    "guide_markdown": "# Guide Smoke",
                    "comptes_info": "Compte test",
                    "troubleshooting_json": "[]",
                },
            ).status_code,
            302,
        )
        self.assertIn(
            "Guide Smoke",
            self.client.get(f"/admin/wiki?logiciel_id={logiciel_id}").get_data(as_text=True),
        )

        self.assertEqual(
            self.client.post(
                f"/admin/logiciels/{logiciel_id}/edit",
                data={"nom": "SmokeCRUD-2", "categorie": "Test", "actif": "1"},
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT nom FROM logiciels WHERE id = ?",
                    (logiciel_id,),
                ).fetchone()["nom"],
                "SmokeCRUD-2",
            )

        self.assertEqual(
            self.client.post(
                f"/admin/logiciels/{logiciel_id}/versions/{version_id}/delete"
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/logiciels/{logiciel_id}/plugins/{plugin_id}/delete"
            ).status_code,
            302,
        )

        self.assertEqual(
            self.client.post(
                "/admin/profs/add",
                data={"email": "crud@example.test", "nom": "Prof CRUD"},
            ).status_code,
            302,
        )
        with app.app_context():
            db = get_db()
            prof = db.execute(
                "SELECT id, token FROM prof_tokens WHERE email = 'crud@example.test'"
            ).fetchone()
            prof_id, old_token = prof["id"], prof["token"]
        self.assertIn(
            "Prof CRUD",
            self.client.get("/admin/profs").get_data(as_text=True),
        )

        self.assertEqual(self.client.post(f"/admin/profs/{prof_id}/revoke").status_code, 302)
        self.assertEqual(self.client.post(f"/admin/profs/{prof_id}/activate").status_code, 302)
        self.assertEqual(self.client.post(f"/admin/profs/{prof_id}/regen").status_code, 302)
        with app.app_context():
            db = get_db()
            regenerated = db.execute(
                "SELECT token, actif FROM prof_tokens WHERE id = ?",
                (prof_id,),
            ).fetchone()
        self.assertNotEqual(regenerated["token"], old_token)
        self.assertEqual(regenerated["actif"], 1)

        self.assertEqual(
            self.client.post(f"/admin/logiciels/{logiciel_id}/delete").status_code,
            302,
        )
        self.assertEqual(self._count("logiciels", "id", logiciel_id), 0)
        for table in ("logiciel_versions", "logiciel_plugins", "logiciel_guides"):
            with self.subTest(table=table):
                self.assertEqual(self._count(table, "logiciel_id", logiciel_id), 0)


if __name__ == "__main__":
    unittest.main()
