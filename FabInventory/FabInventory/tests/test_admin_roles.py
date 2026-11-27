"""Tests du rôle technicien (suite 8) : matrice des permissions.

Un technicien se connecte comme un administrateur mais ne garde que
l'inventaire : lectures publiques du parc et écritures (import, master, scan,
flag, roadmap, absences). Tout l'espace /admin — catalogue, paramètres,
professeurs, demandes, journal — lui répond 403.
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-roles-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from werkzeug.security import generate_password_hash  # noqa: E402

from tests.support import reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402

INVENTAIRE_GET = [
    "/",
    "/upload",
    "/roadmaps",
    "/roadmap/minimal-pack",
    "/compare",
    "/sante",
    "/rapports",
    "/search?q=firefox",
]

ADMIN_GET = [
    "/admin/",
    "/admin/masters",
    "/admin/demandes",
    "/admin/logiciels",
    "/admin/profs",
    "/admin/wiki",
    "/admin/settings",
    "/admin/journal",
    "/admin/export-pdf",
]

# Routes /admin ouvertes au technicien (inventaire) et à tout le monde.
ADMIN_PUBLIC_ENDPOINTS = {
    "admin_login",
    "admin_logout",
    "admin_trash",
    "admin_trash_restore",
}


def _admin_rules(methods):
    """Règles /admin acceptant l'une des méthodes, hors endpoints ouverts."""
    rules = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/admin/"):
            continue
        if rule.endpoint in ADMIN_PUBLIC_ENDPOINTS:
            continue
        accepted = sorted(rule.methods & set(methods))
        if accepted:
            rules.append((rule, accepted))
    return rules


def _url(rule):
    """URL concrète : chaque paramètre reçoit 1, le décorateur passe avant la vue."""
    return re.sub(r"<[^>]+>", "1", rule.rule)


