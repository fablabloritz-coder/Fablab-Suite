"""Tests : migrations de schema ordonnees et versionnees (suite 7).

Le schema est porte par une liste ordonnee de migrations idempotentes,
enregistrees dans `schema_version` : une base vide comme une base v2.1.4 (sans
table de suivi) traverse toutes les etapes sans perte, et une base a jour ne
rejoue aucun backfill. Les budgets des pages critiques sur un parc de 500 PC
sont couverts par `tests/test_parc_scale.py`.

Ce module s'execute dans son propre processus via `tests/run_all.py` : `app.py`
fige son chemin de base a l'import.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

_MODULE_TMP = tempfile.mkdtemp(prefix="fabinventory-migrations-")
os.environ["DB_PATH"] = os.path.join(_MODULE_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_MODULE_TMP, "uploads")
os.environ["SECRET_KEY"] = "tests"

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from app import (  # noqa: E402
    SCHEMA_VERSION,
    _SCHEMA_MIGRATIONS,
    app,
    get_db,
    init_db,
)

# Schema d'une base v2.1.4 : ni `schema_version`, ni `scan_date_iso`, ni
# `workflow_type`, ni `pc_name_key`, ni couverture logicielle.
LEGACY_SCHEMA = """
CREATE TABLE masters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pc_name TEXT NOT NULL,
    label TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_id INTEGER NOT NULL,
    scan_date TEXT NOT NULL,
    software_json TEXT DEFAULT '[]',
    disks_json TEXT DEFAULT '[]',
    network_json TEXT DEFAULT '[]',
    total_software INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE software_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    master_id INTEGER NOT NULL,
    software_name TEXT NOT NULL,
    software_version TEXT DEFAULT '',
    software_editor TEXT DEFAULT '',
    software_source TEXT DEFAULT '',
    search_blob TEXT DEFAULT '',
    UNIQUE(snapshot_id, software_name, software_version, software_editor, software_source)
);
CREATE TABLE software_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_id INTEGER NOT NULL,
    software_name TEXT NOT NULL,
    is_important INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    UNIQUE(master_id, software_name)
);
CREATE TABLE roadmap_minimal_pack (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    software_name TEXT NOT NULL UNIQUE,
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE roadmap_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_id INTEGER NOT NULL,
    software_name TEXT NOT NULL,
    note TEXT DEFAULT '',
    is_done INTEGER DEFAULT 0,
    source TEXT DEFAULT 'custom',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(master_id, software_name)
);
"""


def _supprimer_base():
    chemin = os.environ["DB_PATH"]
    for suffixe in ("", "-wal", "-shm"):
        try:
            os.remove(chemin + suffixe)
        except FileNotFoundError:
            pass


def _tables(db):
    return {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _colonnes(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _versions(db):
    return [
        row["version"]
        for row in db.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    ]


class SchemaMigrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        with app.app_context():
            _supprimer_base()
            init_db()

    def test_liste_ordonnee_sans_trou(self):
        versions = [version for version, _, _ in _SCHEMA_MIGRATIONS]
        self.assertEqual(versions, list(range(1, SCHEMA_VERSION + 1)))
        self.assertEqual(SCHEMA_VERSION, versions[-1])
        self.assertTrue(all(nom.strip() for _, nom, _ in _SCHEMA_MIGRATIONS))

    def test_base_vide_enregistre_toutes_les_migrations(self):
        with app.app_context():
            db = get_db()
            self.assertEqual(_versions(db), list(range(1, SCHEMA_VERSION + 1)))
            self.assertTrue(
                _tables(db)
                >= {"schema_version", "software_followups", "deleted_items", "logiciel_aliases"}
            )
            self.assertIn("software_name_norm", _colonnes(db, "software_index"))
            self.assertIn("role", _colonnes(db, "admin_users"))
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM admin_users").fetchone()["c"], 1
            )
            self.assertEqual(
                db.execute(
                    "SELECT role FROM admin_users WHERE username = 'admin'"
                ).fetchone()["role"],
                "admin",
            )

    def test_migrations_non_rejouees_sur_base_a_jour(self):
        """Une base au niveau courant ne rejoue pas les backfills."""
        with app.app_context():
            db = get_db()
            master_id = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES ('PC-GARDE', 'pc-garde', 'inventory')"
            ).lastrowid
            snapshot_id = db.execute(
                "INSERT INTO snapshots (master_id, scan_date, total_software) "
                "VALUES (?, '01/02/2026 08:00', 0)",
                (master_id,),
            ).lastrowid
            db.execute(
                "INSERT INTO software_index (snapshot_id, master_id, software_name, "
                "software_name_norm, search_blob) VALUES (?, ?, 'Outil', '', 'outil')",
                (snapshot_id, master_id),
            )
            db.commit()

            init_db()

            norm = db.execute(
                "SELECT software_name_norm FROM software_index WHERE software_name = 'Outil'"
            ).fetchone()["software_name_norm"]
            iso = db.execute(
                "SELECT scan_date_iso FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()["scan_date_iso"]
        self.assertEqual(norm, "")
        self.assertEqual(iso, "")

    def test_base_v214_migree_sans_perte(self):
        """Une base sans table de suivi traverse toutes les etapes, une seule fois."""
        _supprimer_base()
        legacy = sqlite3.connect(os.environ["DB_PATH"])
        legacy.row_factory = sqlite3.Row
        legacy.executescript(LEGACY_SCHEMA)
        master_id = legacy.execute(
            "INSERT INTO masters (pc_name, label) VALUES (' PC-Legacy ', 'Salle Info 1')"
        ).lastrowid
        snapshot_id = legacy.execute(
            "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) "
            "VALUES (?, '12/03/2024 09:30', '[]', 3)",
            (master_id,),
        ).lastrowid
        for nom in ("Microsoft Office LTSC 2021", "JavaScript Runtime", "KB5030211"):
            legacy.execute(
                "INSERT INTO software_index (snapshot_id, master_id, software_name, search_blob) "
                "VALUES (?, ?, ?, ?)",
                (snapshot_id, master_id, nom, nom.lower()),
            )
        legacy.commit()
        legacy.close()

        init_db()

        with app.app_context():
            db = get_db()
            self.assertEqual(_versions(db), list(range(1, SCHEMA_VERSION + 1)))
            master = db.execute(
                "SELECT pc_name, pc_name_key, workflow_type FROM masters WHERE id = ?",
                (master_id,),
            ).fetchone()
            self.assertEqual(master["pc_name"], "PC-Legacy")
            self.assertEqual(master["pc_name_key"], "pc-legacy")
            self.assertEqual(master["workflow_type"], "inventory")

            snapshot = db.execute(
                "SELECT scan_date, scan_date_iso FROM snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            self.assertEqual(snapshot["scan_date"], "12/03/2024 09:30")
            self.assertEqual(snapshot["scan_date_iso"], "2024-03-12 09:30:00")

            norms = {
                row["software_name"]: row["software_name_norm"]
                for row in db.execute(
                    "SELECT software_name, software_name_norm FROM software_index"
                ).fetchall()
            }
            self.assertEqual(norms["Microsoft Office LTSC 2021"], "office")
            self.assertEqual(norms["KB5030211"], "kb5030211")
            self.assertEqual(len(norms), 3)
            self.assertIn("software_category", _colonnes(db, "software_index"))
            self.assertIn("logiciel_aliases", _tables(db))
            # Les donnees historiques sont intactes.
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"], 1
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"], 1
            )

    def test_base_ancienne_sans_suivi_rejoue_les_etapes(self):
        """Un fichier sans `schema_version` (sauvegarde ancienne) est repris."""
        with app.app_context():
            db = get_db()
            for nom in ("PC-10", "PC-10 ", "pc-10"):
                db.execute(
                    "INSERT INTO masters (pc_name, workflow_type) VALUES (?, 'inventory')",
                    (nom,),
                )
            db.execute("DROP TABLE schema_version")
            db.commit()

            init_db()

            masters = db.execute("SELECT id FROM masters").fetchall()
            self.assertEqual(len(masters), 1)
            self.assertEqual(_versions(db), list(range(1, SCHEMA_VERSION + 1)))


if __name__ == "__main__":
    unittest.main()
