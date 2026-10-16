import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class MissionStepsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-steps-tests-")
        models.DATA_DIR = cls._tmpdir
        models.DB_PATH = os.path.join(cls._tmpdir, "fabtrack_test.db")
        models.init_db()
        app_module.app.config.update(TESTING=True)
        app_module._db_initialized = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        models.DATA_DIR = cls._orig_data_dir
        models.DB_PATH = cls._orig_db_path
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _mission(self):
        r = self.client.post("/missions/api/create", json={"titre": "M", "statut": "en_cours"})
        self.assertEqual(r.status_code, 201, r.data)
        return int(r.get_json()["data"]["id"])

    def test_steps_crud_and_last_step_in_list(self):
        mid = self._mission()
        r = self.client.post(f"/missions/api/{mid}/steps", json={"texte": "Pièce commandée", "created_at": "2025-01-02T09:30"})
        self.assertEqual(r.status_code, 201, r.data)
        first = r.get_json()["data"]
        self.assertEqual(first["created_at"], "2025-01-02 09:30:00")

        r = self.client.post(f"/missions/api/{mid}/steps", json={"texte": "Pièce reçue"})
        self.assertEqual(r.status_code, 201, r.data)
        second = r.get_json()["data"]
        self.assertTrue(second["created_at"])

        steps = self.client.get(f"/missions/api/{mid}/steps").get_json()["data"]
        self.assertEqual([s["texte"] for s in steps], ["Pièce commandée", "Pièce reçue"])

        mission = next(m for m in self.client.get("/missions/api/list").get_json()["data"] if m["id"] == mid)
        self.assertEqual(mission["derniere_etape_texte"], "Pièce reçue")
        self.assertEqual(mission["nb_etapes"], 2)

        r = self.client.put(f"/missions/api/steps/{first['id']}", json={"texte": "Commande passée", "created_at": "2099-01-01T08:00"})
        self.assertEqual(r.status_code, 200, r.data)
        mission = next(m for m in self.client.get("/missions/api/list").get_json()["data"] if m["id"] == mid)
        self.assertEqual(mission["derniere_etape_texte"], "Commande passée")

        r = self.client.delete(f"/missions/api/steps/{first['id']}")
        self.assertEqual(r.status_code, 200)
        steps = self.client.get(f"/missions/api/{mid}/steps").get_json()["data"]
        self.assertEqual(len(steps), 1)

    def test_validation_and_cascade(self):
        mid = self._mission()
        self.assertEqual(self.client.post(f"/missions/api/{mid}/steps", json={"texte": "  "}).status_code, 400)
        self.assertEqual(self.client.post(f"/missions/api/{mid}/steps", json={"texte": "x", "created_at": "nope"}).status_code, 400)
        self.assertEqual(self.client.post("/missions/api/999999/steps", json={"texte": "x"}).status_code, 404)

        self.client.post(f"/missions/api/{mid}/steps", json={"texte": "a"})
        self.client.delete(f"/missions/api/{mid}")
        db = models.get_db()
        try:
            n = db.execute("SELECT COUNT(*) FROM mission_steps WHERE mission_id = ?", (mid,)).fetchone()[0]
        finally:
            db.close()
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