def _inventory_html(pc_name="PC-TECH"):
    payload = {
        "pcName": pc_name,
        "date": "27/11/2026 10:00",
        "software": [
            {
                "n": "Firefox",
                "v": "129",
                "e": "",
                "d": "27/11/2026 10:00",
                "s": 1,
                "src": "Registre",
            }
        ],
    }
    return (
        '<html><body><span class="label">OS</span><span>Windows 11</span>'
        '<script id="inventoryData" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


class AdminRolesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        with app.app_context():
            reset_temp_db(init_db)
            db = get_db()
            for username, password, role in (
                ("admin-test", "admin-pass", "admin"),
                ("tech-test", "tech-pass", "technicien"),
            ):
                db.execute(
                    "INSERT INTO admin_users (username, password_hash, role) "
                    "VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), role),
                )
            db.commit()
        self.client = app.test_client()

    def _login(self, username, password):
        response = self.client.post(
            "/admin/login",
            data={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302)
        return response

    def _login_technicien(self):
        return self._login("tech-test", "tech-pass")

    def _login_admin(self):
        return self._login("admin-test", "admin-pass")

    def _master_id(self, pc_name="PC-TECH"):
        with app.app_context():
            row = get_db().execute(
                "SELECT id FROM masters WHERE pc_name = ?", (pc_name,)
            ).fetchone()
        return row["id"] if row else None

    # ── connexion ────────────────────────────────────────────────────────────

    def test_connexion_redirige_selon_le_role(self):
        self.assertEqual(self._login_admin().headers["Location"], "/admin/")
        self.client.get("/admin/logout")
        self.assertEqual(self._login_technicien().headers["Location"], "/")

    def test_role_inconnu_retombe_sur_technicien(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO admin_users (username, password_hash, role) "
                "VALUES ('tech-bizarre', ?, 'superviseur')",
                (generate_password_hash("tech-pass"),),
            )
            db.commit()
        self.assertEqual(self._login("tech-bizarre", "tech-pass").headers["Location"], "/")
        self.assertEqual(self.client.get("/admin/logiciels").status_code, 403)

    # ── technicien : inventaire autorisé ─────────────────────────────────────

    def test_technicien_lit_linventaire(self):
        self._login_technicien()
        for path in INVENTAIRE_GET:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_technicien_ecrit_dans_linventaire(self):
        self._login_technicien()

        self.assertEqual(
            self.client.post("/master/new", data={"pc_name": "PC-NOUVEAU"}).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/upload",
                data={"file": (io.BytesIO(_inventory_html().encode("utf-8")), "scan.html")},
                content_type="multipart/form-data",
            ).status_code,
            302,
        )

        master_id = self._master_id()
        self.assertIsNotNone(self._master_id("PC-NOUVEAU"))

        flag = self.client.post(
            "/api/flag",
            json={"master_id": master_id, "name": "Firefox", "important": 1},
        )
        self.assertEqual(flag.status_code, 200)
        self.assertEqual(flag.get_json(), {"ok": True})

        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute(
                    "SELECT is_important FROM software_flags "
                    "WHERE master_id = ? AND software_name = 'Firefox'",
                    (master_id,),
                ).fetchone()["is_important"],
                1,
            )
            # L'écriture est journalisée sous le nom du technicien.
            self.assertEqual(
                db.execute(
                    "SELECT username FROM audit_log WHERE action = 'flag' "
                    "ORDER BY id DESC"
                ).fetchone()["username"],
                "tech-test",
            )

    # ── technicien : corbeille restaurable, purge réservée ─────────────

    def _supprimer_master(self, pc_name):
        """Crée un master, le supprime et retourne l'archive correspondante."""
        self.assertEqual(
            self.client.post("/master/new", data={"pc_name": pc_name}).status_code,
            302,
        )
        master_id = self._master_id(pc_name)
        self.assertEqual(
            self.client.post(f"/master/{master_id}/delete").status_code, 302
        )
        self.assertIsNone(self._master_id(pc_name))
        with app.app_context():
            return get_db().execute(
                "SELECT id FROM deleted_items WHERE restored_at IS NULL "
                "ORDER BY id DESC"
            ).fetchone()["id"]

    def test_technicien_accede_a_la_corbeille(self):
        self._login_technicien()
        self._supprimer_master("PC-CORBEILLE")

        response = self.client.get("/admin/trash")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("PC-CORBEILLE", body)
        self.assertIn("Restaurer", body)
        # La purge définitive reste hors de portée du technicien.
        self.assertNotIn("Purger", body)
        self.assertIn("réservée aux administrateurs", body)

    def test_technicien_restaure_un_master_supprime(self):
        self._login_technicien()
        item_id = self._supprimer_master("PC-RESTAURE")

        response = self.client.post(f"/admin/trash/{item_id}/restore")
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(self._master_id("PC-RESTAURE"))

    def test_technicien_ne_purge_pas_la_corbeille(self):
        self._login_technicien()
        self._supprimer_master("PC-GARDEE")

        self.assertEqual(self.client.post("/admin/trash/purge").status_code, 403)
        with app.app_context():
            self.assertEqual(
                get_db().execute(
                    "SELECT COUNT(*) AS c FROM deleted_items"
                ).fetchone()["c"],
                1,
            )

    def test_admin_purge_la_corbeille(self):
        self._login_admin()
        self._supprimer_master("PC-PURGEE")
        with app.app_context():
            db = get_db()
            db.execute("UPDATE deleted_items SET restored_at = CURRENT_TIMESTAMP")
            db.commit()

        self.assertIn(
            "Purger", self.client.get("/admin/trash").get_data(as_text=True)
        )
        self.assertEqual(self.client.post("/admin/trash/purge").status_code, 302)
        with app.app_context():
            self.assertEqual(
                get_db().execute(
                    "SELECT COUNT(*) AS c FROM deleted_items"
                ).fetchone()["c"],
                0,
            )

    # ── technicien : espace admin refusé ─────────────────────────────────────

    def test_technicien_ne_lit_pas_les_pages_admin(self):
        self._login_technicien()
        rules = _admin_rules({"GET"})
        # Toutes les pages /admin sauf la corbeille, la connexion et la sortie.
        self.assertGreaterEqual(len(rules), 8)
        for rule, _ in rules:
            with self.subTest(endpoint=rule.endpoint):
                response = self.client.get(_url(rule))
                self.assertEqual(response.status_code, 403)
                self.assertIn("Accès refusé", response.get_data(as_text=True))

    def test_technicien_n_ecrit_pas_dans_l_admin(self):
        """Toute écriture /admin — catalogue, paramètres, professeurs — est refusée."""
        self._login_technicien()
        rules = _admin_rules({"POST", "PUT", "PATCH", "DELETE"})
        self.assertGreaterEqual(len(rules), 10)
        for rule, methods in rules:
            for method in methods:
                with self.subTest(endpoint=rule.endpoint, method=method):
                    response = self.client.open(_url(rule), method=method, data={})
                    self.assertEqual(response.status_code, 403)

        # L'API des demandes est elle aussi réservée à l'administrateur.
        api = self.client.patch("/api/admin/demandes/1", json={"statut": "Validé"})
        self.assertEqual(api.status_code, 403)
        self.assertEqual(
            api.get_json()["error"], "Action réservée aux administrateurs"
        )
        details = self.client.get("/api/admin/demandes/1/details")
        self.assertEqual(details.status_code, 403)

    # ── administrateur : non-régression ──────────────────────────────────────

    def test_admin_garde_tous_ses_acces(self):
        self._login_admin()
        for path in ADMIN_GET:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        response = self.client.post(
            "/admin/logiciels/add",
            data={"nom": "Outil-Test", "categorie": "Test"},
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertIsNotNone(
                get_db().execute(
                    "SELECT id FROM logiciels WHERE nom = 'Outil-Test'"
                ).fetchone()
            )

    def test_admin_peut_toujours_ecrire_dans_linventaire(self):
        self._login_admin()
        self.assertEqual(
            self.client.post("/master/new", data={"pc_name": "PC-ADMIN"}).status_code,
            302,
        )
        self.assertIsNotNone(self._master_id("PC-ADMIN"))

    def test_session_sans_role_reste_administrateur(self):
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_user_id"] = 1
        self.assertEqual(self.client.get("/admin/logiciels").status_code, 200)

    # ── anonyme ──────────────────────────────────────────────────────────────

    def test_anonyme_est_renvoye_vers_la_connexion(self):
        response = self.client.get("/admin/logiciels")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

        response = self.client.post("/master/new", data={"pc_name": "PC-ANON"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])
        self.assertIsNone(self._master_id("PC-ANON"))


if __name__ == "__main__":
    unittest.main()
