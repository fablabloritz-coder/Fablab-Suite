import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class StockSurfaceConversionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-stock-tests-")

        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, "fabtrack_test.db")
        models.init_db()

        app_module.app.config.update(TESTING=True)
        # DB already initialized for tests, avoid running startup hooks on each request.
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _create_article(self, payload):
        response = self.client.post("/stock/api/articles", json=payload)
        self.assertEqual(response.status_code, 200, response.data)
        body = response.get_json()
        self.assertTrue(body.get("success"), body)
        return int(body["id"])

    def _get_article(self, article_id):
        response = self.client.get(f"/stock/api/articles/{article_id}")
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()

    def test_create_article_planche_is_converted_to_m2(self):
        article_id = self._create_article(
            {
                "nom": "Test Planche Conversion",
                "unite": "planche",
                "longueur_cm": 200,
                "largeur_cm": 100,
                "quantite_actuelle": 2,
                "quantite_minimum": 1,
                "quantite_maximum": 10,
            }
        )

        article = self._get_article(article_id)
        self.assertEqual(article["unite"], "m²")
        self.assertAlmostEqual(float(article["quantite_actuelle"]), 4.0, places=6)
        self.assertAlmostEqual(float(article["quantite_minimum"]), 2.0, places=6)
        self.assertAlmostEqual(float(article["quantite_maximum"]), 20.0, places=6)

    def test_update_thresholds_in_planches_when_unit_is_m2(self):
        article_id = self._create_article(
            {
                "nom": "Test Seuils M2",
                "unite": "m²",
                "longueur_cm": 150,
                "largeur_cm": 100,
                "quantite_actuelle": 12,
                "quantite_minimum": 3,
                "quantite_maximum": 9,
            }
        )

        update_response = self.client.put(
            f"/stock/api/articles/{article_id}",
            json={
                "nom": "Test Seuils M2",
                "unite": "m²",
                "longueur_cm": 150,
                "largeur_cm": 100,
                "quantite_minimum": 2,
                "quantite_maximum": 4,
                "threshold_unit_mode": "planches",
            },
        )
        self.assertEqual(update_response.status_code, 200, update_response.data)
        body = update_response.get_json()
        self.assertTrue(body.get("success"), body)

        article = self._get_article(article_id)
        # 150x100 cm = 1.5 m2 per board.
        self.assertEqual(article["unite"], "m²")
        self.assertAlmostEqual(float(article["quantite_minimum"]), 3.0, places=6)
        self.assertAlmostEqual(float(article["quantite_maximum"]), 6.0, places=6)

    def test_planche_without_dimensions_keeps_raw_values(self):
        article_id = self._create_article(
            {
                "nom": "Test Planche Sans Dimensions",
                "unite": "planche",
                "quantite_actuelle": 5,
                "quantite_minimum": 2,
                "quantite_maximum": 8,
            }
        )

        article = self._get_article(article_id)
        self.assertEqual(article["unite"], "planche")
        self.assertAlmostEqual(float(article["quantite_actuelle"]), 5.0, places=6)
        self.assertAlmostEqual(float(article["quantite_minimum"]), 2.0, places=6)
        self.assertAlmostEqual(float(article["quantite_maximum"]), 8.0, places=6)


if __name__ == "__main__":
    unittest.main()
