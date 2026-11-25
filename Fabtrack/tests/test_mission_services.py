"""Tests du catalogue de services externes et du marqueur « dépend d'un autre service ».

Le marqueur n'est pas une colonne booléenne : il est DÉRIVÉ de `missions.service_id`
par les API de lecture. Ces tests couvrent donc les deux risques principaux :

- un PUT partiel (`{"statut": ...}`, cas du bouton « avancer » du kanban) ne doit
  jamais effacer le marqueur ;
- désactiver un service ne doit ni détacher les missions existantes, ni empêcher
  de les modifier, ni faire disparaître le nom du service des lectures.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

import app as app_module
import models


class MissionServicesApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_data_dir = models.DATA_DIR
        cls._orig_db_path = models.DB_PATH
        cls._tmpdir = tempfile.mkdtemp(prefix="fabtrack-services-tests-")
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

    def _service(self, nom=None, couleur="#0ea5e9"):
        nom = nom or f"Service-{self._testMethodName}-{self._service_seq}"
        self._service_seq += 1
        r = self.client.post("/missions/api/services", json={"nom": nom, "couleur": couleur})
        self.assertEqual(r.status_code, 201, r.data)
        return r.get_json()["data"]

    def _mission(self, **payload):
        body = {"titre": "Mission test"}
        body.update(payload)
        r = self.client.post("/missions/api/create", json=body)
        self.assertEqual(r.status_code, 201, r.data)
        return r.get_json()["data"]

    def _list_mission(self, mission_id):
        rows = self.client.get("/missions/api/list").get_json()["data"]
        return next(m for m in rows if m["id"] == mission_id)

    def setUp(self):
        self._service_seq = 0

    # ── Catalogue ─────────────────────────────────────────────

    def test_service_crud_et_marqueur_sur_la_mission(self):
        service = self._service(couleur="#0ea5e9")
        self.assertEqual(service["actif"], 1)

        mission = self._mission(service_id=service["id"])
        self.assertEqual(mission["service_id"], service["id"])
        self.assertEqual(mission["depend_service_externe"], 1)
        self.assertEqual(mission["service_nom"], service["nom"])
        self.assertEqual(mission["service_couleur"], "#0ea5e9")

        # Le marqueur suit le service même après renommage (jointure par id).
        r = self.client.put(
            f"/missions/api/services/{service['id']}",
            json={"nom": service["nom"] + " (renommé)", "couleur": "#ef4444"},
        )
        self.assertEqual(r.status_code, 200, r.data)
        relue = self._list_mission(mission["id"])
        self.assertEqual(relue["service_nom"], service["nom"] + " (renommé)")
        self.assertEqual(relue["service_couleur"], "#ef4444")

    def test_mission_sans_service_n_a_pas_de_marqueur(self):
        mission = self._mission()
        self.assertIsNone(mission["service_id"])
        self.assertEqual(mission["depend_service_externe"], 0)
        self.assertIsNone(mission["service_nom"])

    def test_nom_de_service_unique(self):
        service = self._service()
        r = self.client.post("/missions/api/services", json={"nom": service["nom"]})
        self.assertEqual(r.status_code, 400, r.data)
        self.assertFalse(r.get_json()["success"])

    def test_nom_de_service_requis(self):
        r = self.client.post("/missions/api/services", json={"nom": "   "})
        self.assertEqual(r.status_code, 400, r.data)

    # ── Rétro-compatibilité des mises à jour ──────────────────

    def test_mise_a_jour_partielle_conserve_le_marqueur(self):
        service = self._service()
        mission = self._mission(service_id=service["id"])

        # Cas réel : moveStatus() n'envoie que le statut.
        r = self.client.put(f"/missions/api/{mission['id']}", json={"statut": "en_cours"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["data"]["depend_service_externe"], 1)
        self.assertEqual(self._list_mission(mission["id"])["service_nom"], service["nom"])

    def test_decocher_le_service_efface_le_marqueur(self):
        service = self._service()
        mission = self._mission(service_id=service["id"])

        r = self.client.put(f"/missions/api/{mission['id']}", json={"service_id": None})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["data"]["depend_service_externe"], 0)

        # Une chaîne vide est traitée comme « aucune dépendance ».
        r = self.client.put(f"/missions/api/{mission['id']}", json={"service_id": service["id"]})
        self.assertEqual(r.get_json()["data"]["depend_service_externe"], 1)
        r = self.client.put(f"/missions/api/{mission['id']}", json={"service_id": ""})
        self.assertEqual(r.get_json()["data"]["depend_service_externe"], 0)

    def test_service_inconnu_refuse(self):
        r = self.client.post("/missions/api/create", json={"titre": "M", "service_id": 999999})
        self.assertEqual(r.status_code, 400, r.data)

    def test_service_id_malforme_refuse_sans_effacer_le_marqueur(self):
        service = self._service()
        mission = self._mission(service_id=service["id"])

        r = self.client.put(f"/missions/api/{mission['id']}", json={"service_id": "abc"})
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(self._list_mission(mission["id"])["depend_service_externe"], 1)

        r = self.client.post("/missions/api/create", json={"titre": "M", "service_id": "abc"})
        self.assertEqual(r.status_code, 400, r.data)

    # ── Désactivation ─────────────────────────────────────────

    def test_service_desactive_non_affectable_mais_conserve(self):
        service = self._service()
        mission = self._mission(service_id=service["id"])

        r = self.client.delete(f"/missions/api/services/{service['id']}")
        self.assertEqual(r.status_code, 200, r.data)

        # Plus proposé pour une nouvelle affectation...
        actifs = [s["id"] for s in self.client.get("/missions/api/services").get_json()["data"]]
        self.assertNotIn(service["id"], actifs)
        r = self.client.post("/missions/api/create", json={"titre": "M2", "service_id": service["id"]})
        self.assertEqual(r.status_code, 400, r.data)

        autre = self._mission()
        r = self.client.put(f"/missions/api/{autre['id']}", json={"service_id": service["id"]})
        self.assertEqual(r.status_code, 400, r.data)

        # ... mais la mission existante garde son marqueur et son nom.
        relue = self._list_mission(mission["id"])
        self.assertEqual(relue["depend_service_externe"], 1)
        self.assertEqual(relue["service_nom"], service["nom"])

        # Édition sans toucher au service : acceptée.
        r = self.client.put(f"/missions/api/{mission['id']}", json={"titre": "Renommée"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["data"]["depend_service_externe"], 1)

        # Édition qui renvoie explicitement le service inchangé (formulaire) : acceptée.
        r = self.client.put(
            f"/missions/api/{mission['id']}",
            json={"titre": "Renommée 2", "service_id": service["id"]},
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["data"]["depend_service_externe"], 1)

        # Le service reste visible dans la gestion, marqué inactif.
        tous = self.client.get("/missions/api/services?include_inactive=true").get_json()["data"]
        ligne = next(s for s in tous if s["id"] == service["id"])
        self.assertEqual(ligne["actif"], 0)

        # Et il peut être réactivé.
        r = self.client.put(
            f"/missions/api/services/{service['id']}",
            json={"nom": service["nom"], "couleur": service["couleur"], "actif": 1},
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["data"]["actif"], 1)
        actifs = [s["id"] for s in self.client.get("/missions/api/services").get_json()["data"]]
        self.assertIn(service["id"], actifs)

    def test_put_service_sans_actif_conserve_la_desactivation(self):
        """Contrat utilisé par l'écran de gestion : renommer ne réactive pas."""
        service = self._service()
        self.client.delete(f"/missions/api/services/{service['id']}")

        r = self.client.put(
            f"/missions/api/services/{service['id']}",
            json={"nom": service["nom"] + " renommé", "couleur": "#f97316"},
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["data"]["actif"], 0)

    def test_historique_expose_le_marqueur(self):
        service = self._service()
        mission = self._mission(statut="termine", service_id=service["id"])

        data = self.client.get("/missions/api/history").get_json()["data"]
        ligne = next(m for m in data if m["id"] == mission["id"])
        self.assertEqual(ligne["depend_service_externe"], 1)
        self.assertEqual(ligne["service_nom"], service["nom"])

    # ── Page ─────────────────────────────────────────────────

    def test_page_missions_rend_les_controles_service(self):
        r = self.client.get("/missions/")
        self.assertEqual(r.status_code, 200, r.data)
        page = r.data.decode("utf-8")
        self.assertIn("missionDependService", page)
        self.assertIn("missionService", page)
        self.assertIn("modalServices", page)

    # ── Migration ─────────────────────────────────────────────

    def test_migration_idempotente(self):
        db = models.get_db()
        try:
            colonnes = [r[1] for r in db.execute("PRAGMA table_info(missions)").fetchall()]
            self.assertIn("service_id", colonnes)

            # Rejouer la migration ne doit rien dupliquer ni échouer.
            models._ensure_mission_services_schema(db)
            models._ensure_mission_services_schema(db)
            db.commit()
            colonnes_apres = [r[1] for r in db.execute("PRAGMA table_info(missions)").fetchall()]
            self.assertEqual(colonnes, colonnes_apres)
            tables = [
                r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'mission_services'"
                ).fetchall()
            ]
            self.assertEqual(tables, ["mission_services"])
        finally:
            db.close()


