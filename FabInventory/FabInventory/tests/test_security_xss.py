"""Tests du Lot 5 : protections des ecritures admin et rendu sans innerHTML brut."""
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot5-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import reset_temp_db  # noqa: E402
from app import app, init_db  # noqa: E402


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
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_user_id"] = 1

    def test_les_pages_admin_exigent_une_session(self):
        anonymous = app.test_client()
        for path in ("/admin/", "/admin/masters", "/admin/settings"):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertTrue(response.headers["Location"].endswith("/admin/login"))
        self.assertEqual(anonymous.post("/admin/logiciels/1/delete").status_code, 302)
        self.assertEqual(anonymous.patch("/api/admin/demandes/1", json={"statut": "Validé"}).status_code, 302)

    def test_alerte_mot_de_passe_par_defaut_puis_disparition(self):
        html = self.client.get("/admin/").get_data(as_text=True)
        self.assertIn("mot de passe par defaut", html)

        response = self.client.post(
            "/admin/settings",
            data={
                "old_password": "admin",
                "new_password": "secret123",
                "confirm_password": "secret123",
            },
        )
        self.assertEqual(response.status_code, 302)

        html = self.client.get("/admin/").get_data(as_text=True)
        self.assertNotIn("mot de passe par defaut", html)

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
