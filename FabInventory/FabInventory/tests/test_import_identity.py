"""Tests du Lot 4 : import d'identite fiable, apercu et rapport de fusion.

Le nom de fichier ne suffit pas a identifier un poste : ces tests verifient que
la serie du dernier scan est comparee avant toute ecriture, que l'apercu ne
modifie rien, et que le rapport de fusion compte creations, mises a jour,
doublons et refus.
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-lot4-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import app, get_db, init_db  # noqa: E402


def _inventory_html(
    pc_name,
    scan_date,
    serial="SN-001",
    fabricant="Dell",
    domaine="FABLAB",
    software_name="Firefox",
):
    payload = {
        "pcName": pc_name,
        "date": scan_date,
        "software": [
            {
                "n": software_name,
                "v": "128",
                "e": "Mozilla",
                "d": scan_date,
                "s": 1,
                "src": "Registre",
            }
        ],
    }
    spans = (
        '<span class="label">OS</span><span>Windows 11</span>'
        f'<span class="label">Fabricant</span><span>{fabricant}</span>'
        f'<span class="label">Numero de serie</span><span>{serial}</span>'
        f'<span class="label">Domaine</span><span>{domaine}</span>'
    )
    return (
        f'<html><body>{spans}'
        '<script id="inventoryData" type="application/json">'
        f"{json.dumps(payload)}"
        "</script></body></html>"
    )


class ImportIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)
        login_admin(self.client)

    # ── utilitaires ──────────────────────────────────────────────────────────

    def _upload(self, html, filename="inventaire.html", **extra):
        data = {"file": (io.BytesIO(html.encode("utf-8")), filename)}
        data.update(extra)
        return self.client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )

    def _preview(self, files):
        data = {
            "file": [
                (io.BytesIO(html.encode("utf-8")), filename)
                for filename, html in files
            ],
            "preview": "1",
        }
        return self.client.post(
            "/upload", data=data, content_type="multipart/form-data"
        )

    def _confirm(self, token, reimaged=()):
        data = {"confirm_token": token}
        if reimaged:
            data["reimaged"] = [str(index) for index in reimaged]
        return self.client.post("/upload", data=data)

    def _token(self, response):
        match = re.search(r'name="confirm_token" value="([0-9a-f]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, "le formulaire de confirmation doit porter un jeton")
        return match.group(1)

    def _counts(self):
        with app.app_context():
            db = get_db()
            return (
                db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"],
                db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"],
            )

    def _html(self, response):
        return response.get_data(as_text=True)

    # ── identite ─────────────────────────────────────────────────────────────

    def test_meme_identite_importe_et_rapport_de_fusion(self):
        first = self._upload(_inventory_html("PC-01", "28/11/2026 10:00"))
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self._counts(), (1, 1))

        home = self._html(self.client.get("/"))
        self.assertIn("Rapport de fusion", home)
        self.assertIn("Master(s) créé(s)", home)
        self.assertIn("Créé", home)

        second = self._upload(
            _inventory_html("PC-01", "29/11/2026 11:00", software_name="7-Zip")
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self._counts(), (1, 2))

        home = self._html(self.client.get("/"))
        self.assertIn("Mis à jour", home)
        self.assertIn("2", home)

    def test_serie_differente_refuse_import_direct(self):
        self._upload(_inventory_html("PC-01", "28/11/2026 10:00", serial="SN-A"))
        refused = self._upload(
            _inventory_html("PC-01", "29/11/2026 11:00", serial="SN-B")
        )
        self.assertEqual(refused.status_code, 302)
        # Aucun snapshot n'a ete ecrit sur le master existant.
        self.assertEqual(self._counts(), (1, 1))

        home = self._html(self.client.get("/"))
        self.assertIn("Refusé (identité)", home)
        self.assertIn("serie SN-A", home)
        self.assertIn("SN-B", home)
        self.assertIn("poste réimaginé", home)

        with app.app_context():
            db = get_db()
            snapshot = db.execute(
                "SELECT num_serie FROM snapshots ORDER BY id"
            ).fetchone()
            self.assertEqual(snapshot["num_serie"], "SN-A")

    def test_fabricant_et_domaine_signales_sans_blocage(self):
        self._upload(_inventory_html("PC-01", "28/11/2026 10:00"))
        accepted = self._upload(
            _inventory_html(
                "PC-01",
                "29/11/2026 11:00",
                fabricant="HP",
                domaine="ATELIER",
                software_name="7-Zip",
            )
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(self._counts(), (1, 2))

        preview = self._preview(
            [
                (
                    "troisieme.html",
                    _inventory_html(
                        "PC-01",
                        "30/11/2026 09:00",
                        fabricant="Lenovo",
                        domaine="ATELIER",
                        software_name="VLC",
                    ),
                )
            ]
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Fabricant : HP → Lenovo", self._html(preview))
        self.assertIn("Mis à jour", self._html(preview))
        self.assertNotIn("Conflits d'identité", self._html(preview))

    # ── apercu et confirmation ───────────────────────────────────────────────

    def test_apercu_ne_cree_rien_et_liste_le_plan(self):
        self._upload(_inventory_html("PC-01", "28/11/2026 10:00"))

        preview = self._preview(
            [
                (
                    "maj.html",
                    _inventory_html("PC-01", "29/11/2026 11:00", software_name="7-Zip"),
                ),
                ("nouveau.html", _inventory_html("PC-02", "29/11/2026 11:00")),
                ("casse.html", "<html><body>pas d'inventaire</body></html>"),
            ]
        )
        self.assertEqual(preview.status_code, 200)
        body = self._html(preview)
        self.assertIn("Aucune donnée n'a été écrite", body)
        self.assertIn("PC-02", body)
        self.assertIn("Mis à jour", body)
        self.assertIn("Créé", body)
        self.assertIn("Refusé (format)", body)
        # L'apercu n'a rien ecrit.
        self.assertEqual(self._counts(), (1, 1))

    def test_confirmation_importe_le_plan(self):
        self._upload(_inventory_html("PC-01", "28/11/2026 10:00"))

        preview = self._preview(
            [
                (
                    "maj.html",
                    _inventory_html("PC-01", "29/11/2026 11:00", software_name="7-Zip"),
                ),
                ("nouveau.html", _inventory_html("PC-02", "29/11/2026 11:00")),
            ]
        )
        token = self._token(preview)

        confirmed = self._confirm(token)
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(self._counts(), (2, 3))

        body = self._html(self.client.get("/"))
        self.assertIn("Rapport de fusion", body)
        self.assertIn("Master(s) créé(s)", body)
        self.assertIn("Mis à jour", body)
        self.assertIn("Créé", body)

        # Un jeton deja consomme ne rejoue rien.
        self.assertEqual(self._confirm(token).status_code, 302)
        self.assertEqual(self._counts(), (2, 3))

    def test_confirmation_re_resout_sur_l_etat_courant(self):
        preview = self._preview(
            [
                ("pc10.html", _inventory_html("PC-10", "28/11/2026 10:00")),
                ("pc11.html", _inventory_html("PC-11", "28/11/2026 10:00")),
            ]
        )
        token = self._token(preview)

        # Entre l'apercu et la confirmation, PC-10 est importe par un autre
        # chemin : la confirmation doit le traiter en mise a jour, pas en
        # seconde creation.
        self._upload(
            _inventory_html("PC-10", "29/11/2026 11:00", software_name="7-Zip")
        )
        self._confirm(token)

        self.assertEqual(self._counts(), (2, 3))
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "SELECT id FROM masters WHERE pc_name_key = 'pc-10'"
            ).fetchone()["id"]
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) AS c FROM snapshots WHERE master_id = ?",
                    (master_id,),
                ).fetchone()["c"],
                2,
            )

        body = self._html(self.client.get("/"))
        self.assertIn("Mis à jour", body)
        self.assertIn("Créé", body)

    def test_conflit_accepte_comme_poste_reimagine(self):
        self._upload(_inventory_html("PC-01", "28/11/2026 10:00", serial="SN-A"))

        preview = self._preview(
            [
                (
                    "reimage.html",
                    _inventory_html("PC-01", "29/11/2026 11:00", serial="SN-B"),
                )
            ]
        )
        self.assertIn("Conflits d'identité", self._html(preview))
        token = self._token(preview)

        # Sans case cochee, le conflit reste refuse.
        self._confirm(token)
        self.assertEqual(self._counts(), (1, 1))

        preview = self._preview(
            [
                (
                    "reimage.html",
                    _inventory_html("PC-01", "29/11/2026 11:00", serial="SN-B"),
                )
            ]
        )
        token = self._token(preview)
        self._confirm(token, reimaged=[0])
        self.assertEqual(self._counts(), (1, 2))
        with app.app_context():
            db = get_db()
            snapshot = db.execute(
                "SELECT num_serie FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(snapshot["num_serie"], "SN-B")

    def test_deux_fichiers_du_meme_poste_dans_un_lot(self):
        preview = self._preview(
            [
                ("scan1.html", _inventory_html("PC-05", "28/11/2026 10:00")),
                (
                    "scan2.html",
                    _inventory_html("PC-05", "29/11/2026 11:00", software_name="7-Zip"),
                ),
            ]
        )
        body = self._html(preview)
        # Le second fichier vise le master cree par le premier, pas une seconde creation.
        self.assertIn("(créé dans ce lot)", body)
        self.assertNotIn("Impossible", body)

        self._confirm(self._token(preview))
        self.assertEqual(self._counts(), (1, 2))
        home = self._html(self.client.get("/"))
        self.assertIn("Mis à jour", home)

    def test_confirmation_invalide_ne_modifie_rien(self):
        response = self._confirm("0123456789abcdef0123456789abcdef")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._counts(), (0, 0))

        body = self._html(self.client.get("/upload"))
        self.assertNotIn("Confirmer l'import", body)

    def test_doublon_identique_compte_dans_le_rapport(self):
        html = _inventory_html("PC-09", "28/11/2026 10:00")
        self._upload(html)
        self._upload(html)
        self.assertEqual(self._counts(), (1, 1))

        body = self._html(self.client.get("/"))
        self.assertIn("Ignoré (identique)", body)
        self.assertIn("identique(s) ignoré(s)", body)


if __name__ == "__main__":
    unittest.main()
