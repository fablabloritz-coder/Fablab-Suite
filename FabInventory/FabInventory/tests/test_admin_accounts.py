"""Tests de la gestion des comptes (suite 8) : création, mot de passe, désactivation.

La page `/admin/comptes` est réservée aux administrateurs et ne touche qu'aux
comptes techniciens : les comptes administrateurs changent leur mot de passe via
`/admin/settings`. La page de connexion reste inchangée : un compte désactivé
est refusé comme un mauvais mot de passe.
"""
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-accounts-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from werkzeug.security import generate_password_hash  # noqa: E402

from tests.support import reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402

ADMIN_USER = "admin-test"
ADMIN_PASSWORD = "admin-pass"


class AdminAccountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        with app.app_context():
            reset_temp_db(init_db)
            db = get_db()
            db.execute(
                "INSERT INTO admin_users (username, password_hash, role) "
                "VALUES (?, ?, 'admin')",
                (ADMIN_USER, generate_password_hash(ADMIN_PASSWORD)),
            )
            db.commit()
        self.client = app.test_client()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _login(self, username, password):
        return self.client.post(
            "/admin/login", data={"username": username, "password": password}
        )

    def _login_admin(self):
        response = self._login(ADMIN_USER, ADMIN_PASSWORD)
        self.assertEqual(response.headers["Location"], "/admin/")

    def _logout(self):
        self.client.get("/admin/logout")

    def _create(self, username="tech-1", password="tech-pass"):
        response = self.client.post(
            "/admin/comptes/add",
            data={
                "username": username,
                "password": password,
                "confirm_password": password,
            },
        )
        self.assertEqual(response.status_code, 302)
        return username

    def _account(self, username):
        with app.app_context():
            return get_db().execute(
                "SELECT id, role, is_active FROM admin_users WHERE username = ?",
                (username,),
            ).fetchone()

    def _session(self):
        with self.client.session_transaction() as session:
            return dict(session)

    # ── création ─────────────────────────────────────────────────────────────

    def test_admin_cree_un_technicien_qui_peut_se_connecter(self):
        self._login_admin()
        self._create()

        account = self._account("tech-1")
        self.assertEqual(account["role"], "technicien")
        self.assertEqual(account["is_active"], 1)

        self._logout()
        response = self._login("tech-1", "tech-pass")
        self.assertEqual(response.headers["Location"], "/")
        self.assertTrue(self._session().get("admin_logged_in"))

    def test_creation_refusee_si_champs_invalides(self):
        self._login_admin()
        for data in (
            {"username": "", "password": "motdepasse", "confirm_password": "motdepasse"},
            {"username": "tech-2", "password": "court", "confirm_password": "court"},
            {"username": "tech-2", "password": "motdepasse", "confirm_password": "autre"},
        ):
            self.assertEqual(
                self.client.post("/admin/comptes/add", data=data).status_code, 302
            )
        self.assertIsNone(self._account("tech-2"))

        self._create("tech-3", "motdepasse")
        self.client.post(
            "/admin/comptes/add",
            data={
                "username": "tech-3",
                "password": "motdepasse",
                "confirm_password": "motdepasse",
            },
        )
        with app.app_context():
            self.assertEqual(
                get_db().execute(
                    "SELECT COUNT(*) AS c FROM admin_users WHERE username = 'tech-3'"
                ).fetchone()["c"],
                1,
            )

    # ── mot de passe ─────────────────────────────────────────────────────────

    def test_mot_de_passe_reinitialise(self):
        self._login_admin()
        self._create()
        account = self._account("tech-1")
        endpoint = f"/admin/comptes/{account['id']}/password"

        # Un mot de passe trop court est refusé et ne change rien.
        self.client.post(
            endpoint, data={"password": "court", "confirm_password": "court"}
        )
        self._logout()
        self.assertEqual(
            self._login("tech-1", "tech-pass").headers["Location"], "/"
        )

        self._logout()
        self._login_admin()
        response = self.client.post(
            endpoint,
            data={"password": "nouveau-pass", "confirm_password": "nouveau-pass"},
        )
        self.assertEqual(response.status_code, 302)

        self._logout()
        self.assertEqual(self._login("tech-1", "tech-pass").status_code, 200)
        self.assertFalse(self._session().get("admin_logged_in"))
        self.assertEqual(
            self._login("tech-1", "nouveau-pass").headers["Location"], "/"
        )

    # ── désactivation ────────────────────────────────────────────────────────

    def test_desactivation_bloque_la_connexion_puis_reactivation(self):
        self._login_admin()
        self._create()
        account = self._account("tech-1")

        response = self.client.post(f"/admin/comptes/{account['id']}/toggle")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._account("tech-1")["is_active"], 0)

        self._logout()
        self.assertEqual(self._login("tech-1", "tech-pass").status_code, 200)
        self.assertFalse(self._session().get("admin_logged_in"))

        self._login_admin()
        self.client.post(f"/admin/comptes/{account['id']}/toggle")
        self.assertEqual(self._account("tech-1")["is_active"], 1)
        self._logout()
        self.assertEqual(
            self._login("tech-1", "tech-pass").headers["Location"], "/"
        )

    def test_admin_ne_se_desactive_pas_et_ne_se_reinitialise_pas_ici(self):
        self._login_admin()
        admin = self._account(ADMIN_USER)

        self.client.post(f"/admin/comptes/{admin['id']}/toggle")
        self.client.post(
            f"/admin/comptes/{admin['id']}/password",
            data={"password": "pirate-pass", "confirm_password": "pirate-pass"},
        )
        self.assertEqual(self._account(ADMIN_USER)["is_active"], 1)
        # Une action refusée n'écrit rien dans le journal non plus.
        with app.app_context():
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"],
                0,
            )

        self._logout()
        self.assertEqual(self._login(ADMIN_USER, "pirate-pass").status_code, 200)
        self.assertEqual(
            self._login(ADMIN_USER, ADMIN_PASSWORD).headers["Location"], "/admin/"
        )

    def test_compte_au_role_inconnu_reste_revocable(self):
        """Un rôle vide vaut technicien à la connexion : il doit être révocable."""
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO admin_users (username, password_hash, role) "
                "VALUES (?, ?, '')",
                ("tech-legacy", generate_password_hash("legacy-pass")),
            )
            db.commit()

        self._login_admin()
        account = self._account("tech-legacy")
        self.client.post(f"/admin/comptes/{account['id']}/toggle")
        self.assertEqual(self._account("tech-legacy")["is_active"], 0)

        self._logout()
        self.assertEqual(self._login("tech-legacy", "legacy-pass").status_code, 200)

    # ── matrice des rôles ────────────────────────────────────────────────────

    def test_technicien_ne_gere_pas_les_comptes(self):
        self._login_admin()
        self._create()
        account = self._account("tech-1")
        self._logout()
        self._login("tech-1", "tech-pass")

        self.assertEqual(self.client.get("/admin/comptes").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/admin/comptes/add",
                data={
                    "username": "tech-x",
                    "password": "motdepasse",
                    "confirm_password": "motdepasse",
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/admin/comptes/{account['id']}/password",
                data={"password": "autre-pass", "confirm_password": "autre-pass"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/admin/comptes/{account['id']}/toggle").status_code,
            403,
        )
        self.assertIsNone(self._account("tech-x"))
        self.assertEqual(self._account("tech-1")["is_active"], 1)

    # ── page et journal ──────────────────────────────────────────────────────

    def test_page_comptes_liste_les_roles_et_les_actions(self):
        self._login_admin()
        self._create()

        body = self.client.get("/admin/comptes").get_data(as_text=True)
        self.assertIn(ADMIN_USER, body)
        self.assertIn("tech-1", body)
        self.assertIn("Administrateur", body)
        self.assertIn("Technicien", body)
        self.assertIn("Désactiver", body)
        self.assertIn("Mot de passe via", body)

    def test_actions_journalisees_sans_mot_de_passe(self):
        self._login_admin()
        self._create()
        account = self._account("tech-1")
        self.client.post(
            f"/admin/comptes/{account['id']}/password",
            data={"password": "nouveau-pass", "confirm_password": "nouveau-pass"},
        )
        self.client.post(f"/admin/comptes/{account['id']}/toggle")

        with app.app_context():
            rows = get_db().execute(
                "SELECT action, target_label, summary, username FROM audit_log "
                "ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [row["action"] for row in rows],
            ["account_create", "account_password", "account_toggle"],
        )
        self.assertTrue(all(row["username"] == ADMIN_USER for row in rows))
        summaries = " ".join(row["summary"] for row in rows)
        self.assertNotIn("tech-pass", summaries)
        self.assertNotIn("nouveau-pass", summaries)


if __name__ == "__main__":
    unittest.main()
