"""Tests de la suite 5 : couverture logicielle par normalisation et alias.

Les noms de logiciels sont compares sur une cle normalisee (casse, accents,
prefixe d'editeur, suffixe d'edition/version) ou via un alias explicite. La
comparaison n'est jamais une sous-chaine : Java ne doit pas compter JavaScript,
Office ne doit pas compter OpenOffice, Visual Studio ne doit pas compter Visual
Studio Code, ni Python compter Python Launcher.
"""
import os
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-coverage-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"
os.environ["ADMIN_PASSWORD"] = "coverage-admin-password"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from tests.support import login_admin, reset_temp_db  # noqa: E402
from app import (  # noqa: E402
    _coverage_for_logiciels,
    _coverage_for_masters,
    _normalize_software_name,
    _software_masters_count,
    _upsert_snapshot_software_index,
    app,
    get_db,
    init_db,
)


def _software(name, version="1.0"):
    return {"n": name, "v": version, "e": "Test", "src": "Registre"}


class SoftwareCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            reset_temp_db(init_db)

    def _add_logiciel(self, name):
        with app.app_context():
            db = get_db()
            existing = db.execute(
                "SELECT id FROM logiciels WHERE nom = ?", (name,)
            ).fetchone()
            if existing:
                return existing["id"]
            logiciel_id = db.execute(
                "INSERT INTO logiciels (nom) VALUES (?)", (name,)
            ).lastrowid
            db.commit()
            return logiciel_id

    def _add_master(
        self,
        pc_name,
        software_items,
        scan_date="26/11/2026 21:00",
        workflow_type="inventory",
    ):
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES (?, ?, ?)",
                (pc_name, pc_name.lower(), workflow_type),
            ).lastrowid
            snapshot_id = db.execute(
                "INSERT INTO snapshots "
                "(master_id, scan_date, software_json, total_software) "
                "VALUES (?, ?, '[]', ?)",
                (master_id, scan_date, len(software_items)),
            ).lastrowid
            _upsert_snapshot_software_index(
                db, snapshot_id, master_id, software_items
            )
            db.commit()
            return master_id

    def _count(self, name):
        with app.app_context():
            return _software_masters_count(get_db(), name)

    def test_cle_normalisee_ignore_casse_accents_et_editions(self):
        self.assertEqual(
            _normalize_software_name("Microsoft Office LTSC 2021"), "office"
        )
        self.assertEqual(_normalize_software_name("  microsoft   OFFICE  "), "office")
        self.assertEqual(_normalize_software_name("Logiciel Éducatif"), "logiciel educatif")
        self.assertEqual(_normalize_software_name("7-Zip 24.08"), "7 zip")
        self.assertEqual(
            _normalize_software_name("Microsoft Office Professional Plus 2019"),
            "office",
        )
        self.assertEqual(
            _normalize_software_name(
                "Microsoft Visual C++ 2015-2022 Redistributable (x64)"
            ),
            "visual c redistributable",
        )
        self.assertEqual(
            _normalize_software_name("VLC media player"), "vlc media player"
        )

    def test_normalisation_couvre_les_variantes_sans_sous_chaine(self):
        self._add_master("PC-OFFICE", [_software("Microsoft Office LTSC 2021")])
        self._add_master("PC-ACCENT", [_software("logiciel educatif")])
        self._add_master("PC-ZIP", [_software("7-Zip 24.08")])
        self._add_logiciel("Microsoft Office")
        self._add_logiciel("Logiciel Éducatif")
        self._add_logiciel("7-Zip")

        self.assertEqual(self._count("Microsoft Office"), 1)
        self.assertEqual(self._count("Logiciel Éducatif"), 1)
        self.assertEqual(self._count("7-Zip"), 1)

    def test_faux_positifs_java_office_visual_studio_python(self):
        self._add_master("PC-JS", [_software("JavaScript")])
        self._add_master("PC-OO", [_software("Apache OpenOffice 4.1.15")])
        self._add_master("PC-VSC", [_software("Visual Studio Code")])
        self._add_master("PC-PY-LAUNCHER", [_software("Python Launcher")])
        for name in ("Java", "Microsoft Office", "Visual Studio", "Python"):
            self._add_logiciel(name)

        self.assertEqual(self._count("Java"), 0)
        self.assertEqual(self._count("Microsoft Office"), 0)
        self.assertEqual(self._count("Visual Studio"), 0)
        self.assertEqual(self._count("Python"), 0)

    def test_les_vraies_versions_restent_couvertes(self):
        self._add_master("PC-JAVA", [_software("Java 8 Update 411")])
        self._add_master("PC-OFFICE", [_software("Microsoft Office LTSC 2021")])
        self._add_master("PC-VS", [_software("Visual Studio 2022")])
        self._add_master("PC-PY", [_software("Python 3.12.7")])
        for name in ("Java", "Microsoft Office", "Visual Studio", "Python"):
            self._add_logiciel(name)

        self.assertEqual(self._count("Java"), 1)
        self.assertEqual(self._count("Microsoft Office"), 1)
        self.assertEqual(self._count("Visual Studio"), 1)
        self.assertEqual(self._count("Python"), 1)

    def test_alias_admin_ajoute_et_retire_la_couverture(self):
        logiciel_id = self._add_logiciel("VLC")
        self._add_master("PC-VLC", [_software("VLC media player")])
        self.assertEqual(self._count("VLC"), 0)

        login_admin(self.client)
        response = self.client.post(
            f"/admin/logiciels/{logiciel_id}/aliases/add",
            data={"alias_nom": "VLC media player"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._count("VLC"), 1)

        page = self.client.get("/admin/logiciels").get_data(as_text=True)
        self.assertIn("VLC media player", page)

        with app.app_context():
            alias_id = get_db().execute(
                "SELECT id FROM logiciel_aliases WHERE logiciel_id = ?",
                (logiciel_id,),
            ).fetchone()["id"]
        response = self.client.post(
            f"/admin/logiciels/{logiciel_id}/aliases/{alias_id}/delete"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._count("VLC"), 0)

    def test_alias_refuse_aux_visiteurs_anonymes(self):
        logiciel_id = self._add_logiciel("VLC")
        anonymous = app.test_client()
        response = anonymous.post(
            f"/admin/logiciels/{logiciel_id}/aliases/add",
            data={"alias_nom": "VLC media player"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])
        self.assertEqual(self._count("VLC"), 0)

    def test_couverture_agregee_compte_chaque_master_une_fois(self):
        logiciel_id = self._add_logiciel("Office")
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO logiciel_aliases (logiciel_id, alias_nom, alias_norm) "
                "VALUES (?, 'Microsoft Office', 'microsoft office')",
                (logiciel_id,),
            )
            db.commit()
        # Le meme master expose deux variantes : il ne compte qu'une fois.
        self._add_master(
            "PC-OFFICE",
            [_software("Microsoft Office 365"), _software("Office")],
        )
        self._add_master("PC-OO", [_software("OpenOffice")])

        with app.app_context():
            db = get_db()
            logiciels = db.execute("SELECT id, nom FROM logiciels").fetchall()
            coverage = _coverage_for_logiciels(db, logiciels)
        self.assertEqual(coverage[logiciel_id], 1)

    def test_compteurs_agreges_et_par_master_partagent_la_meme_regle(self):
        office_id = self._add_logiciel("Microsoft Office")
        java_id = self._add_logiciel("Java")
        pc_a = self._add_master(
            "PC-A",
            [_software("Microsoft Office LTSC 2021"), _software("Java 8 Update 411")],
        )
        pc_b = self._add_master(
            "PC-B",
            [_software("Microsoft Office 365"), _software("JavaScript")],
        )
        prep = self._add_master(
            "PREP-OFFICE",
            [_software("Microsoft Office 365")],
            workflow_type="preparation",
        )

        with app.app_context():
            db = get_db()
            logiciels = db.execute(
                "SELECT id, nom FROM logiciels WHERE actif = 1"
            ).fetchall()
            masters = db.execute(
                "SELECT id, workflow_type FROM masters"
            ).fetchall()
            coverage = _coverage_for_logiciels(db, logiciels)
            per_master = _coverage_for_masters(db, masters, logiciels)
            office = next(l for l in logiciels if l["id"] == office_id)
            java = next(l for l in logiciels if l["id"] == java_id)
            office_by_master = _coverage_for_masters(db, masters, [office])
            java_by_master = _coverage_for_masters(db, masters, [java])

        self.assertEqual(coverage[office_id], 2)
        self.assertEqual(coverage[java_id], 1)
        self.assertEqual(per_master[prep], 0)
        self.assertEqual(office_by_master[pc_a], 1)
        self.assertEqual(office_by_master[pc_b], 1)
        self.assertEqual(java_by_master[pc_a], 1)
        self.assertEqual(java_by_master[pc_b], 0)
        self.assertEqual(sum(office_by_master.values()), coverage[office_id])
        self.assertEqual(sum(java_by_master.values()), coverage[java_id])

    def test_pages_partagent_les_memes_compteurs(self):
        with app.app_context():
            db = get_db()
            db.execute("UPDATE logiciels SET actif = 0")
            db.commit()
        office_id = self._add_logiciel("Microsoft Office")
        self._add_logiciel("Java")
        self._add_master(
            "PC-A",
            [_software("Microsoft Office LTSC 2021"), _software("Java 8 Update 411")],
        )
        self._add_master("PC-B", [_software("Microsoft Office 365")])
        self._add_master(
            "PREP-OFFICE",
            [_software("Microsoft Office 365")],
            workflow_type="preparation",
        )
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO prof_tokens (email, nom, token, actif) "
                "VALUES ('prof@example.test', 'Prof Test', 'coverage-token', 1)"
            )
            prof_id = db.execute(
                "SELECT id FROM prof_tokens WHERE token = 'coverage-token'"
            ).fetchone()["id"]
            demande_id = db.execute(
                "INSERT INTO demandes (prof_token_id, logiciel_id) VALUES (?, ?)",
                (prof_id, office_id),
            ).lastrowid
            db.commit()

        login_admin(self.client)
        dashboard = self.client.get("/admin/").get_data(as_text=True)
        masters_page = self.client.get("/admin/masters").get_data(as_text=True)
        catalogue = self.client.get("/admin/logiciels").get_data(as_text=True)
        portail = self.client.get("/prof/coverage-token").get_data(as_text=True)

        # Dashboard : 2 logiciels actifs couverts sur 2.
        self.assertIn(
            '2<span class="fs-6 text-muted fw-normal">/2', dashboard
        )
        # Masters : PC-A a les deux, PC-B un seul ; la preparation ne compte pas.
        self.assertIn("2/2", masters_page)
        self.assertIn("1/2", masters_page)
        # Catalogue : Office sur 2 masters, Java sur 1.
        self.assertIn("2/2", catalogue)
        self.assertIn("1/2", catalogue)
        # Portail : memes comptes, sans le master de preparation.
        self.assertIn("Déjà sur 2 postes", portail)
        self.assertIn("Déjà sur 1 poste", portail)
        detail = self.client.get(
            f"/api/admin/demandes/{demande_id}/details"
        ).get_json()
        self.assertEqual(detail["masters_coverage"], 2)

    def test_identifiant_kb_reste_couvert_sans_sous_chaine(self):
        self._add_logiciel("KB5030211")
        self._add_master(
            "PC-KB", [_software("Windows Cumulative Update KB5030211")]
        )
        self._add_master(
            "PC-KB-AUTRE", [_software("Windows Cumulative Update KB50302110")]
        )

        self.assertEqual(self._count("KB5030211"), 1)


if __name__ == "__main__":
    unittest.main()
