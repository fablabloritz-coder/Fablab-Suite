"""Tests FabHome : accueil, profils et endpoints de base.

La base SQLite est redirigee vers un dossier temporaire via FABHOME_DB /
FABHOME_DATA avant l'import de `models` puis de `app`.
"""

import os
import sys
import tempfile
import unittest

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


class FabHomeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="fabhome-tests-")
        os.environ["FABHOME_DB"] = os.path.join(cls._tmpdir, "fabhome_test.db")
        os.environ["FABHOME_DATA"] = cls._tmpdir

        import models as fabhome_models

        fabhome_models.DB_PATH = os.environ["FABHOME_DB"]
        fabhome_models.init_db()
        cls.models = fabhome_models

        import app as app_module

        app_module.app.config["TESTING"] = True
        cls.client = app_module.app.test_client()

    def test_accueil_repond(self):
        reponse = self.client.get("/")
        self.assertEqual(reponse.status_code, 200)

    def test_profils_liste_et_profil_courant(self):
        reponse = self.client.get("/api/profiles")
        self.assertEqual(reponse.status_code, 200)

        donnees = reponse.get_json()
        self.assertIn("profiles", donnees)
        self.assertIn("current", donnees)
        self.assertGreaterEqual(len(donnees["profiles"]), 1)

    def test_creation_renommage_suppression_profil(self):
        creation = self.client.post("/api/profiles", json={"name": "Atelier"})
        self.assertEqual(creation.status_code, 201)
        profil_id = creation.get_json()["id"]

        noms = [p["name"] for p in self.client.get("/api/profiles").get_json()["profiles"]]
        self.assertIn("Atelier", noms)

        renommage = self.client.put(f"/api/profiles/{profil_id}", json={"name": "Atelier 2"})
        self.assertEqual(renommage.status_code, 200)
        self.assertTrue(renommage.get_json()["ok"])

        suppression = self.client.delete(f"/api/profiles/{profil_id}")
        self.assertEqual(suppression.status_code, 200)

    def test_creation_profil_sans_nom_refusee(self):
        reponse = self.client.post("/api/profiles", json={"name": "   "})
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("error", reponse.get_json())

    def test_suppression_du_profil_par_defaut_refusee(self):
        reponse = self.client.delete("/api/profiles/1")
        self.assertEqual(reponse.status_code, 400)

    def test_sante_de_l_application(self):
        reponse = self.client.get("/api/health")
        self.assertEqual(reponse.status_code, 200)

    def test_manifeste_fabsuite(self):
        reponse = self.client.get("/api/fabsuite/manifest")
        self.assertEqual(reponse.status_code, 200)

        manifeste = reponse.get_json()
        self.assertEqual(manifeste.get("app"), "fabhome")
        self.assertIn("version", manifeste)


if __name__ == "__main__":
    unittest.main()
