"""Tests du Lot 5 : protections des ecritures admin et rendu sans innerHTML brut."""
import os
import sys
import tempfile
import unittest
from unittest import mock

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot5-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"
os.environ["ADMIN_PASSWORD"] = "security-test-password"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    _admin_initial_password_path,
    _load_secret_key,
    app,
    get_db,
    init_db,
)


def _template(name):
    with open(os.path.join(_APP_DIR, "templates", name), encoding="utf-8") as handle:
        return handle.read()


class SecurityXssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)

    def test_les_pages_admin_exigent_une_session(self):
        anonymous = app.test_client()
        for path in ("/admin/", "/admin/masters", "/admin/settings"):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertTrue(response.headers["Location"].endswith("/admin/login"))
        self.assertEqual(anonymous.post("/admin/logiciels/1/delete").status_code, 302)
        self.assertEqual(
            anonymous.patch("/api/admin/demandes/1", json={"statut": "Validé"}).status_code,
            401,
        )

    def test_mot_de_passe_admin_sans_valeur_par_defaut(self):
        html = self.client.get("/admin/").get_data(as_text=True)
        self.assertNotIn("mot de passe par defaut", html)

        anonymous = app.test_client()
        env_password = os.environ["ADMIN_PASSWORD"]
        self.assertEqual(
            anonymous.post(
                "/admin/login", data={"username": "admin", "password": "admin"}
            ).status_code,
            200,
        )
        self.assertEqual(anonymous.get("/admin/").status_code, 302)
        self.assertEqual(
            anonymous.post(
                "/admin/login", data={"username": "admin", "password": env_password}
            ).status_code,
            302,
        )

        change = anonymous.post(
            "/admin/settings",
            data={
                "old_password": env_password,
                "new_password": "secret123",
                "confirm_password": "secret123",
            },
        )
        self.assertEqual(change.status_code, 302)

        anonymous.get("/admin/logout")
        self.assertEqual(
            anonymous.post(
                "/admin/login", data={"username": "admin", "password": env_password}
            ).status_code,
            200,
        )
        self.assertEqual(
            anonymous.post(
                "/admin/login", data={"username": "admin", "password": "secret123"}
            ).status_code,
            302,
        )

    def test_seed_genere_un_mot_de_passe_admin_aleatoire(self):
        with mock.patch.dict(os.environ, {"ADMIN_PASSWORD": ""}):
            with app.app_context():
                db = get_db()
                db.execute("DELETE FROM admin_users")
                db.execute("DELETE FROM app_meta WHERE key = 'initial_seed'")
                db.commit()
                init_db()

        chemin = _admin_initial_password_path()
        self.assertTrue(os.path.exists(chemin))
        self.assertEqual(os.stat(chemin).st_mode & 0o777, 0o600)
        with open(chemin, encoding="utf-8") as handle:
            generated = handle.read().strip()
        self.assertGreaterEqual(len(generated), 16)
        self.assertNotEqual(generated, "admin")

        anonymous = app.test_client()
        self.assertEqual(
            anonymous.post(
                "/admin/login", data={"username": "admin", "password": "admin"}
            ).status_code,
            200,
        )
        self.assertEqual(
            anonymous.post(
                "/admin/login", data={"username": "admin", "password": generated}
            ).status_code,
            302,
        )

    def test_cle_de_session_aleatoire_est_persistee(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "change-me-in-production"}):
            first = _load_secret_key()
            second = _load_secret_key()
        self.assertEqual(len(first), 64)
        self.assertEqual(first, second)

        with mock.patch.dict(
            os.environ, {"SECRET_KEY": "une-cle-fournie-par-environnement"}
        ):
            self.assertEqual(_load_secret_key(), "une-cle-fournie-par-environnement")

    def test_les_ecritures_refusent_les_visiteurs_anonymes(self):
        anonymous = app.test_client()
        page_routes = [
            ("/upload", {}),
            ("/master/new", {"pc_name": "PC-ANON"}),
            ("/master/1/edit", {"pc_name": "PC-ANON"}),
            ("/master/1/update", {}),
            ("/master/1/delete", {}),
            ("/master/1/absences", {"followup_id": "1", "status": "intentional"}),
            ("/master/1/roadmap/add", {"software_name": "Anon"}),
            ("/master/1/roadmap/apply-minimal-pack", {}),
            ("/roadmap/minimal-pack/add", {"software_name": "Anon"}),
            ("/roadmap/minimal-pack/1/update", {"software_name": "Anon"}),
            ("/roadmap/minimal-pack/1/delete", {}),
            ("/snapshot/1/delete", {}),
            ("/admin/logiciels/1/delete", {}),
        ]
        for path, data in page_routes:
            with self.subTest(path=path):
                response = anonymous.post(path, data=data)
                self.assertEqual(response.status_code, 302, path)
                self.assertTrue(
                    response.headers["Location"].endswith("/admin/login"), path
                )

        with app.app_context():
            db = get_db()
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"], 0
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM roadmap_minimal_pack").fetchone()["c"],
                0,
            )

        for method, path, payload in (
            ("post", "/api/flag", {"master_id": 1, "name": "Firefox"}),
            ("patch", "/api/admin/demandes/1", {"statut": "Validé"}),
        ):
            with self.subTest(path=path):
                response = getattr(anonymous, method)(path, json=payload)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(
                    response.get_json()["error"], "Authentification admin requise"
                )

    def test_les_post_de_lecture_et_le_portail_prof_restent_publics(self):
        anonymous = app.test_client()

        comparison = anonymous.post("/api/compare", json={})
        self.assertNotEqual(comparison.status_code, 401)

        prof = anonymous.post("/api/prof/token-inconnu/demandes", json={})
        self.assertEqual(prof.status_code, 403)
        self.assertEqual(prof.get_json()["error"], "Token invalide")

    def test_confirmations_ne_mettent_pas_de_donnees_dans_le_js(self):
        for name in (
            "master.html",
            "minimal_pack.html",
            "roadmap_master.html",
            "admin/logiciels.html",
        ):
            source = _template(name)
            with self.subTest(template=name):
                self.assertNotRegex(source, r"onsubmit=\"[^\"]*\{\{")
                self.assertIn("confirm(this.dataset.confirm)", source)

    def test_confirmation_master_echappe_le_nom_du_poste(self):
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES (?, ?, 'inventory')",
                ("');alert(1);//", "');alert(1);//"),
            ).lastrowid
            db.commit()

        html = self.client.get(f"/master/{master_id}").get_data(as_text=True)
        self.assertIn("onsubmit=\"return confirm(this.dataset.confirm)\"", html)
        self.assertIn("&#39;);alert(1);//", html)
        self.assertNotIn("confirm('Supprimer le master", html)

    def test_master_echappe_date_source_et_categorie(self):
        source = _template("master.html")
        self.assertIn("+esc(s.d)+", source)
        self.assertIn("+esc(s.src)+", source)
        self.assertIn("+esc(swCategory)+", source)
        self.assertIn("data-action=\"important\"", source)
        self.assertNotIn("onchange=\"flagSw", source)
        self.assertNotIn("onchange=\"noteSw", source)

    def test_compare_echappe_les_libelles_des_cartes(self):
        source = _template("compare.html")
        self.assertIn("esc(String(value))", source)
        self.assertIn("esc(label)", source)

    def test_guide_et_catalogue_echappent_les_donnees_utilisateur(self):
        self.assertIn("escHtml(item.titre", _template("prof/guide.html"))
        self.assertIn("escHtml(item.solution", _template("prof/guide.html"))
        self.assertIn("escHtml(p.nom)", _template("prof/catalogue.html"))
        self.assertIn("escHtml(f.name)", _template("upload.html"))


if __name__ == "__main__":
    unittest.main()
