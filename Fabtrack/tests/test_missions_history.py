import os
import shutil
import tempfile
import unittest

import app as app_module
import models


class MissionsHistoryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-missions-tests-")

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

    def setUp(self):
        db = models.get_db()
        try:
            db.execute("DELETE FROM mission_events")
            db.execute("DELETE FROM missions")
            db.commit()
        finally:
            db.close()

    def _create_mission(self, titre, statut="a_faire"):
        response = self.client.post(
            "/missions/api/create",
            json={
                "titre": titre,
                "description": "Mission de test",
                "statut": statut,
                "priorite": 1,
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        body = response.get_json()
        self.assertTrue(body.get("success"), body)
        return int(body["data"]["id"])

    def _create_category(self, nom="Maintenance"):
        response = self.client.post(
            "/missions/api/categories",
            json={"nom": nom, "couleur": "#123abc"},
        )
        self.assertEqual(response.status_code, 201, response.data)
        body = response.get_json()
        self.assertTrue(body.get("success"), body)
        return int(body["data"]["id"])

    def test_create_mission_logs_created_event(self):
        mission_id = self._create_mission("Créer mission historique")

        db = models.get_db()
        try:
            events = db.execute(
                """
                SELECT event_type, from_statut, to_statut
                FROM mission_events
                WHERE mission_id = ?
                ORDER BY id ASC
                """,
                (mission_id,),
            ).fetchall()
        finally:
            db.close()

        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "created")
        self.assertEqual(events[0]["to_statut"], "a_faire")

    def test_status_transitions_populate_timeline_and_history(self):
        mission_id = self._create_mission("Mission transition")

        move_doing = self.client.put(
            f"/missions/api/{mission_id}",
            json={"statut": "en_cours"},
        )
        self.assertEqual(move_doing.status_code, 200, move_doing.data)

        move_done = self.client.put(
            f"/missions/api/{mission_id}",
            json={"statut": "termine"},
        )
        self.assertEqual(move_done.status_code, 200, move_done.data)

        db = models.get_db()
        try:
            status_events = db.execute(
                """
                SELECT to_statut
                FROM mission_events
                WHERE mission_id = ? AND event_type = 'status_changed'
                ORDER BY id ASC
                """,
                (mission_id,),
            ).fetchall()
        finally:
            db.close()

        self.assertEqual([r["to_statut"] for r in status_events], ["en_cours", "termine"])

        history_response = self.client.get("/missions/api/history?page=1&page_size=10")
        self.assertEqual(history_response.status_code, 200, history_response.data)
        history_body = history_response.get_json()
        self.assertTrue(history_body.get("success"), history_body)
        self.assertEqual(history_body["pagination"]["total"], 1)

        row = history_body["data"][0]
        self.assertEqual(row["id"], mission_id)
        self.assertTrue(row.get("timeline_created_at"))
        self.assertTrue(row.get("timeline_started_at"))
        self.assertTrue(row.get("timeline_finished_at"))

    def test_history_endpoint_is_paginated(self):
        for i in range(12):
            mission_id = self._create_mission(f"Mission terminée #{i:02d}")
            res = self.client.put(
                f"/missions/api/{mission_id}",
                json={"statut": "termine"},
            )
            self.assertEqual(res.status_code, 200, res.data)

        page1 = self.client.get("/missions/api/history?page=1&page_size=10")
        self.assertEqual(page1.status_code, 200, page1.data)
        body1 = page1.get_json()
        self.assertTrue(body1.get("success"), body1)
        self.assertEqual(len(body1["data"]), 10)
        self.assertEqual(body1["pagination"]["page"], 1)
        self.assertEqual(body1["pagination"]["total"], 12)
        self.assertEqual(body1["pagination"]["total_pages"], 2)

        page2 = self.client.get("/missions/api/history?page=2&page_size=10")
        self.assertEqual(page2.status_code, 200, page2.data)
        body2 = page2.get_json()
        self.assertTrue(body2.get("success"), body2)
        self.assertEqual(len(body2["data"]), 2)
        self.assertEqual(body2["pagination"]["page"], 2)

    def test_mission_list_and_history_include_category_fields(self):
        category_id = self._create_category("Infrastructure")
        create_res = self.client.post(
            "/missions/api/create",
            json={
                "titre": "Mission catégorisée",
                "description": "Mission de test catégorie",
                "statut": "termine",
                "priorite": 1,
                "category_id": category_id,
            },
        )
        self.assertEqual(create_res.status_code, 201, create_res.data)

        list_res = self.client.get("/missions/api/list")
        self.assertEqual(list_res.status_code, 200, list_res.data)
        list_body = list_res.get_json()
        self.assertTrue(list_body.get("success"), list_body)
        self.assertEqual(list_body["data"][0]["categorie_nom"], "Infrastructure")
        self.assertEqual(list_body["data"][0]["categorie_couleur"], "#123abc")

        history_res = self.client.get("/missions/api/history?page=1&page_size=10")
        self.assertEqual(history_res.status_code, 200, history_res.data)
        history_body = history_res.get_json()
        self.assertTrue(history_body.get("success"), history_body)
        self.assertEqual(history_body["data"][0]["categorie_nom"], "Infrastructure")

    def test_history_exports_support_csv_html_pdf(self):
        mission_id = self._create_mission("Mission export", statut="termine")
        self.assertGreater(mission_id, 0)

        csv_res = self.client.get("/missions/api/history/export?format=csv")
        self.assertEqual(csv_res.status_code, 200, csv_res.data)
        self.assertIn("text/csv", csv_res.headers.get("Content-Type", ""))
        csv_text = csv_res.data.decode("utf-8")
        self.assertIn("Mission export", csv_text)
        self.assertIn("ID;Titre;Description;Categorie;Priorite;Creee le;Debut;Terminee le", csv_text)

        html_res = self.client.get("/missions/api/history/export?format=html")
        self.assertEqual(html_res.status_code, 200, html_res.data)
        self.assertIn("text/html", html_res.headers.get("Content-Type", ""))
        html_text = html_res.data.decode("utf-8")
        self.assertIn("<html", html_text.lower())
        self.assertIn("Mission export", html_text)

        pdf_res = self.client.get("/missions/api/history/export?format=pdf")
        self.assertEqual(pdf_res.status_code, 200, pdf_res.data)
        self.assertIn("application/pdf", pdf_res.headers.get("Content-Type", ""))
        self.assertTrue(pdf_res.data.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