class MissionServicesExistingDbMigrationTests(unittest.TestCase):
    """Migration d'une base antérieure à 2.14.0 (missions sans service_id).

    Régression : un index sur `missions(service_id)` créé dans le script d'init
    échouait AVANT la migration qui ajoute la colonne, car
    `CREATE TABLE IF NOT EXISTS missions` ne modifie pas une table existante.
    """

    def setUp(self):
        self._orig_data_dir = models.DATA_DIR
        self._orig_db_path = models.DB_PATH
        self._tmpdir = tempfile.mkdtemp(prefix="fabtrack-old-schema-")
        models.DATA_DIR = self._tmpdir
        models.DB_PATH = os.path.join(self._tmpdir, "fabtrack_old.db")

        conn = sqlite3.connect(models.DB_PATH)
        conn.execute(
            '''CREATE TABLE missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titre TEXT NOT NULL,
                description TEXT DEFAULT '',
                category_id INTEGER,
                statut TEXT NOT NULL DEFAULT 'a_faire',
                priorite INTEGER DEFAULT 0,
                ordre INTEGER DEFAULT 0,
                date_echeance TEXT DEFAULT NULL,
                date_creation TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )'''
        )
        conn.execute("INSERT INTO missions (titre) VALUES ('Ancienne mission')")
        conn.commit()
        conn.close()

    def tearDown(self):
        models.DATA_DIR = self._orig_data_dir
        models.DB_PATH = self._orig_db_path
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_init_db_migre_une_base_existante(self):
        models.init_db()  # ne doit pas lever

        db = models.get_db()
        try:
            colonnes = [r[1] for r in db.execute("PRAGMA table_info(missions)").fetchall()]
            self.assertIn("service_id", colonnes)
            ligne = db.execute(
                "SELECT service_id FROM missions WHERE titre = 'Ancienne mission'"
            ).fetchone()
            self.assertIsNone(ligne["service_id"])
            index = db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_missions_service'"
            ).fetchone()
            self.assertIsNotNone(index, "l'index du marqueur doit exister apres migration")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
