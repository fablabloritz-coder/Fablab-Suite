"""
FabInventory v2.1.0 - Gestionnaire d'inventaire de masters FOG
Flask + SQLite - Compatible Fablab-Suite
"""

import os
import gzip
import hashlib
from collections import defaultdict
import html
import json
import sqlite3
import re
import time
import unicodedata
import secrets
import base64
import io
import csv
import zipfile
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, send_from_directory, session, abort
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from urllib.parse import urlencode

# Source unique de la version : manifeste FabSuite et templates.
APP_VERSION = "2.1.4"

# Version du script PowerShell servi par /download/master-script. Elle doit
# rester alignee sur $script:ScriptVersion (verifie par les tests).
CURRENT_SCRIPT_VERSION = "3.1.0"

# Prefixes editeurs et suffixes d'edition retires pour comparer les noms de
# logiciels sans faux positifs (Java/JavaScript, Office/OpenOffice...).
_EDITOR_PREFIXES = {
    "adobe",
    "apache",
    "apple",
    "autodesk",
    "cisco",
    "google",
    "hp",
    "intel",
    "lenovo",
    "microsoft",
    "mozilla",
    "nvidia",
    "oracle",
    "realtek",
    "vmware",
}
_EDITION_SUFFIXES = {
    "dc",
    "edition",
    "enterprise",
    "home",
    "ltsb",
    "ltsc",
    "plus",
    "pro",
    "professional",
    "standard",
    "update",
    "x64",
    "x86",
}


def _normalize_software_name(value):
    """Cle de comparaison d'un nom de logiciel.

    Minuscules, sans accents ni ponctuation, sans prefixe d'editeur ni suffixe
    d'edition ou de version. La correspondance reste exacte sur cette cle (ou
    via un alias), pour ne pas confondre Java avec JavaScript ni Visual Studio
    avec Visual Studio Code.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " et ")
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    if len(tokens) > 1 and tokens[0] in _EDITOR_PREFIXES:
        tokens = tokens[1:]
    # Une annee au milieu du nom (Office Professional Plus 2019,
    # Visual C++ 2015-2022) est une version, pas le nom du produit.
    tokens = [
        token
        for token in tokens
        if not (len(token) == 4 and token.isdigit() and 1990 <= int(token) <= 2099)
    ]
    while len(tokens) > 1 and (
        tokens[-1].isdigit() or tokens[-1] in _EDITION_SUFFIXES
    ):
        tokens.pop()
    return " ".join(tokens)

app = Flask(__name__)

# Fichiers du compagnon PowerShell : source unique pour le telechargement, le
# pack ZIP et l'empreinte SHA256 affichee.
_DOWNLOADS_DIR = os.path.join(app.root_path, "static", "downloads")
_SCRIPT_ASSET_NAME = "inventaire_master.ps1"
_LAUNCHER_ASSET_NAME = "lancer_inventaire_master.bat"

app.jinja_env.globals["app_version"] = APP_VERSION

# Valeurs historiques connues : les accepter reviendrait a laisser forger un
# cookie de session admin. Elles sont remplacees par une cle aleatoire.
_INSECURE_SECRET_KEYS = {
    "change-me-in-production",
    "fabinventory-secret-change-me",
    "secret",
    "password",
    "changeme",
    "change-me",
    "test",
    "tests",
    "admin",
    "dev",
}
_INSECURE_SECRET_KEY_MARKERS = ("change-me", "changeme", "fabinventory-secret")
SECRET_KEY_MIN_LENGTH = 32
SECRET_KEY_MIN_UNIQUE_CHARS = 10


def _is_strong_secret_key(value):
    """Refuse une cle trop courte, connue ou trop peu variee."""
    if len(value) < SECRET_KEY_MIN_LENGTH:
        return False
    lowered = value.lower()
    if lowered in _INSECURE_SECRET_KEYS:
        return False
    if any(marker in lowered for marker in _INSECURE_SECRET_KEY_MARKERS):
        return False
    return len(set(value)) >= SECRET_KEY_MIN_UNIQUE_CHARS


app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB max upload
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
if os.environ.get("COOKIE_SECURE", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
# Cache navigateur des fichiers statiques : evite une revalidation a chaque page.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

_COMPRESSIBLE_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml")


@app.context_processor
def _inject_script_version():
    return {"current_script_version": CURRENT_SCRIPT_VERSION}


@app.after_request
def _compress_text_response(response):
    """Compresse les reponses texte construites en memoire (HTML, JSON, SVG).

    Les fichiers statiques sont servis en flux direct avec ETag : ils ne
    passent pas par ici, leur cache est gere par SEND_FILE_MAX_AGE_DEFAULT.
    """
    if (
        response.status_code == 200
        and not response.direct_passthrough
        and not response.headers.get("Content-Encoding")
        and "gzip" in request.headers.get("Accept-Encoding", "")
        and (response.content_length or 0) > 500
        and response.mimetype
        and any(response.mimetype.startswith(t) or response.mimetype == t
                for t in _COMPRESSIBLE_TYPES)
    ):
        response.set_data(gzip.compress(response.get_data(), 6))
        response.headers["Content-Encoding"] = "gzip"
        response.headers.add("Vary", "Accept-Encoding")
    return response

DB_PATH = os.environ.get("DB_PATH", "/data/fabinventory.db")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/data/uploads")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

COUNTS_CACHE_TTL_SECONDS = 45
_COUNTS_CACHE = {"expires_at": 0.0, "value": None}


def _perf_logging_enabled():
    return app.debug or os.environ.get("PERF_LOG", "0") == "1"


def _parse_scan_date_iso(value):
    """Convertit une date de scan connue en ISO `YYYY-MM-DD HH:MM:SS`.

    Les anciens rapports utilisent `jj/mm/aaaa hh:mm` ; les tests et certains
    generateurs utilisent deja l'ISO. Retourne une chaine vide si le format
    n'est pas reconnu, afin que l'appelant puisse choisir un repli.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in (
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def _stale_cutoff_iso(days=30):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _master_key(name):
    """Cle d'identite stable d'un master (casse et espaces ignores)."""
    return str(name or "").strip().lower()


def _safe_filename(value, fallback="master"):
    """Composant de nom de fichier sans separateur ni caractere d'en-tete."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return cleaned[:80] or fallback


def _snapshot_content_hash(pc_name, scan_date, software, sys_info, disks=None, network=None, services=None):
    """Empreinte canonique d'un import, pour ignorer un fichier strictement identique."""
    payload = {
        "pc": _master_key(pc_name),
        "date": str(scan_date or "").strip(),
        "software": software or [],
        "sys": sys_info or {},
        "disks": disks or [],
        "network": network or [],
    }
    # Ne pas changer l'empreinte des anciens rapports (sans services) : sinon
    # leur premier reimport creerait un snapshot identique une seconde fois.
    if services:
        payload["services"] = services
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ===== DATABASE =====
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        # Attend jusqu'a 5 s qu'une ecriture concurrente libere la base au lieu
        # d'echouer immediatement sur "database is locked".
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db: db.close()


ADMIN_INITIAL_PASSWORD_FILENAME = "admin-initial-password.txt"


def _admin_initial_password_path():
    """Fichier lisible par le seul proprietaire, a cote de la base."""
    return os.path.join(
        os.path.dirname(os.path.abspath(DB_PATH)), ADMIN_INITIAL_PASSWORD_FILENAME
    )


def _write_initial_admin_password(password):
    """Ecrit le mot de passe genere une seule fois, en 0600."""
    chemin = _admin_initial_password_path()
    try:
        with open(chemin, "w", encoding="utf-8") as handle:
            handle.write(password + "\n")
        os.chmod(chemin, 0o600)
    except OSError:
        app.logger.error(
            "Impossible d'ecrire le mot de passe admin initial dans %s", chemin
        )
        return None
    return chemin


def _remove_initial_admin_password():
    try:
        os.remove(_admin_initial_password_path())
    except FileNotFoundError:
        pass
    except OSError:
        app.logger.warning("Impossible de supprimer le fichier de mot de passe initial")


# Version du schema, portee par la table `schema_version`. Chaque etape de
# `_SCHEMA_MIGRATIONS` est idempotente et s'execute dans l'ordre : une base
# ancienne (v2.1.4) les rejoue toutes une fois, une base a jour n'en rejoue
# aucune. Les tests `test_schema_migrations.py` verifient les deux cas.
SCHEMA_VERSION = 7


def _migrate_1_core_schema(db):
    """Tables de base, index et colonnes historiques (etat v2.0)."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS masters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pc_name TEXT NOT NULL,
        pc_name_key TEXT DEFAULT '',
        label TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_id INTEGER NOT NULL,
        scan_date TEXT NOT NULL,
        scan_date_iso TEXT DEFAULT '',
        content_hash TEXT DEFAULT '',
        os_info TEXT DEFAULT '',
        cpu_info TEXT DEFAULT '',
        ram_go REAL DEFAULT 0,
        fabricant TEXT DEFAULT '',
        num_serie TEXT DEFAULT '',
        domaine TEXT DEFAULT '',
        software_json TEXT DEFAULT '[]',
        disks_json TEXT DEFAULT '[]',
        network_json TEXT DEFAULT '[]',
        services_json TEXT DEFAULT '[]',
        warnings_json TEXT DEFAULT '[]',
        script_version TEXT DEFAULT '',
        scan_schema INTEGER DEFAULT 0,
        generated_at TEXT DEFAULT '',
        total_software INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (master_id) REFERENCES masters(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS software_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_id INTEGER NOT NULL,
        software_name TEXT NOT NULL,
        is_important INTEGER DEFAULT 0,
        note TEXT DEFAULT '',
        is_licensed INTEGER DEFAULT 0,
        license_type TEXT DEFAULT '',
        UNIQUE(master_id, software_name),
        FOREIGN KEY (master_id) REFERENCES masters(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS software_index (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        master_id INTEGER NOT NULL,
        software_name TEXT NOT NULL,
        software_version TEXT DEFAULT '',
        software_editor TEXT DEFAULT '',
        software_source TEXT DEFAULT '',
        software_category TEXT DEFAULT 'main',
        search_blob TEXT DEFAULT '',
        UNIQUE(snapshot_id, software_name, software_version, software_editor, software_source)
    );
    CREATE INDEX IF NOT EXISTS idx_software_index_snapshot ON software_index(snapshot_id);
    CREATE INDEX IF NOT EXISTS idx_software_index_master ON software_index(master_id);
    CREATE INDEX IF NOT EXISTS idx_software_index_name ON software_index(software_name);
    CREATE INDEX IF NOT EXISTS idx_software_index_version ON software_index(software_version);
    CREATE INDEX IF NOT EXISTS idx_software_index_editor ON software_index(software_editor);
    CREATE INDEX IF NOT EXISTS idx_software_index_blob ON software_index(search_blob);
    CREATE INDEX IF NOT EXISTS idx_snapshots_master_created ON snapshots(master_id, created_at DESC, id DESC);
    CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at DESC, id DESC);

    CREATE TABLE IF NOT EXISTS roadmap_minimal_pack (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        software_name TEXT NOT NULL UNIQUE,
        install_url TEXT DEFAULT '',
        note_text TEXT DEFAULT '',
        note TEXT DEFAULT '',
        is_licensed INTEGER DEFAULT 0,
        license_type TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS roadmap_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_id INTEGER NOT NULL,
        software_name TEXT NOT NULL,
        install_url TEXT DEFAULT '',
        note_text TEXT DEFAULT '',
        note TEXT DEFAULT '',
        is_licensed INTEGER DEFAULT 0,
        license_type TEXT DEFAULT '',
        is_done INTEGER DEFAULT 0,
        source TEXT DEFAULT 'custom',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(master_id, software_name),
        FOREIGN KEY (master_id) REFERENCES masters(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_roadmap_items_master ON roadmap_items(master_id);

    -- Suivi des logiciels absents d'un scan recent. Le snapshot reste la
    -- verite observee ; cette table porte les decisions et les taches.
    CREATE TABLE IF NOT EXISTS software_followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_id INTEGER NOT NULL,
        software_key TEXT NOT NULL,
        software_name TEXT NOT NULL,
        old_version TEXT DEFAULT '',
        old_editor TEXT DEFAULT '',
        old_source TEXT DEFAULT '',
        source_snapshot_id INTEGER,
        last_seen_snapshot_id INTEGER,
        last_seen_date TEXT DEFAULT '',
        last_missing_snapshot_id INTEGER,
        last_missing_date TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        decided_at TIMESTAMP,
        resolved_snapshot_id INTEGER,
        resolved_at TIMESTAMP,
        FOREIGN KEY (master_id) REFERENCES masters(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_software_followups_master_status
        ON software_followups(master_id, status);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_software_followups_active
        ON software_followups(master_id, software_key)
        WHERE status <> 'resolved';

    CREATE TABLE IF NOT EXISTS deleted_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL,
        deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        restored_at TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_deleted_items_pending
        ON deleted_items(restored_at, deleted_at DESC);
    """)


def _migrate_2_snapshot_dates(db):
    """Dates de scan comparables, index et vues latest_snapshots/snapshot_keys."""
    # ── Dates de scan comparables ────────────────────────────────────────────
    # `scan_date` reste la valeur affichee (jj/mm/aaaa) ; `scan_date_iso` sert
    # uniquement au tri et aux comparaisons. Les vues ci-dessous retombent sur
    # une conversion SQL pour les lignes anciennes ou inserees directement.
    snap_cols = [row[1] for row in db.execute("PRAGMA table_info(snapshots)").fetchall()]
    if "scan_date_iso" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN scan_date_iso TEXT DEFAULT ''")

    for row in db.execute(
        "SELECT id, scan_date, created_at, scan_date_iso FROM snapshots WHERE scan_date_iso IS NULL OR scan_date_iso = ''"
    ).fetchall():
        iso = _parse_scan_date_iso(row["scan_date"]) or _parse_scan_date_iso(row["created_at"])
        if iso:
            db.execute("UPDATE snapshots SET scan_date_iso = ? WHERE id = ?", (iso, row["id"]))

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_master_scandate "
        "ON snapshots(master_id, scan_date_iso DESC, id DESC)"
    )

    # Une seule definition du « dernier scan » pour toute l'application.
    # `snapshot_keys.scan_key` est une date ISO comparable, meme pour les
    # anciennes bases qui n'ont pas encore ete backfillees.
    db.executescript("""
    DROP VIEW IF EXISTS snapshot_keys;
    CREATE VIEW snapshot_keys AS
    SELECT
        id,
        master_id,
        COALESCE(
            NULLIF(scan_date_iso, ''),
            CASE
                WHEN scan_date GLOB '[0-3][0-9]/[0-1][0-9]/[0-9][0-9][0-9][0-9] [0-2][0-9]:[0-5][0-9]'
                    THEN substr(scan_date, 7, 4) || '-' || substr(scan_date, 4, 2) || '-' || substr(scan_date, 1, 2)
                         || ' ' || substr(scan_date, 12, 5) || ':00'
                WHEN scan_date GLOB '[0-3][0-9]/[0-1][0-9]/[0-9][0-9][0-9][0-9]'
                    THEN substr(scan_date, 7, 4) || '-' || substr(scan_date, 4, 2) || '-' || substr(scan_date, 1, 2)
                         || ' 00:00:00'
                WHEN scan_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9]:[0-5][0-9]'
                    THEN scan_date
                WHEN scan_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9]'
                    THEN scan_date || ':00'
                WHEN scan_date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'
                    THEN scan_date || ' 00:00:00'
                ELSE created_at
            END
        ) AS scan_key
    FROM snapshots;

    DROP VIEW IF EXISTS latest_snapshots;
    CREATE VIEW latest_snapshots AS
    SELECT s.*, k.scan_key AS scan_key
    FROM snapshots s
    JOIN snapshot_keys k ON k.id = s.id
    WHERE NOT EXISTS (
        SELECT 1
        FROM snapshot_keys k2
        WHERE k2.master_id = s.master_id
          AND (
              k2.scan_key > k.scan_key
              OR (k2.scan_key = k.scan_key AND k2.id > s.id)
          )
    );
    """)


def _migrate_3_master_identity(db):
    """Workflow inventory/preparation, cle de nom et colonnes ajoutees depuis v2.0."""
    # Migration idempotente: separation explicite des masters inventories et des listes de preparation.
    cols = [row[1] for row in db.execute("PRAGMA table_info(masters)").fetchall()]
    if "workflow_type" not in cols:
        db.execute("ALTER TABLE masters ADD COLUMN workflow_type TEXT DEFAULT 'inventory'")

    db.execute("CREATE INDEX IF NOT EXISTS idx_masters_workflow_pcname ON masters(workflow_type, pc_name)")

    db.execute("UPDATE masters SET workflow_type = 'inventory' WHERE workflow_type IS NULL OR workflow_type = ''")
    db.execute(
        """
        UPDATE masters
        SET workflow_type = 'preparation'
        WHERE id IN (SELECT DISTINCT master_id FROM roadmap_items)
          AND id NOT IN (SELECT DISTINCT master_id FROM snapshots)
        """
    )

    # ── Identite normalisee des masters ─────────────────────────────────────
    # Un meme PC ne doit pas creer trois fiches selon les espaces ou la casse.
    # La cle est calculee en Python (Unicode) puis stockee pour les requetes.
    master_cols = [row[1] for row in db.execute("PRAGMA table_info(masters)").fetchall()]
    snap_cols = [row[1] for row in db.execute("PRAGMA table_info(snapshots)").fetchall()]
    if "pc_name_key" not in master_cols:
        db.execute("ALTER TABLE masters ADD COLUMN pc_name_key TEXT DEFAULT ''")
    if "content_hash" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN content_hash TEXT DEFAULT ''")
    if "services_json" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN services_json TEXT DEFAULT '[]'")
    if "warnings_json" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN warnings_json TEXT DEFAULT '[]'")
    if "script_version" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN script_version TEXT DEFAULT ''")
    if "scan_schema" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN scan_schema INTEGER DEFAULT 0")
    if "generated_at" not in snap_cols:
        db.execute("ALTER TABLE snapshots ADD COLUMN generated_at TEXT DEFAULT ''")

    # L'index unique doit etre recree apres la fusion : sinon le backfill de
    # la deuxieme fiche homonyme echoue avant que les doublons soient fusionnes.
    db.execute("DROP INDEX IF EXISTS idx_masters_workflow_key")

    for row in db.execute("SELECT id, pc_name, pc_name_key FROM masters").fetchall():
        pc_name = str(row["pc_name"] or "").strip()
        key = _master_key(pc_name)
        if pc_name != row["pc_name"] or key != (row["pc_name_key"] or ""):
            db.execute(
                "UPDATE masters SET pc_name = ?, pc_name_key = ? WHERE id = ?",
                (pc_name, key, row["id"]),
            )

    # Migration: ensure software_category column exists (idempotent)
    try:
        db.execute("SELECT software_category FROM software_index LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE software_index ADD COLUMN software_category TEXT DEFAULT 'main'")

    # Migration idempotente: metadonnees licence sur software_flags.
    sflag_cols = [row[1] for row in db.execute("PRAGMA table_info(software_flags)").fetchall()]
    if "is_licensed" not in sflag_cols:
        db.execute("ALTER TABLE software_flags ADD COLUMN is_licensed INTEGER DEFAULT 0")
    if "license_type" not in sflag_cols:
        db.execute("ALTER TABLE software_flags ADD COLUMN license_type TEXT DEFAULT ''")

    # Migration idempotente: pack minimal URL + note separee + licence.
    mp_cols = [row[1] for row in db.execute("PRAGMA table_info(roadmap_minimal_pack)").fetchall()]
    if "install_url" not in mp_cols:
        db.execute("ALTER TABLE roadmap_minimal_pack ADD COLUMN install_url TEXT DEFAULT ''")
    if "note_text" not in mp_cols:
        db.execute("ALTER TABLE roadmap_minimal_pack ADD COLUMN note_text TEXT DEFAULT ''")
    if "is_licensed" not in mp_cols:
        db.execute("ALTER TABLE roadmap_minimal_pack ADD COLUMN is_licensed INTEGER DEFAULT 0")
    if "license_type" not in mp_cols:
        db.execute("ALTER TABLE roadmap_minimal_pack ADD COLUMN license_type TEXT DEFAULT ''")

    # Migration idempotente: checklist master URL + note separee + licence.
    ri_cols = [row[1] for row in db.execute("PRAGMA table_info(roadmap_items)").fetchall()]
    if "install_url" not in ri_cols:
        db.execute("ALTER TABLE roadmap_items ADD COLUMN install_url TEXT DEFAULT ''")
    if "note_text" not in ri_cols:
        db.execute("ALTER TABLE roadmap_items ADD COLUMN note_text TEXT DEFAULT ''")
    if "is_licensed" not in ri_cols:
        db.execute("ALTER TABLE roadmap_items ADD COLUMN is_licensed INTEGER DEFAULT 0")
    if "license_type" not in ri_cols:
        db.execute("ALTER TABLE roadmap_items ADD COLUMN license_type TEXT DEFAULT ''")

    # Backfill: si l'ancien champ note etait utilise, on le copie dans note_text.
    db.execute("UPDATE roadmap_minimal_pack SET note_text = note WHERE (note_text IS NULL OR note_text = '') AND note IS NOT NULL AND note <> ''")
    db.execute("UPDATE roadmap_items SET note_text = note WHERE (note_text IS NULL OR note_text = '') AND note IS NOT NULL AND note <> ''")


def _migrate_4_master_merge(db):
    """Fusion des doublons de masters homonymes et index unique."""
    # Fusion des doublons deja presents : on garde la fiche la plus ancienne,
    # puis on deplace snapshots, flags et checklist avant de supprimer l'autre.
    # Ce bloc est execute apres toutes les migrations de colonnes pour rester
    # compatible avec les anciennes bases.
    duplicate_groups = db.execute(
        """
        SELECT workflow_type, pc_name_key, MIN(id) AS keep_id
        FROM masters
        WHERE pc_name_key <> ''
        GROUP BY workflow_type, pc_name_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for group in duplicate_groups:
        keep_id = group["keep_id"]
        duplicate_ids = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM masters WHERE workflow_type = ? AND pc_name_key = ? AND id <> ?",
                (group["workflow_type"], group["pc_name_key"], keep_id),
            ).fetchall()
        ]
        for duplicate_id in duplicate_ids:
            db.execute("UPDATE snapshots SET master_id = ? WHERE master_id = ?", (keep_id, duplicate_id))
            db.execute(
                """
                INSERT OR IGNORE INTO software_flags
                    (master_id, software_name, is_important, note, is_licensed, license_type)
                SELECT ?, software_name, is_important, note, is_licensed, license_type
                FROM software_flags WHERE master_id = ?
                """,
                (keep_id, duplicate_id),
            )
            db.execute("DELETE FROM software_flags WHERE master_id = ?", (duplicate_id,))
            db.execute(
                """
                INSERT OR IGNORE INTO roadmap_items
                    (master_id, software_name, install_url, note_text, note,
                     is_licensed, license_type, is_done, source, created_at)
                SELECT ?, software_name, install_url, note_text, note,
                       is_licensed, license_type, is_done, source, created_at
                FROM roadmap_items WHERE master_id = ?
                """,
                (keep_id, duplicate_id),
            )
            db.execute("DELETE FROM roadmap_items WHERE master_id = ?", (duplicate_id,))
            db.execute("DELETE FROM masters WHERE id = ?", (duplicate_id,))

    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_masters_workflow_key "
        "ON masters(workflow_type, pc_name_key) WHERE pc_name_key <> ''"
    )


def _migrate_5_catalogue_coverage(db):
    """Tables du portail, alias et cle normalisee de couverture logicielle."""
    # ─── NOUVELLES TABLES : portail demandes logiciels ───────────────────────

    db.executescript("""
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS app_meta (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS logiciels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL UNIQUE,
        categorie TEXT DEFAULT '',
        description TEXT DEFAULT '',
        dans_pack_base INTEGER DEFAULT 0,
        actif INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS logiciel_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logiciel_id INTEGER NOT NULL,
        numero_version TEXT NOT NULL,
        type_licence TEXT DEFAULT '',
        gratuit_education INTEGER DEFAULT 1,
        url_download TEXT DEFAULT '',
        commentaires TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (logiciel_id) REFERENCES logiciels(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS logiciel_plugins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logiciel_id INTEGER NOT NULL,
        nom TEXT NOT NULL,
        optionnel INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (logiciel_id) REFERENCES logiciels(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS logiciel_guides (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logiciel_id INTEGER NOT NULL UNIQUE,
        guide_markdown TEXT DEFAULT '',
        comptes_info TEXT DEFAULT '',
        troubleshooting_json TEXT DEFAULT '[]',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (logiciel_id) REFERENCES logiciels(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS logiciel_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        logiciel_id INTEGER NOT NULL,
        alias_nom TEXT NOT NULL,
        alias_norm TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(logiciel_id, alias_norm),
        FOREIGN KEY (logiciel_id) REFERENCES logiciels(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_logiciel_aliases_norm ON logiciel_aliases(alias_norm);

    CREATE TABLE IF NOT EXISTS prof_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        nom TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        actif INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS demandes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prof_token_id INTEGER NOT NULL,
        logiciel_id INTEGER NOT NULL,
        version_id INTEGER,
        precisions TEXT DEFAULT '',
        statut TEXT DEFAULT 'Demandé',
        notes_internes TEXT DEFAULT '',
        date_demande TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        date_installation TIMESTAMP,
        FOREIGN KEY (prof_token_id) REFERENCES prof_tokens(id) ON DELETE CASCADE,
        FOREIGN KEY (logiciel_id) REFERENCES logiciels(id) ON DELETE CASCADE,
        FOREIGN KEY (version_id) REFERENCES logiciel_versions(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS demande_plugins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        demande_id INTEGER NOT NULL,
        plugin_id INTEGER NOT NULL,
        UNIQUE(demande_id, plugin_id),
        FOREIGN KEY (demande_id) REFERENCES demandes(id) ON DELETE CASCADE,
        FOREIGN KEY (plugin_id) REFERENCES logiciel_plugins(id) ON DELETE CASCADE
    );
    """)

    # Migration: cle normalisee des noms pour la couverture logicielle.
    si_cols = [row[1] for row in db.execute("PRAGMA table_info(software_index)").fetchall()]
    if "software_name_norm" not in si_cols:
        db.execute(
            "ALTER TABLE software_index ADD COLUMN software_name_norm TEXT DEFAULT ''"
        )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_software_index_name_norm "
        "ON software_index(software_name_norm)"
    )
    rows = db.execute(
        "SELECT id, software_name FROM software_index "
        "WHERE COALESCE(software_name_norm, '') = ''"
    ).fetchall()
    if rows:
        db.executemany(
            "UPDATE software_index SET software_name_norm = ? WHERE id = ?",
            [
                (_normalize_software_name(row["software_name"]), row["id"])
                for row in rows
            ],
        )


def _migrate_6_audit_log(db):
    """Journal des ecritures : qui a change quoi, quand et depuis quelle IP."""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            username TEXT NOT NULL DEFAULT '',
            user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_id INTEGER,
            target_label TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_audit_log_created
            ON audit_log(created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_log_action
            ON audit_log(action, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_log_user
            ON audit_log(username, created_at DESC);
        """
    )


def _migrate_7_admin_roles(db):
    """Role des comptes : `admin` (tout) ou `technicien` (inventaire seulement)."""
    cols = [row[1] for row in db.execute("PRAGMA table_info(admin_users)").fetchall()]
    if "role" not in cols:
        db.execute(
            "ALTER TABLE admin_users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'"
        )


_SCHEMA_MIGRATIONS = [
    (1, "tables de base", _migrate_1_core_schema),
    (2, "dates de scan", _migrate_2_snapshot_dates),
    (3, "identite des masters", _migrate_3_master_identity),
    (4, "fusion des doublons", _migrate_4_master_merge),
    (5, "catalogue et couverture", _migrate_5_catalogue_coverage),
    (6, "journal d'audit", _migrate_6_audit_log),
    (7, "rôles des comptes", _migrate_7_admin_roles),
]

assert SCHEMA_VERSION == _SCHEMA_MIGRATIONS[-1][0]


def _apply_schema_migrations(db):
    """Applique dans l'ordre les migrations non encore enregistrees.

    Une etape est enregistree dans `schema_version` juste apres son execution :
    si une migration ulterieure echoue, les precedentes restent acquises et la
    suivante reprend au bon endroit. Chaque etape etant idempotente, rejouer
    une base ancienne sans table `schema_version` reste sans effet de bord.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        row["version"] for row in db.execute("SELECT version FROM schema_version")
    }
    for version, name, migration in _SCHEMA_MIGRATIONS:
        if version in applied:
            continue
        migration(db)
        db.execute(
            "INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)",
            (version, name),
        )
        db.commit()


def _seed_initial_data(db):
    # Seed initial unique. Le marqueur evite qu'un admin ou un logiciel du pack
    # supprime volontairement reapparaisse au prochain demarrage.
    seed_done = db.execute(
        "SELECT value FROM app_meta WHERE key = 'initial_seed'"
    ).fetchone()
    if not seed_done:
        existing_admin = db.execute("SELECT id FROM admin_users WHERE username = 'admin'").fetchone()
        if not existing_admin:
            # Pas de compte admin/admin : mot de passe fourni par
            # ADMIN_PASSWORD, sinon valeur aleatoire ecrite une seule fois dans
            # un fichier reserve au proprietaire.
            admin_password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
            generated = not admin_password
            if generated:
                admin_password = secrets.token_urlsafe(12)
            db.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash(admin_password))
            )
            if generated:
                chemin = _write_initial_admin_password(admin_password)
                if chemin:
                    app.logger.warning(
                        "Mot de passe admin initial genere : voir %s", chemin
                    )

        pack_base = [
            ("VLC media player", "Multimédia", "Lecteur multimédia universel", 1),
            ("7-Zip", "Utilitaires", "Archiveur de fichiers gratuit", 1),
            ("SumatraPDF", "Bureautique", "Lecteur PDF léger et rapide", 1),
        ]
        for nom, cat, desc, pack in pack_base:
            db.execute(
                "INSERT OR IGNORE INTO logiciels (nom, categorie, description, dans_pack_base) VALUES (?, ?, ?, ?)",
                (nom, cat, desc, pack)
            )
        db.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('initial_seed', '1')"
        )



def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        # Certains montages reseau ne supportent pas WAL : on reste en mode par defaut.
        pass
    _apply_schema_migrations(db)
    _seed_initial_data(db)
    db.commit()
    db.close()

init_db()



def _load_secret_key():
    """Cle de session : environnement sain, sinon cle aleatoire persistee.

    Une cle par defaut connue permettrait de forger un cookie admin ; on la
    remplace par une valeur aleatoire stockee en base pour survivre aux
    redemarrages sans dependre d'une variable d'environnement.
    """
    env_key = (os.environ.get("SECRET_KEY") or "").strip()
    if _is_strong_secret_key(env_key):
        return env_key
    if env_key:
        app.logger.warning(
            "SECRET_KEY fournie mais trop courte ou faible : elle est ignoree."
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'secret_key'"
        ).fetchone()
        if row and row["value"]:
            return row["value"]
        generated = secrets.token_hex(32)
        # Plusieurs workers peuvent demarrer ensemble : le premier insere sa
        # cle, les suivants relisent celle qui est persistee. Sinon chaque
        # worker signerait les sessions avec une cle differente.
        conn.execute(
            "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('secret_key', ?)",
            (generated,),
        )
        conn.commit()
        stored = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'secret_key'"
        ).fetchone()
        app.logger.warning(
            "SECRET_KEY exploitable absente : cle aleatoire persistee dans la base."
        )
        return stored["value"] if stored else generated
    finally:
        conn.close()


app.secret_key = _load_secret_key()


# ─── RÔLES ET DÉCORATEUR AUTH ADMIN ──────────────────────────────────────────
# Deux rôles : `admin` (tout) et `technicien` (inventaire seulement). Les
# écritures d'inventaire restent ouvertes à tout compte connecté (voir
# `_require_admin_for_writes`) ; tout l'espace /admin — catalogue, paramètres,
# professeurs, demandes, journal — est réservé au rôle administrateur.

ROLE_ADMIN = "admin"
ROLE_TECHNICIEN = "technicien"


def _current_role():
    """Rôle de la session ; une session ouverte avant le rôle vaut admin."""
    return session.get("admin_role") or ROLE_ADMIN


def roles_required(*roles):
    """Session obligatoire, puis contrôle du rôle (403 hors des rôles donnés)."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('admin_logged_in'):
                return redirect(url_for('admin_login'))
            if _current_role() not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


# Catalogue, paramètres, professeurs, demandes, journal : administrateur seul.
admin_required = roles_required(ROLE_ADMIN)
# Gestes d'inventaire rangés dans /admin (corbeille, restauration) : les deux rôles.
staff_required = roles_required(ROLE_ADMIN, ROLE_TECHNICIEN)


def _get_admin_user():
    """Retourne le nom d'utilisateur admin connecté (pour affichage)."""
    return session.get('admin_username', 'admin')


@app.context_processor
def _inject_role():
    """Expose le rôle aux templates : la décision d'accès reste côté serveur."""
    logged_in = bool(session.get('admin_logged_in'))
    role = _current_role()
    return {
        "is_admin": logged_in and role == ROLE_ADMIN,
        "is_technicien": logged_in and role == ROLE_TECHNICIEN,
    }


@app.errorhandler(403)
def forbidden(error):
    """Un technicien reste hors du catalogue, des paramètres et des professeurs."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Action réservée aux administrateurs"}), 403
    return render_template("403.html"), 403


# ─── JOURNAL D'AUDIT ─────────────────────────────────────────────────────────
# Chaque ecriture journalisee garde l'auteur, l'action, la cible et un resume.
# Les notes de licence et les mots de passe ne sont jamais recopies : les
# resumes ne contiennent que des faits (booleens, types, compteurs).

AUDIT_RETENTION_DEFAULT_DAYS = 365
AUDIT_RETENTION_MAX_DAYS = 3650
AUDIT_PAGE_SIZE = 100
AUDIT_ACTION_LABELS = {
    "import": "Import d'inventaire",
    "master_create": "Création de master",
    "master_update": "Nouveau scan",
    "master_edit": "Modification de master",
    "master_delete": "Suppression de master",
    "snapshot_delete": "Suppression de scan",
    "trash_restore": "Restauration",
    "trash_purge": "Purge de la corbeille",
    "flag": "Flag logiciel",
    "roadmap_add": "Ajout checklist",
    "roadmap_update": "Modification checklist",
    "roadmap_toggle": "Cochage checklist",
    "roadmap_delete": "Suppression checklist",
    "roadmap_pack": "Application du pack minimal",
    "pack_add": "Ajout au pack minimal",
    "pack_update": "Modification du pack minimal",
    "pack_delete": "Retrait du pack minimal",
    "followup": "Décision d'absence",
    "settings": "Paramètres",
}


def _audit_retention_days(db):
    """Jours de conservation du journal ; 0 signifie « sans limite »."""
    row = db.execute(
        "SELECT value FROM app_meta WHERE key = 'audit_retention_days'"
    ).fetchone()
    try:
        days = int(row["value"]) if row else AUDIT_RETENTION_DEFAULT_DAYS
    except (TypeError, ValueError):
        days = AUDIT_RETENTION_DEFAULT_DAYS
    if days < 0:
        return AUDIT_RETENTION_DEFAULT_DAYS
    return days


def _purge_audit_log(db):
    """Supprime les entrees plus anciennes que la retention configuree."""
    days = _audit_retention_days(db)
    if days == 0:
        return 0
    cur = db.execute(
        "DELETE FROM audit_log WHERE created_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    return cur.rowcount or 0


def _audit(db, action, target_type="", target_id=None, target_label="", summary=""):
    """Ajoute une entree au journal ; l'appelant valide avec son commit."""
    _purge_audit_log(db)
    db.execute(
        """
        INSERT INTO audit_log
        (username, user_id, action, target_type, target_id, target_label, summary, ip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.get("admin_username") or "",
            session.get("admin_user_id"),
            action,
            target_type or "",
            target_id,
            (target_label or "")[:200],
            (summary or "")[:500],
            (request.remote_addr or "")[:45],
        ),
    )


def _audit_filter_args(args):
    """Traduit les filtres de la page en clauses SQL parametrees."""
    user = (args.get("user") or "").strip()
    action = (args.get("action") or "").strip()
    date_from = (args.get("from") or "").strip()
    date_to = (args.get("to") or "").strip()
    where, params = [], []
    if user:
        where.append("username = ?")
        params.append(user)
    if action in AUDIT_ACTION_LABELS:
        where.append("action = ?")
        params.append(action)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from):
        where.append("date(created_at) >= date(?)")
        params.append(date_from)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to):
        where.append("date(created_at) <= date(?)")
        params.append(date_to)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params, {
        "user": user,
        "action": action if action in AUDIT_ACTION_LABELS else "",
        "from": date_from if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from) else "",
        "to": date_to if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to) else "",
    }


# ─── PROTECTION DES ÉCRITURES ────────────────────────────────────────────────
# Toute ecriture exige une session admin ; les lectures restent publiques.
# Le portail professeur (token dedie) et la comparaison (POST en lecture seule)
# gardent leur acces propre.
_PUBLIC_WRITE_ENDPOINTS = {
    "admin_login",
    "api_compare",
    "api_prof_creer_demande",
    "api_prof_annuler_demande",
}


@app.before_request
def _require_admin_for_writes():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.endpoint is None or request.endpoint in _PUBLIC_WRITE_ENDPOINTS:
        return None
    if session.get("admin_logged_in"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentification admin requise"}), 401
    flash("Connectez-vous pour effectuer cette action.", "error")
    return redirect(url_for("admin_login"))


# ─── HELPERS DEMANDES ─────────────────────────────────────────────────────────

STATUTS_DEMANDE = ['Demandé', 'Validé', 'En cours', 'Installé', 'Refusé']
STATUT_BADGE = {
    'Demandé': 'warning',
    'Validé': 'info',
    'En cours': 'primary',
    'Installé': 'success',
    'Refusé': 'danger',
}


def _logiciel_full(db, logiciel_id):
    """Retourne logiciel + ses versions + plugins + guide."""
    log = db.execute("SELECT * FROM logiciels WHERE id = ?", (logiciel_id,)).fetchone()
    if not log:
        return None
    versions = db.execute(
        "SELECT * FROM logiciel_versions WHERE logiciel_id = ? ORDER BY id DESC", (logiciel_id,)
    ).fetchall()
    plugins = db.execute(
        "SELECT * FROM logiciel_plugins WHERE logiciel_id = ? ORDER BY nom", (logiciel_id,)
    ).fetchall()
    guide = db.execute(
        "SELECT * FROM logiciel_guides WHERE logiciel_id = ?", (logiciel_id,)
    ).fetchone()
    return dict(log), [dict(v) for v in versions], [dict(p) for p in plugins], dict(guide) if guide else None


def _demande_detail(db, demande_id):
    row = db.execute("""
        SELECT d.*, pt.email AS prof_email, pt.nom AS prof_nom,
               l.nom AS logiciel_nom, l.categorie AS logiciel_categorie,
               lv.numero_version
        FROM demandes d
        JOIN prof_tokens pt ON pt.id = d.prof_token_id
        JOIN logiciels l ON l.id = d.logiciel_id
        LEFT JOIN logiciel_versions lv ON lv.id = d.version_id
        WHERE d.id = ?
    """, (demande_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    plugins = db.execute("""
        SELECT lp.nom, lp.optionnel
        FROM demande_plugins dp
        JOIN logiciel_plugins lp ON lp.id = dp.plugin_id
        WHERE dp.demande_id = ?
    """, (demande_id,)).fetchall()
    d['plugins'] = [dict(p) for p in plugins]
    coverage = _coverage_for_logiciels(
        db, [{"id": d["logiciel_id"], "nom": d["logiciel_nom"]}]
    )
    d['masters_coverage'] = coverage.get(d['logiciel_id'], 0)
    return d


def _is_token_identifier(norm):
    """Identifiant court (KB...) qui doit matcher comme jeton exact."""
    return bool(re.fullmatch(r"kb\d+", norm))


def _matching_terms_sql(terms):
    """Clause SQL pour une cle normalisee exacte ou un identifiant jeton."""
    exact = sorted(term for term in terms if term and not _is_token_identifier(term))
    tokens = sorted(term for term in terms if _is_token_identifier(term))
    clauses = []
    params = []
    if exact:
        clauses.append(
            "si.software_name_norm IN ({})".format(
                ",".join("?" for _ in exact)
            )
        )
        params.extend(exact)
    for term in tokens:
        clauses.append("instr(' ' || si.software_name_norm || ' ', ?) > 0")
        params.append(" {} ".format(term))
    return "(" + " OR ".join(clauses) + ")", params


def _coverage_terms(db, logiciels_list):
    """{logiciel_id: cles normalisees du nom et des alias}."""
    if not logiciels_list:
        return {}
    aliases = defaultdict(set)
    for row in db.execute(
        "SELECT logiciel_id, alias_norm FROM logiciel_aliases"
    ).fetchall():
        if row["alias_norm"]:
            aliases[row["logiciel_id"]].add(row["alias_norm"])

    terms_by_id = {}
    for logiciel in logiciels_list:
        norm = _normalize_software_name(logiciel["nom"])
        terms_by_id[logiciel["id"]] = (
            ({norm} if norm else set())
            | aliases.get(logiciel["id"], set())
        )
    return terms_by_id


def _terms_match(terms, present, present_tokens):
    """Vrai si un des termes couvre un nom du dernier scan."""
    return any(
        term in present
        or (_is_token_identifier(term) and term in present_tokens)
        for term in terms
    )


def _software_masters_count(db, logiciel_nom):
    """Nombre de masters inventory ayant ce logiciel dans leur dernier scan.

    Comparaison sur la cle normalisee ou un alias explicite, jamais sur une
    sous-chaine : « Java » ne compte plus « JavaScript ».
    """
    norm = _normalize_software_name(logiciel_nom)
    if not norm:
        return 0
    logiciels = [
        row
        for row in db.execute("SELECT id, nom FROM logiciels").fetchall()
        if _normalize_software_name(row["nom"]) == norm
    ]
    terms = {norm}
    for logiciel_terms in _coverage_terms(db, logiciels).values():
        terms |= logiciel_terms
    where, params = _matching_terms_sql(terms)
    result = db.execute(
        f"""
        SELECT COUNT(DISTINCT si.master_id) AS c
        FROM software_index si
        JOIN latest_snapshots ls ON ls.id = si.snapshot_id
        JOIN masters m ON m.id = si.master_id
        WHERE m.workflow_type = 'inventory' AND {where}
        """,
        tuple(params),
    ).fetchone()
    return result['c'] if result else 0


def _coverage_for_logiciels(db, logiciels_list):
    """Retourne {logiciel_id: masters_count} avec une seule requete de paires."""
    coverage = {l["id"]: 0 for l in logiciels_list}
    terms_by_id = _coverage_terms(db, logiciels_list)
    all_terms = set().union(*terms_by_id.values()) if terms_by_id else set()
    if not all_terms:
        return coverage

    where, params = _matching_terms_sql(all_terms)
    rows = db.execute(
        f"""
        SELECT DISTINCT si.software_name_norm AS norm, si.master_id
        FROM software_index si
        JOIN latest_snapshots ls ON ls.id = si.snapshot_id
        JOIN masters m ON m.id = si.master_id
        WHERE m.workflow_type = 'inventory' AND {where}
        """,
        tuple(params),
    ).fetchall()

    kb_terms = {term for term in all_terms if _is_token_identifier(term)}
    masters_by_term = defaultdict(set)
    for row in rows:
        norm = row["norm"]
        masters_by_term[norm].add(row["master_id"])
        for token in norm.split():
            if token in kb_terms:
                masters_by_term[token].add(row["master_id"])

    for logiciel_id, terms in terms_by_id.items():
        covered = set()
        for term in terms:
            covered |= masters_by_term.get(term, set())
        coverage[logiciel_id] = len(covered)
    return coverage


def _coverage_for_masters(db, masters, logiciels_list):
    """{master_id: nombre de logiciels du catalogue detectes}.

    Seuls les masters inventories comptent : une liste de preparation n'a pas
    de scan et ne doit pas gonfler les compteurs du catalogue.
    """
    counts = {m["id"]: 0 for m in masters}
    terms_by_id = _coverage_terms(db, logiciels_list)
    if not terms_by_id or not counts:
        return counts

    norms_by_master = defaultdict(set)
    for row in db.execute("""
        SELECT si.master_id, si.software_name_norm AS norm
        FROM software_index si
        JOIN latest_snapshots ls ON ls.id = si.snapshot_id
        JOIN masters m ON m.id = si.master_id
        WHERE m.workflow_type = 'inventory'
    """).fetchall():
        norms_by_master[row["master_id"]].add(row["norm"] or "")

    terms_list = list(terms_by_id.values())
    for master in masters:
        if master["workflow_type"] != "inventory":
            continue
        present = norms_by_master.get(master["id"], set())
        present_tokens = set()
        for norm in present:
            present_tokens.update(norm.split())
        counts[master["id"]] = sum(
            1
            for terms in terms_list
            if _terms_match(terms, present, present_tokens)
        )
    return counts


def _invalidate_counts_cache():
    _COUNTS_CACHE["expires_at"] = 0.0
    _COUNTS_CACHE["value"] = None


def _suite_counts(db):
    now = time.time()
    cached = _COUNTS_CACHE.get("value")
    if cached is not None and now < float(_COUNTS_CACHE.get("expires_at") or 0):
        return cached

    masters = db.execute(
        "SELECT COUNT(*) AS c FROM masters WHERE workflow_type = 'inventory'"
    ).fetchone()["c"]
    snapshots = db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"]
    value = (int(masters), int(snapshots))
    _COUNTS_CACHE["value"] = value
    _COUNTS_CACHE["expires_at"] = now + COUNTS_CACHE_TTL_SECONDS
    return value


@app.after_request
def add_fabsuite_cors_headers(response):
    """CORS restreint aux endpoints FabSuite uniquement."""
    if request.path.startswith("/api/fabsuite/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.before_request
def track_request_start():
    if _perf_logging_enabled():
        g._request_start = time.perf_counter()


@app.after_request
def log_request_duration(response):
    if _perf_logging_enabled() and hasattr(g, "_request_start"):
        elapsed_ms = (time.perf_counter() - g._request_start) * 1000.0
        app.logger.info(
            "perf endpoint=%s method=%s status=%s duration_ms=%.2f",
            request.path,
            request.method,
            response.status_code,
            elapsed_ms,
        )
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION : PORTAIL DEMANDES LOGICIELS — ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(
            url_for(
                'admin_dashboard' if _current_role() == ROLE_ADMIN else 'index'
            )
        )
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_db()
        user = db.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            # Seul le rôle exact `admin` garde tous les droits : un rôle vide,
            # inconnu ou mal saisi retombe sur technicien (moindre privilège).
            role = ROLE_ADMIN if user["role"] == ROLE_ADMIN else ROLE_TECHNICIEN
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_user_id'] = user['id']
            session['admin_role'] = role
            return redirect(
                url_for('admin_dashboard' if role == ROLE_ADMIN else 'index')
            )
        error = "Identifiants incorrects."
    return render_template("admin/login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


@app.route("/admin/")
@admin_required
def admin_dashboard():
    db = get_db()
    default_pw = db.execute(
        "SELECT value FROM app_meta WHERE key = 'admin_password_is_default'"
    ).fetchone()
    default_password_warning = bool(default_pw and default_pw['value'] == '1')
    # ── Demandes ──
    statuts = db.execute(
        "SELECT statut, COUNT(*) AS total FROM demandes GROUP BY statut"
    ).fetchall()
    statuts_dict = {r['statut']: r['total'] for r in statuts}
    total_demandes = sum(statuts_dict.values())

    top_logiciels = db.execute("""
        SELECT l.nom, COUNT(d.id) AS nb
        FROM demandes d
        JOIN logiciels l ON l.id = d.logiciel_id
        GROUP BY d.logiciel_id
        ORDER BY nb DESC
        LIMIT 8
    """).fetchall()

    recentes = db.execute("""
        SELECT d.id, d.statut, d.date_demande, pt.nom AS prof_nom, l.nom AS logiciel_nom
        FROM demandes d
        JOIN prof_tokens pt ON pt.id = d.prof_token_id
        JOIN logiciels l ON l.id = d.logiciel_id
        ORDER BY d.date_demande DESC
        LIMIT 8
    """).fetchall()

    nb_logiciels = db.execute("SELECT COUNT(*) AS c FROM logiciels WHERE actif = 1").fetchone()['c']
    nb_profs = db.execute("SELECT COUNT(*) AS c FROM prof_tokens WHERE actif = 1").fetchone()['c']

    # ── Inventaire ──
    nb_masters = db.execute(
        "SELECT COUNT(*) AS c FROM masters WHERE workflow_type = 'inventory'"
    ).fetchone()['c']
    nb_snapshots = db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()['c']

    # Logiciels du catalogue déjà détectés sur au moins un master
    logiciels_actifs = db.execute("SELECT id, nom FROM logiciels WHERE actif=1").fetchall()
    coverage = _coverage_for_logiciels(db, logiciels_actifs)
    nb_couverts = sum(
        1 for l in logiciels_actifs if coverage.get(l['id'], 0) > 0
    )

    # Masters sans scan récent (>30 jours). On compare des dates ISO, jamais
    # le texte `jj/mm/aaaa` : le tri lexicographique donnait des faux positifs.
    cutoff_iso = _stale_cutoff_iso()
    masters_obsoletes = db.execute("""
        SELECT COUNT(*) AS c
        FROM masters m
        LEFT JOIN latest_snapshots ls ON ls.master_id = m.id
        WHERE m.workflow_type = 'inventory'
          AND (ls.id IS NULL OR ls.scan_key < ?)
    """, (cutoff_iso,)).fetchone()['c']

    return render_template("admin/dashboard.html",
        statuts_dict=statuts_dict,
        total_demandes=total_demandes,
        top_logiciels=top_logiciels,
        recentes=recentes,
        nb_logiciels=nb_logiciels,
        nb_profs=nb_profs,
        nb_masters=nb_masters,
        nb_snapshots=nb_snapshots,
        nb_couverts=nb_couverts,
        nb_catalogue=len(logiciels_actifs),
        masters_obsoletes=masters_obsoletes,
        default_password_warning=default_password_warning,
        admin_user=_get_admin_user(),
        STATUT_BADGE=STATUT_BADGE,
    )


@app.route("/admin/masters")
@admin_required
def admin_masters():
    db = get_db()
    nb_catalogue = db.execute("SELECT COUNT(*) AS c FROM logiciels WHERE actif=1").fetchone()['c']

    masters = db.execute("""
        SELECT m.id, m.pc_name, m.label, m.notes, m.workflow_type,
               ls.scan_date, ls.total_software, ls.os_info, ls.id AS snapshot_id,
               ls.scan_key
        FROM masters m
        LEFT JOIN latest_snapshots ls ON ls.master_id = m.id
        ORDER BY m.workflow_type, m.pc_name
    """).fetchall()

    logiciels_actifs = db.execute("SELECT id, nom FROM logiciels WHERE actif=1").fetchall()
    counts = _coverage_for_masters(db, masters, logiciels_actifs)

    cutoff_iso = _stale_cutoff_iso()
    result = []
    for m in masters:
        master = dict(m)
        master['nb_couverts'] = counts[m['id']]
        master['nb_catalogue'] = nb_catalogue
        master['is_stale'] = (not m['scan_key']) or m['scan_key'] < cutoff_iso
        result.append(master)

    return render_template("admin/masters.html",
        masters=result,
        nb_catalogue=nb_catalogue,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/demandes")
@admin_required
def admin_demandes():
    db = get_db()
    filtre_statut = request.args.get("statut") or ""
    filtre_logiciel = (request.args.get("logiciel") or "").strip()

    query = """
        SELECT d.id, d.statut, d.date_demande, d.date_installation, d.precisions,
               pt.nom AS prof_nom, pt.email AS prof_email,
               l.nom AS logiciel_nom, l.id AS logiciel_id,
               lv.numero_version
        FROM demandes d
        JOIN prof_tokens pt ON pt.id = d.prof_token_id
        JOIN logiciels l ON l.id = d.logiciel_id
        LEFT JOIN logiciel_versions lv ON lv.id = d.version_id
    """
    conditions, params = [], []
    if filtre_statut in STATUTS_DEMANDE:
        conditions.append("d.statut = ?")
        params.append(filtre_statut)
    if filtre_logiciel:
        conditions.append("l.nom LIKE ?")
        params.append(f"%{filtre_logiciel}%")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY d.date_demande DESC"

    demandes = [dict(r) for r in db.execute(query, params).fetchall()]
    logiciels = db.execute("SELECT id, nom FROM logiciels WHERE actif=1 ORDER BY nom").fetchall()

    return render_template("admin/demandes.html",
        demandes=demandes,
        logiciels=logiciels,
        STATUTS_DEMANDE=STATUTS_DEMANDE,
        STATUT_BADGE=STATUT_BADGE,
        filtre_statut=filtre_statut,
        filtre_logiciel=filtre_logiciel,
        admin_user=_get_admin_user(),
    )


@app.route("/api/admin/demandes/<int:demande_id>", methods=["PATCH"])
@admin_required
def api_admin_update_demande(demande_id):
    data = request.get_json(silent=True) or {}
    nouveau_statut = data.get("statut") or ""
    # Ne pas ecraser les notes quand le client ne les envoie pas.
    notes = data.get("notes_internes")

    if nouveau_statut and nouveau_statut not in STATUTS_DEMANDE:
        return jsonify({"error": "Statut invalide"}), 400
    if notes is not None and not isinstance(notes, str):
        return jsonify({"error": "Notes invalides"}), 400

    db = get_db()
    demande = db.execute("SELECT id FROM demandes WHERE id = ?", (demande_id,)).fetchone()
    if not demande:
        return jsonify({"error": "Demande introuvable"}), 404

    updates, params = [], []
    if nouveau_statut:
        updates.append("statut = ?")
        params.append(nouveau_statut)
        if nouveau_statut == "Installé":
            updates.append("date_installation = CURRENT_TIMESTAMP")
    if notes is not None:
        updates.append("notes_internes = ?")
        params.append(notes)

    if updates:
        params.append(demande_id)
        db.execute(f"UPDATE demandes SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/demandes/<int:demande_id>/details")
@admin_required
def api_admin_demande_details(demande_id):
    db = get_db()
    d = _demande_detail(db, demande_id)
    if not d:
        return jsonify({"error": "Introuvable"}), 404
    return jsonify(d)


@app.route("/admin/logiciels")
@admin_required
def admin_logiciels():
    db = get_db()
    logiciels = db.execute("SELECT * FROM logiciels ORDER BY nom").fetchall()
    nb_masters_total = db.execute(
        "SELECT COUNT(*) AS c FROM masters WHERE workflow_type='inventory'"
    ).fetchone()['c']
    aliases_by_logiciel = defaultdict(list)
    for row in db.execute(
        "SELECT * FROM logiciel_aliases ORDER BY alias_nom"
    ).fetchall():
        aliases_by_logiciel[row["logiciel_id"]].append(dict(row))

    result = []
    for log in logiciels:
        l = dict(log)
        l['versions'] = [dict(v) for v in db.execute(
            "SELECT * FROM logiciel_versions WHERE logiciel_id = ? ORDER BY id DESC", (l['id'],)
        ).fetchall()]
        l['plugins'] = [dict(p) for p in db.execute(
            "SELECT * FROM logiciel_plugins WHERE logiciel_id = ? ORDER BY nom", (l['id'],)
        ).fetchall()]
        l['nb_demandes'] = db.execute(
            "SELECT COUNT(*) AS c FROM demandes WHERE logiciel_id = ?", (l['id'],)
        ).fetchone()['c']
        l['nb_masters_total'] = nb_masters_total
        l['aliases'] = aliases_by_logiciel.get(l['id'], [])
        result.append(l)

    coverage = _coverage_for_logiciels(db, result)
    for l in result:
        l['nb_masters'] = coverage.get(l['id'], 0)

    categories = sorted(set(r['categorie'] for r in result if r['categorie']))
    return render_template("admin/logiciels.html",
        logiciels=result,
        categories=categories,
        nb_masters_total=nb_masters_total,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/logiciels/add", methods=["POST"])
@admin_required
def admin_logiciels_add():
    nom = (request.form.get("nom") or "").strip()
    categorie = (request.form.get("categorie") or "").strip()
    description = (request.form.get("description") or "").strip()
    dans_pack_base = 1 if request.form.get("dans_pack_base") else 0
    if not nom:
        flash("Le nom est obligatoire.", "error")
        return redirect(url_for('admin_logiciels'))
    db = get_db()
    try:
        db.execute(
            "INSERT INTO logiciels (nom, categorie, description, dans_pack_base) VALUES (?,?,?,?)",
            (nom, categorie, description, dans_pack_base)
        )
        db.commit()
        flash(f"Logiciel « {nom} » ajouté.", "success")
    except Exception:
        flash("Ce nom de logiciel existe déjà.", "error")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/edit", methods=["POST"])
@admin_required
def admin_logiciels_edit(logiciel_id):
    nom = (request.form.get("nom") or "").strip()
    categorie = (request.form.get("categorie") or "").strip()
    description = (request.form.get("description") or "").strip()
    dans_pack_base = 1 if request.form.get("dans_pack_base") else 0
    actif = 1 if request.form.get("actif") else 0
    if not nom:
        return jsonify({"error": "Nom obligatoire"}), 400
    db = get_db()
    db.execute(
        "UPDATE logiciels SET nom=?, categorie=?, description=?, dans_pack_base=?, actif=? WHERE id=?",
        (nom, categorie, description, dans_pack_base, actif, logiciel_id)
    )
    db.commit()
    flash(f"Logiciel « {nom} » mis à jour.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/delete", methods=["POST"])
@admin_required
def admin_logiciels_delete(logiciel_id):
    db = get_db()
    log = db.execute("SELECT nom FROM logiciels WHERE id=?", (logiciel_id,)).fetchone()
    if log:
        db.execute("DELETE FROM logiciels WHERE id=?", (logiciel_id,))
        db.commit()
        flash(f"Logiciel « {log['nom']} » supprimé.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/versions/add", methods=["POST"])
@admin_required
def admin_logiciels_version_add(logiciel_id):
    numero = (request.form.get("numero_version") or "").strip()
    type_licence = (request.form.get("type_licence") or "").strip()
    gratuit_education = 1 if request.form.get("gratuit_education") else 0
    url_download = (request.form.get("url_download") or "").strip()
    commentaires = (request.form.get("commentaires") or "").strip()
    if not numero:
        flash("Le numéro de version est obligatoire.", "error")
        return redirect(url_for('admin_logiciels'))
    db = get_db()
    db.execute(
        "INSERT INTO logiciel_versions (logiciel_id, numero_version, type_licence, gratuit_education, url_download, commentaires) VALUES (?,?,?,?,?,?)",
        (logiciel_id, numero, type_licence, gratuit_education, url_download, commentaires)
    )
    db.commit()
    flash(f"Version {numero} ajoutée.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/versions/<int:version_id>/delete", methods=["POST"])
@admin_required
def admin_logiciels_version_delete(logiciel_id, version_id):
    db = get_db()
    db.execute("DELETE FROM logiciel_versions WHERE id=? AND logiciel_id=?", (version_id, logiciel_id))
    db.commit()
    flash("Version supprimée.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/plugins/add", methods=["POST"])
@admin_required
def admin_logiciels_plugin_add(logiciel_id):
    nom = (request.form.get("nom") or "").strip()
    optionnel = 1 if request.form.get("optionnel") else 0
    if not nom:
        flash("Le nom du plugin est obligatoire.", "error")
        return redirect(url_for('admin_logiciels'))
    db = get_db()
    db.execute(
        "INSERT INTO logiciel_plugins (logiciel_id, nom, optionnel) VALUES (?,?,?)",
        (logiciel_id, nom, optionnel)
    )
    db.commit()
    flash(f"Plugin « {nom} » ajouté.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/plugins/<int:plugin_id>/delete", methods=["POST"])
@admin_required
def admin_logiciels_plugin_delete(logiciel_id, plugin_id):
    db = get_db()
    db.execute("DELETE FROM logiciel_plugins WHERE id=? AND logiciel_id=?", (plugin_id, logiciel_id))
    db.commit()
    flash("Plugin supprimé.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/logiciels/<int:logiciel_id>/aliases/add", methods=["POST"])
@admin_required
def admin_logiciels_alias_add(logiciel_id):
    alias_nom = (request.form.get("alias_nom") or "").strip()
    db = get_db()
    logiciel = db.execute(
        "SELECT id FROM logiciels WHERE id=?", (logiciel_id,)
    ).fetchone()
    if not logiciel:
        flash("Logiciel introuvable.", "error")
        return redirect(url_for('admin_logiciels'))
    alias_norm = _normalize_software_name(alias_nom)
    if not alias_norm:
        flash("L'alias est vide ou invalide.", "error")
        return redirect(url_for('admin_logiciels'))
    try:
        db.execute(
            "INSERT INTO logiciel_aliases (logiciel_id, alias_nom, alias_norm) "
            "VALUES (?,?,?)",
            (logiciel_id, alias_nom, alias_norm),
        )
        db.commit()
        flash(f"Alias « {alias_nom} » ajouté.", "success")
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Cet alias existe déjà pour ce logiciel.", "error")
    return redirect(url_for('admin_logiciels'))


@app.route(
    "/admin/logiciels/<int:logiciel_id>/aliases/<int:alias_id>/delete",
    methods=["POST"],
)
@admin_required
def admin_logiciels_alias_delete(logiciel_id, alias_id):
    db = get_db()
    db.execute(
        "DELETE FROM logiciel_aliases WHERE id=? AND logiciel_id=?",
        (alias_id, logiciel_id),
    )
    db.commit()
    flash("Alias supprimé.", "success")
    return redirect(url_for('admin_logiciels'))


@app.route("/admin/profs")
@admin_required
def admin_profs():
    db = get_db()
    profs = db.execute("SELECT * FROM prof_tokens ORDER BY nom").fetchall()
    result = []
    for p in profs:
        prof = dict(p)
        prof['nb_demandes'] = db.execute(
            "SELECT COUNT(*) AS c FROM demandes WHERE prof_token_id=?", (p['id'],)
        ).fetchone()['c']
        result.append(prof)
    return render_template("admin/profs.html",
        profs=result,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/profs/add", methods=["POST"])
@admin_required
def admin_profs_add():
    import uuid
    email = (request.form.get("email") or "").strip().lower()
    nom = (request.form.get("nom") or "").strip()
    if not email or not nom:
        flash("Email et nom sont obligatoires.", "error")
        return redirect(url_for('admin_profs'))
    token = str(uuid.uuid4())
    db = get_db()
    try:
        db.execute(
            "INSERT INTO prof_tokens (email, nom, token) VALUES (?,?,?)",
            (email, nom, token)
        )
        db.commit()
        lien = url_for('prof_catalogue', token=token, _external=True)
        flash(f"Professeur « {nom} » ajouté. Lien d'accès : {lien}", "success")
    except Exception:
        flash("Cet email est déjà enregistré.", "error")
    return redirect(url_for('admin_profs'))


@app.route("/admin/profs/<int:prof_id>/revoke", methods=["POST"])
@admin_required
def admin_profs_revoke(prof_id):
    db = get_db()
    db.execute("UPDATE prof_tokens SET actif=0 WHERE id=?", (prof_id,))
    db.commit()
    flash("Token révoqué.", "warning")
    return redirect(url_for('admin_profs'))


@app.route("/admin/profs/<int:prof_id>/activate", methods=["POST"])
@admin_required
def admin_profs_activate(prof_id):
    db = get_db()
    db.execute("UPDATE prof_tokens SET actif=1 WHERE id=?", (prof_id,))
    db.commit()
    flash("Token réactivé.", "success")
    return redirect(url_for('admin_profs'))


@app.route("/admin/profs/<int:prof_id>/regen", methods=["POST"])
@admin_required
def admin_profs_regen(prof_id):
    import uuid
    new_token = str(uuid.uuid4())
    db = get_db()
    db.execute("UPDATE prof_tokens SET token=?, actif=1 WHERE id=?", (new_token, prof_id))
    db.commit()
    lien = url_for('prof_catalogue', token=new_token, _external=True)
    flash(f"Nouveau lien généré : {lien}", "success")
    return redirect(url_for('admin_profs'))


@app.route("/admin/wiki")
@admin_required
def admin_wiki():
    db = get_db()
    logiciels = db.execute("SELECT id, nom FROM logiciels WHERE actif=1 ORDER BY nom").fetchall()
    logiciel_id = request.args.get("logiciel_id", type=int)
    guide = None
    logiciel_sel = None
    if logiciel_id:
        logiciel_sel = db.execute("SELECT * FROM logiciels WHERE id=?", (logiciel_id,)).fetchone()
        guide = db.execute("SELECT * FROM logiciel_guides WHERE logiciel_id=?", (logiciel_id,)).fetchone()
    return render_template("admin/wiki.html",
        logiciels=logiciels,
        logiciel_id=logiciel_id,
        logiciel_sel=logiciel_sel,
        guide=guide,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/wiki/<int:logiciel_id>/save", methods=["POST"])
@admin_required
def admin_wiki_save(logiciel_id):
    guide_markdown = request.form.get("guide_markdown") or ""
    comptes_info = request.form.get("comptes_info") or ""
    troubleshooting_json = request.form.get("troubleshooting_json") or "[]"
    db = get_db()
    existing = db.execute("SELECT id FROM logiciel_guides WHERE logiciel_id=?", (logiciel_id,)).fetchone()
    if existing:
        db.execute(
            "UPDATE logiciel_guides SET guide_markdown=?, comptes_info=?, troubleshooting_json=?, updated_at=CURRENT_TIMESTAMP WHERE logiciel_id=?",
            (guide_markdown, comptes_info, troubleshooting_json, logiciel_id)
        )
    else:
        db.execute(
            "INSERT INTO logiciel_guides (logiciel_id, guide_markdown, comptes_info, troubleshooting_json) VALUES (?,?,?,?)",
            (logiciel_id, guide_markdown, comptes_info, troubleshooting_json)
        )
    db.commit()
    flash("Guide sauvegardé.", "success")
    return redirect(url_for('admin_wiki', logiciel_id=logiciel_id))


@app.route("/admin/export-pdf")
@admin_required
def admin_export_pdf():
    """Page HTML pour impression PDF."""
    db = get_db()
    filtre_statut = request.args.get("statut") or "Validé"
    query = """
        SELECT d.id, d.statut, d.date_demande, d.precisions, d.notes_internes,
               pt.nom AS prof_nom, pt.email AS prof_email,
               l.nom AS logiciel_nom, l.categorie AS logiciel_categorie,
               lv.numero_version
        FROM demandes d
        JOIN prof_tokens pt ON pt.id = d.prof_token_id
        JOIN logiciels l ON l.id = d.logiciel_id
        LEFT JOIN logiciel_versions lv ON lv.id = d.version_id
    """
    params = []
    if filtre_statut in STATUTS_DEMANDE:
        query += " WHERE d.statut = ?"
        params.append(filtre_statut)
    query += " ORDER BY l.nom, pt.nom"
    demandes = [dict(r) for r in db.execute(query, params).fetchall()]
    return render_template("admin/export_pdf.html",
        demandes=demandes,
        filtre_statut=filtre_statut,
        STATUTS_DEMANDE=STATUTS_DEMANDE,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    db = get_db()
    error = None
    if request.method == "POST":
        old_password = request.form.get("old_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        user_id = session.get('admin_user_id')
        user = db.execute("SELECT * FROM admin_users WHERE id=?", (user_id,)).fetchone()
        if not user or not check_password_hash(user['password_hash'], old_password):
            error = "Mot de passe actuel incorrect."
        elif new_password != confirm:
            error = "Les mots de passe ne correspondent pas."
        elif len(new_password) < 6:
            error = "Le mot de passe doit faire au moins 6 caractères."
        else:
            db.execute(
                "UPDATE admin_users SET password_hash=? WHERE id=?",
                (generate_password_hash(new_password), user_id)
            )
            db.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) "
                "VALUES ('admin_password_is_default', '0')"
            )
            _audit(
                db,
                "settings",
                "utilisateur",
                user_id,
                session.get("admin_username") or "",
                "Mot de passe modifié",
            )
            db.commit()
            _remove_initial_admin_password()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for('admin_settings'))
    return render_template("admin/settings.html", error=error, admin_user=_get_admin_user())


# ─── JOURNAL D'AUDIT : CONSULTATION, EXPORT, RETENTION ───────────────────────

@app.route("/admin/journal", methods=["GET", "POST"])
@admin_required
def admin_audit_log():
    db = get_db()
    if request.method == "POST":
        raw = (request.form.get("retention_days") or "").strip()
        if not raw.isdigit() or int(raw) > AUDIT_RETENTION_MAX_DAYS:
            flash(
                f"Rétention invalide : 0 (illimité) à {AUDIT_RETENTION_MAX_DAYS} jours.",
                "error",
            )
        else:
            days = int(raw)
            db.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) "
                "VALUES ('audit_retention_days', ?)",
                (str(days),),
            )
            summary = "Rétention illimitée" if days == 0 else f"Rétention : {days} jour(s)"
            _audit(db, "settings", "journal", None, "Journal d'audit", summary)
            db.commit()
            flash("Rétention du journal mise à jour.", "success")
        return redirect(url_for("admin_audit_log"))

    clause, params, filters = _audit_filter_args(request.args)
    total = db.execute(
        "SELECT COUNT(*) AS c FROM audit_log" + clause, params
    ).fetchone()["c"]
    pages = max(1, (total + AUDIT_PAGE_SIZE - 1) // AUDIT_PAGE_SIZE)
    page = min(max(request.args.get("page", type=int) or 1, 1), pages)
    rows = db.execute(
        "SELECT * FROM audit_log"
        + clause
        + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (*params, AUDIT_PAGE_SIZE, (page - 1) * AUDIT_PAGE_SIZE),
    ).fetchall()
    entries = []
    for row in rows:
        entry = dict(row)
        entry["action_label"] = AUDIT_ACTION_LABELS.get(row["action"], row["action"])
        entries.append(entry)
    users = [
        row["username"]
        for row in db.execute(
            "SELECT DISTINCT username FROM audit_log ORDER BY username"
        ).fetchall()
    ]
    export_query = urlencode({key: value for key, value in filters.items() if value})
    return render_template(
        "admin/audit.html",
        entries=entries,
        total=total,
        page=page,
        pages=pages,
        users=users,
        actions=AUDIT_ACTION_LABELS,
        filters=filters,
        retention=_audit_retention_days(db),
        retention_max=AUDIT_RETENTION_MAX_DAYS,
        export_query=export_query,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/journal/export.csv")
@admin_required
def admin_audit_export():
    db = get_db()
    clause, params, _ = _audit_filter_args(request.args)
    rows = db.execute(
        "SELECT * FROM audit_log" + clause + " ORDER BY created_at ASC, id ASC",
        params,
    ).fetchall()
    data = [
        [
            row["created_at"],
            row["username"],
            AUDIT_ACTION_LABELS.get(row["action"], row["action"]),
            row["target_type"],
            row["target_label"],
            row["summary"],
            row["ip"],
        ]
        for row in rows
    ]
    return _csv_response(
        data,
        [
            "horodatage",
            "utilisateur",
            "action",
            "type_cible",
            "cible",
            "resume",
            "adresse_ip",
        ],
        "FabInventory-Journal-Audit.csv",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION : PORTAIL PROFESSEURS (accès via token UUID)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_prof_by_token(token):
    """Retourne le prof si son token est actif, sinon None."""
    db = get_db()
    prof = db.execute(
        "SELECT * FROM prof_tokens WHERE token=? AND actif=1", (token,)
    ).fetchone()
    return dict(prof) if prof else None


@app.route("/prof/<token>")
def prof_catalogue(token):
    prof = _get_prof_by_token(token)
    if not prof:
        return render_template("prof/token_invalide.html"), 403
    db = get_db()
    nb_masters_total = db.execute(
        "SELECT COUNT(*) AS c FROM masters WHERE workflow_type='inventory'"
    ).fetchone()['c']
    logiciels = db.execute(
        "SELECT * FROM logiciels WHERE actif=1 ORDER BY categorie, nom"
    ).fetchall()
    # Set des logiciels déjà demandés par ce prof et pas encore terminés
    demandes_en_cours = set(
        r['logiciel_id'] for r in db.execute("""
            SELECT logiciel_id FROM demandes
            WHERE prof_token_id=? AND statut NOT IN ('Installé','Refusé')
        """, (prof['id'],)).fetchall()
    )
    result = []
    for log in logiciels:
        l = dict(log)
        l['versions'] = [dict(v) for v in db.execute(
            "SELECT * FROM logiciel_versions WHERE logiciel_id=? ORDER BY id DESC", (l['id'],)
        ).fetchall()]
        l['plugins'] = [dict(p) for p in db.execute(
            "SELECT * FROM logiciel_plugins WHERE logiciel_id=? ORDER BY nom", (l['id'],)
        ).fetchall()]
        l['nb_masters_total'] = nb_masters_total
        l['demande_en_cours'] = l['id'] in demandes_en_cours
        result.append(l)

    coverage = _coverage_for_logiciels(db, result)
    for l in result:
        l['nb_masters'] = coverage.get(l['id'], 0)

    mes_demandes = db.execute("""
        SELECT d.id, d.statut, d.date_demande, d.precisions,
               l.nom AS logiciel_nom, lv.numero_version
        FROM demandes d
        JOIN logiciels l ON l.id = d.logiciel_id
        LEFT JOIN logiciel_versions lv ON lv.id = d.version_id
        WHERE d.prof_token_id = ?
        ORDER BY d.date_demande DESC
    """, (prof['id'],)).fetchall()

    categories = sorted(set(l['categorie'] for l in result if l['categorie']))
    return render_template("prof/catalogue.html",
        prof=prof,
        logiciels=result,
        categories=categories,
        mes_demandes=mes_demandes,
        STATUT_BADGE=STATUT_BADGE,
        token=token,
    )


@app.route("/api/prof/<token>/demandes", methods=["POST"])
def api_prof_creer_demande(token):
    prof = _get_prof_by_token(token)
    if not prof:
        return jsonify({"error": "Token invalide"}), 403

    data = request.get_json(silent=True) or {}
    logiciel_id = data.get("logiciel_id")
    version_id = data.get("version_id")
    precisions = (data.get("precisions") or "").strip()
    plugin_ids = data.get("plugin_ids") or []

    if not logiciel_id:
        return jsonify({"error": "logiciel_id obligatoire"}), 400

    db = get_db()
    log = db.execute("SELECT id FROM logiciels WHERE id=? AND actif=1", (logiciel_id,)).fetchone()
    if not log:
        return jsonify({"error": "Logiciel introuvable"}), 404

    # Validation version
    if version_id:
        ver = db.execute(
            "SELECT id FROM logiciel_versions WHERE id=? AND logiciel_id=?",
            (version_id, logiciel_id)
        ).fetchone()
        if not ver:
            version_id = None

    # Empêcher les doublons (même prof, même logiciel, demande en cours)
    existing = db.execute("""
        SELECT id FROM demandes
        WHERE prof_token_id=? AND logiciel_id=? AND statut NOT IN ('Installé','Refusé')
    """, (prof['id'], logiciel_id)).fetchone()
    if existing:
        return jsonify({"error": "Vous avez déjà une demande en cours pour ce logiciel."}), 409

    db.execute(
        "INSERT INTO demandes (prof_token_id, logiciel_id, version_id, precisions) VALUES (?,?,?,?)",
        (prof['id'], logiciel_id, version_id, precisions)
    )
    demande_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()['id']

    # Plugins sélectionnés
    for pid in plugin_ids:
        plug = db.execute(
            "SELECT id FROM logiciel_plugins WHERE id=? AND logiciel_id=?",
            (pid, logiciel_id)
        ).fetchone()
        if plug:
            db.execute(
                "INSERT OR IGNORE INTO demande_plugins (demande_id, plugin_id) VALUES (?,?)",
                (demande_id, pid)
            )
    db.commit()
    return jsonify({"ok": True, "demande_id": demande_id})


@app.route("/api/prof/<token>/demandes/<int:demande_id>/cancel", methods=["POST"])
def api_prof_annuler_demande(token, demande_id):
    prof = _get_prof_by_token(token)
    if not prof:
        return jsonify({"error": "Token invalide"}), 403
    db = get_db()
    demande = db.execute(
        "SELECT id, statut FROM demandes WHERE id=? AND prof_token_id=?",
        (demande_id, prof['id'])
    ).fetchone()
    if not demande:
        return jsonify({"error": "Demande introuvable"}), 404
    if demande['statut'] not in ('Demandé', 'Validé'):
        return jsonify({"error": "Cette demande ne peut plus être annulée."}), 400
    db.execute("DELETE FROM demandes WHERE id=?", (demande_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/prof/<token>/guide/<int:logiciel_id>")
def prof_guide(token, logiciel_id):
    prof = _get_prof_by_token(token)
    if not prof:
        return render_template("prof/token_invalide.html"), 403
    db = get_db()
    logiciel = db.execute("SELECT * FROM logiciels WHERE id=? AND actif=1", (logiciel_id,)).fetchone()
    if not logiciel:
        flash("Logiciel introuvable.", "error")
        return redirect(url_for('prof_catalogue', token=token))
    guide = db.execute("SELECT * FROM logiciel_guides WHERE logiciel_id=?", (logiciel_id,)).fetchone()
    return render_template("prof/guide.html",
        prof=prof,
        logiciel=logiciel,
        guide=guide,
        token=token,
    )


@app.route("/api/fabsuite/manifest")
def fabsuite_manifest():
    db = get_db()
    masters, snapshots = _suite_counts(db)
    return jsonify({
        "app": "fabinventory",
        "name": "FabInventory",
        "version": APP_VERSION,
        "suite_version": "1.0.0",
        "status": "running",
        "description": "Gestionnaire d'inventaire de masters informatique",
        "icon": "bi-pc-display",
        "color": "#0ea5e9",
        "capabilities": ["inventory", "stats"],
        "notifications_endpoint": "/api/fabsuite/notifications",
        "widgets": [
            {
                "id": "masters-count",
                "label": "Masters",
                "type": "counter",
                "endpoint": "/api/fabsuite/widget/masters-count",
                "refresh_interval": 120,
            },
            {
                "id": "snapshots-count",
                "label": "Snapshots",
                "type": "counter",
                "endpoint": "/api/fabsuite/widget/snapshots-count",
                "refresh_interval": 120,
            },
            {
                "id": "inventory-overview",
                "label": "Résumé inventaire",
                "type": "text",
                "endpoint": "/api/fabsuite/widget/inventory-overview",
                "refresh_interval": 180,
            },
        ],
        "meta": {
            "masters": masters,
            "snapshots": snapshots,
        },
    })


@app.route("/api/fabsuite/health")
def fabsuite_health():
    db = get_db()
    masters, snapshots = _suite_counts(db)
    return jsonify({
        "status": "ok",
        "app": "fabinventory",
        "timestamp": datetime.now().isoformat(),
        "stats": {
            "masters": masters,
            "snapshots": snapshots,
        },
    })


@app.route("/api/fabsuite/widget/masters-count")
def fabsuite_widget_masters_count():
    db = get_db()
    masters, _ = _suite_counts(db)
    return jsonify({
        "type": "counter",
        "value": masters,
        "label": "Masters inventoriés",
        "unit": "masters",
    })


@app.route("/api/fabsuite/widget/snapshots-count")
def fabsuite_widget_snapshots_count():
    db = get_db()
    _, snapshots = _suite_counts(db)
    return jsonify({
        "type": "counter",
        "value": snapshots,
        "label": "Snapshots enregistrés",
        "unit": "snapshots",
    })


@app.route("/api/fabsuite/widget/inventory-overview")
def fabsuite_widget_inventory_overview():
    db = get_db()
    masters, snapshots = _suite_counts(db)
    return jsonify({
        "type": "text",
        "title": "FabInventory",
        "content": f"{masters} master(s) et {snapshots} snapshot(s) gérés.",
    })


@app.route("/api/fabsuite/notifications")
def fabsuite_notifications():
    return jsonify({"notifications": []})


@app.route("/api/fabsuite/<path:subpath>", methods=["OPTIONS"])
def fabsuite_options(subpath):
    return ("", 204)


@app.route("/healthz")
def healthz():
    db = get_db()
    db.execute("SELECT 1").fetchone()
    return jsonify({"status": "ok"})


@app.route("/api/category-stats")
def api_category_stats():
    """Return category distribution statistics for dashboard"""
    db = get_db()
    # Ne compter que le dernier scan de chaque master : sinon chaque nouvel
    # import double artificiellement les categories du dashboard.
    stats = db.execute("""
        SELECT 
            si.software_category,
            COUNT(*) as count
        FROM software_index si
        JOIN latest_snapshots ls ON ls.id = si.snapshot_id
        GROUP BY si.software_category
        ORDER BY count DESC
    """).fetchall()
    
    result = {}
    total = 0
    for row in stats:
        cat = row["software_category"] or "main"
        count = row["count"]
        result[cat] = count
        total += count
    
    return jsonify({
        "categories": result,
        "total": total,
        "percentage": {cat: round(100 * count / total, 1) for cat, count in result.items()} if total > 0 else {}
    })

@app.route("/api/software")
def api_software_list():
    """List software with optional category and master filters"""
    category = request.args.get("category", "").strip().lower()
    master_id = request.args.get("master_id")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    # Validate category if provided
    if category and category not in ["main", "update", "composant", "doublon"]:
        return jsonify({"error": "Invalid category"}), 400
    if limit is None or not (1 <= limit <= 500):
        return jsonify({"error": "limit doit etre entre 1 et 500"}), 400
    if offset is None or offset < 0:
        return jsonify({"error": "offset doit etre positif ou nul"}), 400

    db = get_db()

    # Build query
    sql = "SELECT software_name, software_version, software_editor, software_source, software_category, master_id FROM software_index WHERE 1=1"
    params = []

    if category:
        sql += " AND software_category = ?"
        params.append(category)

    if master_id:
        sql += " AND master_id = ?"
        params.append(master_id)

    sql += " ORDER BY software_name ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(sql, params).fetchall()

    # Count total (without limit/offset)
    count_sql = "SELECT COUNT(*) as cnt FROM software_index WHERE 1=1"
    count_params = []
    if category:
        count_sql += " AND software_category = ?"
        count_params.append(category)
    if master_id:
        count_sql += " AND master_id = ?"
        count_params.append(master_id)

    total = db.execute(count_sql, count_params).fetchone()["cnt"]

    return jsonify({
        "software": [{
            "name": row["software_name"],
            "version": row["software_version"],
            "editor": row["software_editor"],
            "source": row["software_source"],
            "category": row["software_category"],
            "master_id": row["master_id"]
        } for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows)
    })


@app.route("/admin/reindex-software", methods=["POST"])
@admin_required
def admin_reindex_software():
    """Rebuild software index to recompute categories for existing snapshots."""
    db = get_db()
    _rebuild_software_index(db)
    db.commit()
    flash("Index logiciels reconstruit: categories recalculees", "success")
    return redirect(url_for("index"))


# ===== PARSER =====
def parse_inventory_html(html_content):
    """Extract inventory data from HTML file with embedded JSON."""
    match = re.search(
        r'<script\s+id=["\']inventoryData["\']\s+type=["\']application/json["\']>\s*(\{.*?\})\s*</script>',
        html_content, re.DOTALL
    )
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return data
    except json.JSONDecodeError:
        return None


def _strip_html_cell(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _extract_table_rows(section_html):
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", section_html, re.S | re.I):
        cells = [
            _strip_html_cell(cell)
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
        ]
        if cells:
            rows.append(cells)
    return rows


def _clean_value(value):
    return str(value or "").strip()


def _json_number(value, default=0.0):
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def _normalize_json_disks(items):
    """Normalise les disques du JSON schema 3 (ou renvoie None si absent)."""
    if not isinstance(items, list):
        return None
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = _clean_value(item.get("label") or item.get("name"))
        size = _json_number(item.get("size_go", item.get("size")))
        free = _json_number(item.get("free_go", item.get("free")))
        if not label and not size and not free:
            continue
        result.append({"label": label or "?", "size_go": round(size, 1), "free_go": round(free, 1)})
    return result


def _normalize_json_network(items):
    """Normalise le reseau du JSON schema 3 (ou renvoie None si absent)."""
    if not isinstance(items, list):
        return None
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_value(item.get("name") or item.get("interface"))
        ip = _clean_value(item.get("ip"))
        mac = _clean_value(item.get("mac"))
        if not name and not ip and not mac:
            continue
        entry = {"name": name or "?", "ip": ip, "mac": mac}
        for extra in ("dhcp", "gateway", "dns"):
            value = _clean_value(item.get(extra))
            if value:
                entry[extra] = value
        result.append(entry)
    return result


def _load_json_list(raw_value):
    """Charge une liste JSON stockee, sans laisser une ancienne valeur casser la page."""
    try:
        value = json.loads(raw_value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _scan_metadata(data):
    """Metadonnees de provenance d'un rapport, tolerantes aux anciens formats."""
    if not isinstance(data, dict):
        return {"warnings": [], "script_version": "", "schema": 0, "generated_at": ""}
    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings = [str(item).strip() for item in warnings if str(item or "").strip()]
    try:
        schema = int(data.get("schema") or 0)
    except (TypeError, ValueError):
        schema = 0
    return {
        "warnings": warnings,
        "script_version": str(data.get("script_version") or "").strip(),
        "schema": schema,
        "generated_at": str(data.get("generated_at") or "").strip(),
    }


def _script_version_key(value):
    """Transforme "3.1" ou "v3.1.0" en tuple comparable, ou () si inconnue."""
    parts = re.findall(r"\d+", str(value or ""))
    if not parts:
        return ()
    numbers = [int(part) for part in parts[:3]]
    return tuple(numbers + [0] * (3 - len(numbers)))


def _is_old_script_version(value):
    """Vrai si la version est absente ou anterieure a CURRENT_SCRIPT_VERSION."""
    found = _script_version_key(value)
    return not found or found < _script_version_key(CURRENT_SCRIPT_VERSION)


def _normalize_json_services(items):
    """Normalise les services du JSON schema 3."""
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _clean_value(item.get("n") or item.get("name") or item.get("DisplayName"))
        short_name = _clean_value(item.get("sc") or item.get("short_name") or item.get("Name"))
        if not name and not short_name:
            continue
        result.append({
            "n": name or short_name,
            "sc": short_name,
            "status": _clean_value(item.get("status") or item.get("State")),
            "start": _clean_value(item.get("start") or item.get("StartMode")),
        })
    return result


def extract_services(data):
    """Services automatiques du JSON enrichi (liste vide pour les anciens fichiers)."""
    if not isinstance(data, dict):
        return []
    return _normalize_json_services(data.get("services"))


def extract_disks_network(html_content, data=None):
    """Lit les disques/reseau du JSON enrichi, sinon des tableaux HTML historiques."""
    json_disks = _normalize_json_disks(data.get("disks")) if isinstance(data, dict) else None
    json_network = _normalize_json_network(data.get("network")) if isinstance(data, dict) else None

    def section(title_pattern):
        match = re.search(
            rf"<h2[^>]*>\s*{title_pattern}\s*</h2>(.*?)(?=<h2[^>]*>|</body>|$)",
            html_content,
            re.S | re.I,
        )
        return match.group(1) if match else ""

    disks = json_disks
    if disks is None:
        disks = []
        for cells in _extract_table_rows(section(r"Disques?")):
            if len(cells) < 3 or cells[0].lower() in ("lettre", "lecteur"):
                continue
            try:
                size = float(cells[1].replace("Go", "").replace(",", ".").strip())
                free = float(cells[2].replace("Go", "").replace(",", ".").strip())
            except ValueError:
                continue
            disks.append({"label": cells[0], "size_go": size, "free_go": free})

    network = json_network
    if network is None:
        network = []
        for cells in _extract_table_rows(section(r"R[eé]seau")):
            if len(cells) < 3 or cells[0].lower() == "carte":
                continue
            network.append({"name": cells[0], "ip": cells[1], "mac": cells[2]})

    return disks, network


def extract_system_info(html_content, data=None):
    """Lit le systeme du JSON enrichi, puis complete avec le HTML historique."""
    info = {
        "os": "",
        "windows_version": "",
        "cpu": "",
        "ram": 0,
        "fabricant": "",
        "num_serie": "",
        "domaine": "",
    }

    patterns = {
        "os": [r'<span class="label">OS</span>\s*<span>(.*?)</span>', r'"OS":\s*"(.*?)"'],
        "windows_version": [r'<span class="label">Version Windows</span>\s*<span>(.*?)</span>'],
        "cpu": [r'<span class="label">CPU</span>\s*<span>(.*?)</span>', r'"CPU":\s*"(.*?)"'],
        "ram": [r'<span class="label">RAM</span>\s*<span>([\d.,]+)', r'"RAM":\s*"([\d.,]+)'],
        "fabricant": [r'<span class="label">Fabricant</span>\s*<span>(.*?)</span>'],
        "num_serie": [r'<span class="label">N.*?serie</span>\s*<span>(.*?)</span>'],
        "domaine": [r'<span class="label">Domaine</span>\s*<span>(.*?)</span>'],
    }
    for key, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, html_content, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                if key == "ram":
                    try: info[key] = float(val.replace(",", "."))
                    except: pass
                else:
                    info[key] = val
                break

    system = data.get("system") if isinstance(data, dict) else None
    if isinstance(system, dict):
        aliases = {
            "os": ("os",),
            "windows_version": ("windows_version", "windowsVersion", "release"),
            "cpu": ("cpu", "cpu_name"),
            "fabricant": ("fabricant", "manufacturer"),
            "num_serie": ("num_serie", "serial"),
            "domaine": ("domaine", "domain"),
        }
        for target, sources in aliases.items():
            for source in sources:
                value = _clean_value(system.get(source))
                if value:
                    info[target] = value
                    break
        if "ram" in system:
            try:
                info["ram"] = round(float(str(system.get("ram")).replace(",", ".")), 1)
            except (TypeError, ValueError):
                pass
    return info


def _compose_os_info(sys_info):
    base = str((sys_info or {}).get("os", "") or "").strip()
    win_version = str((sys_info or {}).get("windows_version", "") or "").strip()
    if win_version and win_version.lower() not in base.lower():
        if base:
            return f"{base} ({win_version})"
        return win_version
    return base


def _parse_preview_limit(raw_value, default_value=10):
    try:
        value = int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        value = default_value
    return max(5, min(value, 200))


def _normalize_software_map(software_list):
    result = {}
    for item in software_list or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("n", "")).strip()
        if not name:
            continue
        key = name.lower()
        result[key] = {
            "n": name,
            "v": str(item.get("v", "") or "").strip(),
        }
    return result


def _software_meta_map(software_list):
    """Conserve les champs utiles pour preparer une eventuelle reinstallaion."""
    result = {}
    for item in software_list or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("n", "") or "").strip()
        if not name:
            continue
        result[name.lower()] = {
            "n": name,
            "v": str(item.get("v", "") or "").strip(),
            "e": str(item.get("e", "") or "").strip(),
            "src": str(item.get("src", "") or "").strip(),
        }
    return result


def _build_software_diff(old_software, new_software, preview_limit=10):
    old_map = _normalize_software_map(old_software)
    new_map = _normalize_software_map(new_software)

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added_keys = sorted(new_keys - old_keys)
    removed_keys = sorted(old_keys - new_keys)
    old_meta = _software_meta_map(old_software)

    removed_items = []
    for key in removed_keys:
        item = old_meta.get(key, {})
        removed_items.append({
            "key": key,
            "name": item.get("n", key),
            "version": item.get("v", ""),
            "editor": item.get("e", ""),
            "source": item.get("src", ""),
        })

    version_changed = []
    for key in sorted(old_keys & new_keys):
        old_v = old_map[key].get("v", "")
        new_v = new_map[key].get("v", "")
        if old_v != new_v:
            version_changed.append({
                "name": new_map[key].get("n", key),
                "old_version": old_v,
                "new_version": new_v,
            })

    return {
        "old_total": len(old_map),
        "new_total": len(new_map),
        "added_count": len(added_keys),
        "removed_count": len(removed_keys),
        "changed_count": len(version_changed),
        "preview_limit": preview_limit,
        "removed_items": removed_items,
        "added_examples": [new_map[k].get("n", k) for k in added_keys[:preview_limit]],
        "removed_examples": [item["name"] for item in removed_items[:preview_limit]],
        "changed_examples": version_changed[:preview_limit],
    }


def _build_system_diff(latest_snapshot, new_sys_info):
    fields = [
        ("os_info", "OS"),
        ("cpu_info", "CPU"),
        ("ram_go", "RAM"),
        ("fabricant", "Fabricant"),
        ("num_serie", "Numero serie"),
        ("domaine", "Domaine"),
    ]

    mapping = {
        "os_info": "os",
        "cpu_info": "cpu",
        "ram_go": "ram",
        "fabricant": "fabricant",
        "num_serie": "num_serie",
        "domaine": "domaine",
    }

    changes = []
    if not latest_snapshot:
        return changes

    for db_key, label in fields:
        old_val = latest_snapshot[db_key]
        new_val = new_sys_info.get(mapping[db_key], "")
        if db_key == "ram_go":
            old_num = float(old_val or 0)
            new_num = float(new_val or 0)
            if abs(old_num - new_num) > 0.001:
                changes.append({"label": label, "old": old_num, "new": new_num})
        else:
            old_txt = str(old_val or "").strip()
            new_txt = str(new_val or "").strip()
            if old_txt != new_txt:
                changes.append({"label": label, "old": old_txt or "-", "new": new_txt or "-"})

    return changes


DISK_ALERT_FREE_GO = 10.0
DISK_ALERT_FREE_PCT = 15.0


def _row_value(row, key, default=""):
    if row is None or key not in row.keys():
        return default
    value = row[key]
    return default if value is None else value


def _disk_metrics(disk):
    disk = disk if isinstance(disk, dict) else {}
    size = _json_number(disk.get("size_go", disk.get("size")))
    free = _json_number(disk.get("free_go", disk.get("free")))
    percent = round((free / size) * 100, 1) if size > 0 else None
    return {"size": round(size, 1), "free": round(free, 1), "percent": percent}


def _disk_is_low(metrics):
    if not metrics or metrics.get("free") is None:
        return False
    return metrics["free"] < DISK_ALERT_FREE_GO or (
        metrics.get("percent") is not None and metrics["percent"] < DISK_ALERT_FREE_PCT
    )


def _index_items(items, *fields):
    """Indexe des objets heterogenes sans perdre les doublons de nom."""
    result = {}
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        key = ""
        for field in fields:
            key = str(item.get(field) or "").strip().lower()
            if key:
                break
        key = key or f"#{index}"
        if key in result:
            key = f"{key}#{index}"
        result[key] = item
    return result


def _change_status(old, new):
    if old is None:
        return "Ajouté"
    if new is None:
        return "Retiré"
    return "Modifié"


def _software_changes(old_software, new_software):
    old_map = _normalize_software_map(old_software)
    new_map = _normalize_software_map(new_software)
    added = [new_map[key] for key in sorted(set(new_map) - set(old_map))]
    removed = [old_map[key] for key in sorted(set(old_map) - set(new_map))]
    changed = []
    for key in sorted(set(old_map) & set(new_map)):
        old_v = old_map[key].get("v", "")
        new_v = new_map[key].get("v", "")
        if old_v != new_v:
            changed.append({"n": new_map[key].get("n", key), "old": old_v, "new": new_v})
    return {"added": added, "removed": removed, "changed": changed}


def _system_changes(old_snapshot, new_snapshot):
    fields = [
        ("OS", "os_info"),
        ("CPU", "cpu_info"),
        ("RAM", "ram_go"),
        ("Fabricant", "fabricant"),
        ("Numero serie", "num_serie"),
        ("Domaine", "domaine"),
    ]
    changes = []
    for label, key in fields:
        old = _row_value(old_snapshot, key)
        new = _row_value(new_snapshot, key)
        if key == "ram_go":
            old = round(float(old or 0), 1)
            new = round(float(new or 0), 1)
        changed = old != new
        changes.append({"label": label, "old": old, "new": new, "changed": changed})
    return changes


def _disk_changes(old_disks, new_disks):
    old_map = _index_items(old_disks, "label", "name")
    new_map = _index_items(new_disks, "label", "name")
    rows = []
    for key in sorted(set(old_map) | set(new_map)):
        old_disk = old_map.get(key)
        new_disk = new_map.get(key)
        old_metrics = _disk_metrics(old_disk) if old_disk is not None else None
        new_metrics = _disk_metrics(new_disk) if new_disk is not None else None
        status = _change_status(old_disk, new_disk)
        if status == "Modifié" and old_metrics == new_metrics:
            status = "Inchangé"
        delta = None
        if old_metrics is not None and new_metrics is not None:
            delta = round(new_metrics["free"] - old_metrics["free"], 1)
        label = (new_disk or old_disk or {}).get("label") or (new_disk or old_disk or {}).get("name") or key
        rows.append({
            "label": label,
            "old": old_metrics,
            "new": new_metrics,
            "delta": delta,
            "status": status,
            "changed": status != "Inchangé",
            "low": _disk_is_low(new_metrics),
        })
    return rows


def _network_changes(old_network, new_network):
    old_map = _index_items(old_network, "name", "interface")
    new_map = _index_items(new_network, "name", "interface")
    fields = ("ip", "mac", "dhcp", "gateway", "dns")
    rows = []
    for key in sorted(set(old_map) | set(new_map)):
        old_item = old_map.get(key, {})
        new_item = new_map.get(key, {})
        changed_fields = [field for field in fields if old_item.get(field, "") != new_item.get(field, "")]
        status = _change_status(old_map.get(key), new_map.get(key))
        if status == "Modifié" and not changed_fields:
            status = "Inchangé"
        rows.append({
            "name": new_item.get("name") or old_item.get("name") or key,
            "old": old_item,
            "new": new_item,
            "changed_fields": changed_fields,
            "status": status,
            "changed": status != "Inchangé",
        })
    return rows


def _service_changes(old_services, new_services):
    old_map = _index_items(old_services, "sc", "n")
    new_map = _index_items(new_services, "sc", "n")
    rows = []
    for key in sorted(set(old_map) | set(new_map)):
        old_item = old_map.get(key, {})
        new_item = new_map.get(key, {})
        changed = old_item.get("status") != new_item.get("status") or old_item.get("start") != new_item.get("start")
        status = _change_status(old_map.get(key), new_map.get(key))
        if status == "Modifié" and not changed:
            status = "Inchangé"
        rows.append({
            "name": new_item.get("n") or old_item.get("n") or key,
            "short": new_item.get("sc") or old_item.get("sc") or key,
            "old": old_item,
            "new": new_item,
            "status": status,
            "changed": status != "Inchangé",
        })
    return rows


def _build_inventory_changes(old_snapshot, new_snapshot):
    old_software = _load_json_list(_row_value(old_snapshot, "software_json", "[]"))
    new_software = _load_json_list(_row_value(new_snapshot, "software_json", "[]"))
    old_disks = _load_json_list(_row_value(old_snapshot, "disks_json", "[]"))
    new_disks = _load_json_list(_row_value(new_snapshot, "disks_json", "[]"))
    old_network = _load_json_list(_row_value(old_snapshot, "network_json", "[]"))
    new_network = _load_json_list(_row_value(new_snapshot, "network_json", "[]"))
    old_services = _load_json_list(_row_value(old_snapshot, "services_json", "[]"))
    new_services = _load_json_list(_row_value(new_snapshot, "services_json", "[]"))
    old_warnings = _load_json_list(_row_value(old_snapshot, "warnings_json", "[]"))
    new_warnings = _load_json_list(_row_value(new_snapshot, "warnings_json", "[]"))
    return {
        "software": _software_changes(old_software, new_software),
        "system": _system_changes(old_snapshot, new_snapshot),
        "disks": _disk_changes(old_disks, new_disks),
        "network": _network_changes(old_network, new_network),
        "services": _service_changes(old_services, new_services),
        "warnings_added": sorted(set(new_warnings) - set(old_warnings)),
        "warnings_removed": sorted(set(old_warnings) - set(new_warnings)),
    }


def _snapshot_meta(snapshot):
    return {
        "script_version": str(_row_value(snapshot, "script_version", "")),
        "schema": int(_row_value(snapshot, "scan_schema", 0) or 0),
        "generated_at": str(_row_value(snapshot, "generated_at", "")),
        "warnings": _load_json_list(_row_value(snapshot, "warnings_json", "[]")),
    }


def _pending_update_file(token):
    safe_token = re.sub(r"[^a-zA-Z0-9_-]", "", token)
    return os.path.join(UPLOAD_FOLDER, f"pending_update_{safe_token}.json")


def _cleanup_pending_updates(max_age_seconds=86400):
    now = time.time()
    try:
        entries = os.listdir(UPLOAD_FOLDER)
    except OSError:
        return

    for name in entries:
        is_pending = name.startswith("pending_update_") or name.startswith("pending_upload_")
        if not is_pending or not name.endswith(".json"):
            continue
        path = os.path.join(UPLOAD_FOLDER, name)
        try:
            age = now - os.path.getmtime(path)
            if age > max_age_seconds:
                os.remove(path)
        except OSError:
            continue


def _normalize_software_fields(sw):
    name = str(sw.get("n", "") or "").strip()
    version = str(sw.get("v", "") or "").strip()
    editor = str(sw.get("e", "") or "").strip()
    source = str(sw.get("src", "") or "").strip()
    # Prefer explicit category when provided by snapshot generator, then fallback to heuristics.
    raw_category = str(
        sw.get("cat")
        or sw.get("category")
        or sw.get("categorie")
        or ""
    ).strip().lower()

    if raw_category in ("main", "update", "composant", "doublon"):
        category = raw_category
    else:
        name_l = name.lower()
        editor_l = editor.lower()

        # Common update packages (Windows KB, cumulative/security updates, patches, service packs).
        if re.search(r"\b(kb\d{4,}|update|hotfix|patch|cumulative|service\s*pack|security\s*update)\b", name_l):
            category = "update"
        # Runtime/framework/component packages often bundled as dependencies.
        elif re.search(r"\b(runtime|redistributable|framework|sdk|driver|component|plugin|library|module|webview2)\b", name_l) or \
             re.search(r"\b(microsoft|intel|nvidia|amd)\b", editor_l) and re.search(r"\b(driver|runtime|framework|redistributable)\b", name_l):
            category = "composant"
        else:
            category = "main"

    return name, version, editor, source, category


def _upsert_snapshot_software_index(db, snapshot_id, master_id, software_list):
    db.execute("DELETE FROM software_index WHERE snapshot_id = ?", (snapshot_id,))

    rows = []
    for sw in software_list or []:
        if not isinstance(sw, dict):
            continue
        name, version, editor, source, category = _normalize_software_fields(sw)
        if not name:
            continue
        name_norm = _normalize_software_name(name)
        search_blob = " ".join([name, version, editor, source]).lower()
        rows.append((snapshot_id, master_id, name, name_norm, version, editor, source, category, search_blob))

    if rows:
        db.executemany(
            """
            INSERT OR IGNORE INTO software_index (
                snapshot_id, master_id, software_name, software_name_norm, software_version, software_editor, software_source, software_category, search_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


FOLLOWUP_STATUSES = ("pending", "intentional", "to_install")
FOLLOWUP_STATUS_LABELS = {
    "pending": "À vérifier",
    "intentional": "Retiré volontairement",
    "to_install": "À installer",
    "resolved": "Résolu",
}
FOLLOWUP_STATUS_ORDER = {"to_install": 0, "pending": 1, "intentional": 2, "resolved": 3}


def _active_followups(db, master_id):
    return db.execute(
        """
        SELECT *
        FROM software_followups
        WHERE master_id = ? AND status <> 'resolved'
        ORDER BY CASE status
                     WHEN 'to_install' THEN 0
                     WHEN 'pending' THEN 1
                     WHEN 'intentional' THEN 2
                     ELSE 3
                 END,
                 software_name COLLATE NOCASE
        """,
        (master_id,),
    ).fetchall()


def _followup_counts(followups):
    counts = {"pending": 0, "intentional": 0, "to_install": 0}
    for followup in followups:
        status = followup["status"]
        if status in counts:
            counts[status] += 1
    counts["total"] = len(followups)
    return counts


def _set_followup_status(db, followup_id, status):
    if status not in FOLLOWUP_STATUSES:
        return False
    if status == "pending":
        db.execute(
            "UPDATE software_followups SET status = ?, decided_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, followup_id),
        )
    else:
        db.execute(
            "UPDATE software_followups SET status = ?, decided_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, followup_id),
        )
    return True


def _apply_followup_decisions(db, master_id, decisions):
    """Applique les choix de l'ecran de confirmation aux suivis actifs."""
    for software_key, status in (decisions or {}).items():
        if status not in FOLLOWUP_STATUSES:
            continue
        followup = db.execute(
            "SELECT id FROM software_followups "
            "WHERE master_id = ? AND software_key = ? AND status <> 'resolved' "
            "ORDER BY id DESC LIMIT 1",
            (master_id, software_key),
        ).fetchone()
        if followup:
            _set_followup_status(db, followup["id"], status)


def _parse_followup_decisions(form, removed_items, defaults=None):
    decisions = dict(defaults or {})
    for index, item in enumerate(removed_items or []):
        key = str(item.get("key", "") or "").strip().lower()
        if not key:
            continue
        raw = form.get(f"decision_{index}")
        if raw is None:
            raw = decisions.get(key, "pending")
        raw = str(raw or "").strip()
        if raw not in FOLLOWUP_STATUSES:
            raw = "pending"
        decisions[key] = raw
    return decisions


def _sync_software_followups(db, master_id, snapshot_id, scan_date, software, previous_snapshot):
    """Met a jour les suivis d'absence et resout ceux qui reapparaissent."""
    active = db.execute(
        "SELECT * FROM software_followups WHERE master_id = ? AND status <> 'resolved'",
        (master_id,),
    ).fetchall()
    active_by_key = {row["software_key"]: row for row in active}
    new_map = _normalize_software_map(software)

    for key, followup in active_by_key.items():
        if key in new_map:
            db.execute(
                "UPDATE software_followups SET status = 'resolved', "
                "last_seen_snapshot_id = ?, last_seen_date = ?, "
                "resolved_snapshot_id = ?, resolved_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (snapshot_id, scan_date, snapshot_id, followup["id"]),
            )
        else:
            db.execute(
                "UPDATE software_followups SET last_missing_snapshot_id = ?, "
                "last_missing_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (snapshot_id, scan_date, followup["id"]),
            )

    if previous_snapshot is None:
        return

    old_software = _load_json_list(_row_value(previous_snapshot, "software_json", "[]"))
    old_map = _normalize_software_map(old_software)
    old_meta = _software_meta_map(old_software)
    previous_id = _row_value(previous_snapshot, "id", None)
    previous_date = str(_row_value(previous_snapshot, "scan_date", "") or "")

    for key in sorted(set(old_map) - set(new_map)):
        if key in active_by_key:
            continue
        item = old_meta.get(key, {})
        db.execute(
            """
            INSERT OR IGNORE INTO software_followups (
                master_id, software_key, software_name, old_version, old_editor, old_source,
                source_snapshot_id, last_seen_snapshot_id, last_seen_date,
                last_missing_snapshot_id, last_missing_date, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                master_id,
                key,
                item.get("n", key),
                item.get("v", ""),
                item.get("e", ""),
                item.get("src", ""),
                snapshot_id,
                previous_id,
                previous_date,
                snapshot_id,
                scan_date,
            ),
        )


def _insert_snapshot(db, master_id, scan_date, scan_date_iso, content_hash,
                     software, sys_info, disks, network, services, metadata=None):
    """Insere un snapshot, ses metadonnees de scan et son index, sans commit."""
    metadata = metadata or {}
    previous_snapshot = db.execute(
        "SELECT * FROM latest_snapshots WHERE master_id = ?", (master_id,)
    ).fetchone()
    cur = db.execute("""
        INSERT INTO snapshots (master_id, scan_date, scan_date_iso, content_hash, os_info, cpu_info, ram_go,
            fabricant, num_serie, domaine, software_json, disks_json, network_json, services_json,
            warnings_json, script_version, scan_schema, generated_at, total_software)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        master_id,
        scan_date,
        scan_date_iso,
        content_hash,
        _compose_os_info(sys_info),
        sys_info.get("cpu", ""),
        float(sys_info.get("ram", 0) or 0),
        sys_info.get("fabricant", ""),
        sys_info.get("num_serie", ""),
        sys_info.get("domaine", ""),
        json.dumps(software, ensure_ascii=False),
        json.dumps(disks, ensure_ascii=False),
        json.dumps(network, ensure_ascii=False),
        json.dumps(services, ensure_ascii=False),
        json.dumps(metadata.get("warnings") or [], ensure_ascii=False),
        str(metadata.get("script_version") or ""),
        int(metadata.get("schema") or 0),
        str(metadata.get("generated_at") or ""),
        len(software),
    ))
    _upsert_snapshot_software_index(db, cur.lastrowid, master_id, software)
    _sync_software_followups(db, master_id, cur.lastrowid, scan_date, software, previous_snapshot)
    return cur.lastrowid


def _rebuild_software_index(db):
    db.execute("DELETE FROM software_index")
    snapshots = db.execute("SELECT id, master_id, software_json FROM snapshots").fetchall()
    for snap in snapshots:
        try:
            software_list = json.loads(snap["software_json"] or "[]")
        except json.JSONDecodeError:
            software_list = []
        _upsert_snapshot_software_index(db, snap["id"], snap["master_id"], software_list)


def _ensure_software_index_ready(db):
    """Repare les snapshots non indexes, y compris quand l'index est partiel."""
    missing = int(db.execute(
        """
        SELECT COUNT(*) AS c
        FROM snapshots s
        WHERE s.total_software > 0
          AND s.software_json IS NOT NULL
          AND s.software_json NOT IN ('', '[]', 'null')
          AND NOT EXISTS (SELECT 1 FROM software_index si WHERE si.snapshot_id = s.id)
        """
    ).fetchone()["c"])
    if missing > 0:
        _rebuild_software_index(db)
        db.commit()


def _apply_minimal_pack_to_master(db, master_id):
    pack_items = db.execute(
        """
        SELECT software_name, install_url, note_text, note, is_licensed, license_type
        FROM roadmap_minimal_pack
        ORDER BY software_name
        """
    ).fetchall()
    for item in pack_items:
        note_text = item["note_text"] or item["note"] or ""
        db.execute(
            """
            INSERT OR IGNORE INTO roadmap_items
            (master_id, software_name, install_url, note_text, note, is_licensed, license_type, is_done, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'minimal')
            """,
            (
                master_id,
                item["software_name"],
                item["install_url"] or "",
                note_text,
                note_text,
                int(item["is_licensed"] or 0),
                item["license_type"] or "",
            ),
        )


def _get_preparation_master(db, master_id):
    return db.execute(
        "SELECT * FROM masters WHERE id = ? AND workflow_type = 'preparation'",
        (master_id,),
    ).fetchone()


# ─── RAPPORTS PARC ───────────────────────────────────────────────────────────
# Trois lectures agregees du dernier scan : versions par logiciel, licences
# declarees et logiciels importants. Les scans anciens et les masters de
# preparation sont exclus ; aucune de ces fonctions n'ecrit en base.


def _parc_version_key(value):
    """Cle de tri naturelle des versions (24.10 apres 24.9), texte sinon."""
    parts = re.findall(r"\d+", str(value or ""))
    if not parts:
        return (0, str(value or "").lower())
    return (1, ".".join(f"{int(part):09d}" for part in parts[:4]))


def _parc_software_key(row):
    """Cle normalisee d'une ligne : celle stockee, sinon recalculee."""
    return row["software_name_norm"] or _normalize_software_name(row["software_name"])


def _parc_display_name(names):
    """Nom lisible d'un groupe : la variante la plus courte (« Firefox »)."""
    return min(names, key=lambda name: (len(name), name.lower()))


def _latest_software_rows(db):
    """Index logiciel du dernier scan de chaque master inventorie.

    La cle `software_name_norm` regroupe les variantes d'un meme logiciel
    (« Mozilla Firefox » et « Firefox »). Les lignes restent individuelles :
    deux versions installees cote a cote sur un poste sont donc conservees.
    """
    return db.execute(
        """
        SELECT si.master_id AS master_id,
               si.software_name AS software_name,
               COALESCE(si.software_name_norm, '') AS software_name_norm,
               TRIM(COALESCE(si.software_version, '')) AS software_version,
               TRIM(COALESCE(si.software_category, '')) AS software_category
        FROM software_index si
        JOIN masters m ON m.id = si.master_id
        WHERE m.workflow_type = 'inventory'
          AND si.snapshot_id IN (SELECT id FROM latest_snapshots)
        """
    ).fetchall()


def _parc_version_rows(db):
    """Logiciel (cle normalisee) x version x nombre de masters."""
    grouped = {}
    for row in _latest_software_rows(db):
        item = grouped.setdefault(
            (_parc_software_key(row), row["software_version"]),
            {
                "version": row["software_version"],
                "masters": set(),
                "names": [],
            },
        )
        item["masters"].add(row["master_id"])
        item["names"].append(row["software_name"])

    # Une version absente ne peut etre ni « ancienne » ni « derniere observee ».
    latest_by_norm = {}
    for (norm, version), item in grouped.items():
        if not version:
            continue
        key = _parc_version_key(version)
        if norm not in latest_by_norm or key > latest_by_norm[norm]:
            latest_by_norm[norm] = key

    result = []
    for (norm, version), item in grouped.items():
        if not version:
            status = "sans_version"
        elif _parc_version_key(version) == latest_by_norm[norm]:
            status = "last"
        else:
            status = "old"
        result.append(
            {
                "software_name": _parc_display_name(item["names"]),
                "norm": norm,
                "version": version,
                "masters": len(item["masters"]),
                "status": status,
            }
        )
    # Versions recentes d'abord, puis groupes par logiciel.
    result.sort(key=lambda item: _parc_version_key(item["version"]), reverse=True)
    result.sort(key=lambda item: (item["norm"], item["software_name"].lower()))
    return result


def _parc_licence_flag_rows(db):
    """Drapeaux « sous licence » du parc : une ligne par master et logiciel."""
    return db.execute(
        """
        SELECT sf.master_id AS master_id,
               sf.software_name AS software_name,
               TRIM(COALESCE(sf.license_type, '')) AS license_type,
               TRIM(COALESCE(sf.note, '')) AS note,
               m.pc_name AS pc_name
        FROM software_flags sf
        JOIN masters m ON m.id = sf.master_id
        WHERE sf.is_licensed = 1 AND m.workflow_type = 'inventory'
        ORDER BY sf.software_name COLLATE NOCASE, m.pc_name COLLATE NOCASE
        """
    ).fetchall()


def _parc_licence_rows(db):
    """Logiciels sous licence (unite : logiciel), masters sans information."""
    rows = _parc_licence_flag_rows(db)

    grouped = {}
    for row in rows:
        norm = _normalize_software_name(row["software_name"])
        item = grouped.setdefault(
            norm,
            {
                "names": [],
                "license_types": [],
                "masters": [],
                "missing": 0,
            },
        )
        item["names"].append(row["software_name"])
        if row["license_type"]:
            if row["license_type"] not in item["license_types"]:
                item["license_types"].append(row["license_type"])
        else:
            item["missing"] += 1
        if row["pc_name"] not in item["masters"]:
            item["masters"].append(row["pc_name"])

    result = []
    for item in grouped.values():
        item["software_name"] = _parc_display_name(item.pop("names"))
        item["license_label"] = ", ".join(item["license_types"]) or "—"
        item["without_info"] = item["missing"] > 0
        result.append(item)
    result.sort(key=lambda item: item["software_name"].lower())
    return result


def _parc_important_rows(db):
    """Logiciels marques importants, avec toutes leurs versions au dernier scan."""
    rows = db.execute(
        """
        SELECT sf.master_id AS master_id,
               sf.software_name AS software_name,
               TRIM(COALESCE(sf.note, '')) AS note,
               sf.is_licensed AS is_licensed,
               TRIM(COALESCE(sf.license_type, '')) AS license_type,
               m.pc_name AS pc_name
        FROM software_flags sf
        JOIN masters m ON m.id = sf.master_id
        WHERE sf.is_important = 1 AND m.workflow_type = 'inventory'
        ORDER BY m.pc_name COLLATE NOCASE
        """
    ).fetchall()

    # Toutes les versions installees d'un logiciel sur un master, pas seulement
    # la plus grande chaine : deux versions cote a cote restent visibles.
    versions_by_master = {}
    for row in _latest_software_rows(db):
        versions_by_master.setdefault(
            (row["master_id"], _parc_software_key(row)), []
        ).append((row["software_version"], row["software_category"]))

    grouped = {}
    for row in rows:
        norm = _normalize_software_name(row["software_name"])
        item = grouped.setdefault(
            norm,
            {
                "norm": norm,
                "names": [],
                "masters": {},
                "notes": [],
                "license_types": [],
                "licensed": False,
            },
        )
        item["names"].append(row["software_name"])
        master = item["masters"].get(row["master_id"])
        if master is None:
            master = item["masters"][row["master_id"]] = {
                "id": row["master_id"],
                "pc_name": row["pc_name"],
                "versions": [],
                "category": "",
            }
            for version, category in versions_by_master.get((row["master_id"], norm), []):
                if version:
                    master["versions"].append(version)
                if category and not master["category"]:
                    master["category"] = category
        if row["note"] and row["note"] not in item["notes"]:
            item["notes"].append(row["note"])
        if row["is_licensed"]:
            item["licensed"] = True
            if row["license_type"] and row["license_type"] not in item["license_types"]:
                item["license_types"].append(row["license_type"])

    result = []
    for item in grouped.values():
        item["software_name"] = _parc_display_name(item.pop("names"))
        item["license_label"] = ", ".join(item["license_types"]) or "—"
        for master in item["masters"].values():
            master["versions"] = sorted(
                set(master["versions"]), key=_parc_version_key, reverse=True
            )
        # L'ordre des masters suit le nom du poste, comme les autres rapports.
        item["masters"] = sorted(
            item["masters"].values(), key=lambda master: master["pc_name"].lower()
        )
        item["versions"] = sorted(
            {version for master in item["masters"] for version in master["versions"]},
            key=_parc_version_key,
            reverse=True,
        )
        result.append(item)
    result.sort(key=lambda item: (item["norm"], item["software_name"].lower()))
    return result


def _parc_report_summary(versions, licences, importants):
    return {
        "software_count": len({row["norm"] for row in versions}),
        "version_rows": len(versions),
        "licensed_count": len(licences),
        "licensed_without_info": sum(row["without_info"] for row in licences),
        "important_count": len(importants),
    }


# Prefixes interpretes comme une formule par un tableur : une cellule qui en
# commence une est neutralisee par une apostrophe, sinon un nom de logiciel ou
# une note saisie dans l'interface pourrait executer une formule a l'ouverture.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv_response(rows, header, filename):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(header)
    writer.writerows([[_csv_safe(cell) for cell in row] for row in rows])
    content = output.getvalue().encode("utf-8-sig")
    output.close()
    return app.response_class(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ===== ROUTES =====

def _fleet_latest_rows(db):
    return db.execute("""
        SELECT m.id, m.pc_name, m.label,
               ls.id AS snapshot_id, ls.scan_date, ls.scan_key,
               ls.disks_json, ls.network_json, ls.warnings_json,
               ls.script_version, ls.scan_schema, ls.total_software
        FROM masters m
        LEFT JOIN latest_snapshots ls ON ls.master_id = m.id
        WHERE m.workflow_type = 'inventory'
        ORDER BY m.pc_name
    """).fetchall()


def _build_fleet_health(db, stale_days=30):
    """Agrege le dernier etat de chaque master sans charger tout l'historique."""
    rows = _fleet_latest_rows(db)
    cutoff = _stale_cutoff_iso(stale_days)
    entries = []
    disk_alerts = []
    warnings = []
    network_entries = []
    ip_map = defaultdict(list)
    mac_map = defaultdict(list)

    for row in rows:
        snapshot_id = row["snapshot_id"]
        disks = _load_json_list(row["disks_json"])
        network = _load_json_list(row["network_json"])
        scan_warnings = _load_json_list(row["warnings_json"])
        script_old = bool(snapshot_id) and _is_old_script_version(
            row["script_version"]
        )
        low_disks = []
        for disk in disks:
            metrics = _disk_metrics(disk)
            if _disk_is_low(metrics):
                label = disk.get("label") or disk.get("name") or "?"
                alert = {
                    "pc_name": row["pc_name"],
                    "master_id": row["id"],
                    "snapshot_id": snapshot_id,
                    "scan_date": row["scan_date"] or "",
                    "label": label,
                    "free": metrics["free"],
                    "size": metrics["size"],
                    "percent": metrics["percent"],
                }
                low_disks.append(alert)
                disk_alerts.append(alert)

        for message in scan_warnings:
            warnings.append({
                "pc_name": row["pc_name"],
                "master_id": row["id"],
                "snapshot_id": snapshot_id,
                "scan_date": row["scan_date"] or "",
                "message": message,
            })

        for item in network:
            if not isinstance(item, dict):
                continue
            entry = {
                "pc_name": row["pc_name"],
                "master_id": row["id"],
                "snapshot_id": snapshot_id,
                "scan_date": row["scan_date"] or "",
                "name": str(item.get("name") or "?"),
                "ip": str(item.get("ip") or "").strip(),
                "mac": str(item.get("mac") or "").strip(),
                "dhcp": str(item.get("dhcp") or "").strip(),
                "gateway": str(item.get("gateway") or "").strip(),
                "dns": str(item.get("dns") or "").strip(),
                "duplicate_ip": False,
                "duplicate_mac": False,
            }
            network_entries.append(entry)
            if entry["ip"] and not entry["ip"].startswith("127."):
                ip_map[entry["ip"]].append(entry)
            if entry["mac"] and not entry["mac"].startswith("00:00:00"):
                mac_map[entry["mac"]].append(entry)

        entries.append({
            "id": row["id"],
            "pc_name": row["pc_name"],
            "label": row["label"] or "",
            "snapshot_id": snapshot_id,
            "scan_date": row["scan_date"] or "",
            "scan_key": row["scan_key"] or "",
            "script_version": row["script_version"] or "",
            "scan_schema": row["scan_schema"] or 0,
            "script_old": script_old,
            "total_software": row["total_software"] or 0,
            "missing_scan": snapshot_id is None,
            "stale": (not snapshot_id) or (row["scan_key"] or "") < cutoff,
            "low_disks": low_disks,
            "warnings": scan_warnings,
            "network": network,
        })

    duplicate_ips = {key: value for key, value in ip_map.items() if len(value) > 1}
    duplicate_macs = {key: value for key, value in mac_map.items() if len(value) > 1}
    for entry in network_entries:
        entry["duplicate_ip"] = entry["ip"] in duplicate_ips
        entry["duplicate_mac"] = entry["mac"] in duplicate_macs

    for entry in entries:
        entry["has_low_disk"] = bool(entry["low_disks"])
        entry["has_warning"] = bool(entry["warnings"])
        entry["has_network_duplicate"] = any(
            item.get("ip") in duplicate_ips or item.get("mac") in duplicate_macs
            for item in entry["network"] if isinstance(item, dict)
        )

    return {
        "entries": entries,
        "network_entries": network_entries,
        "disk_alerts": disk_alerts,
        "warnings": warnings,
        "duplicate_ips": duplicate_ips,
        "duplicate_macs": duplicate_macs,
        "stale_days": stale_days,
        "cutoff": cutoff,
        "total": len(entries),
        "scanned": sum(1 for entry in entries if entry["snapshot_id"]),
        "missing_scan": sum(1 for entry in entries if entry["missing_scan"]),
        "stale": sum(1 for entry in entries if entry["stale"]),
        "old_script": sum(1 for entry in entries if entry["script_old"]),
        "disk_masters": len({entry["id"] for entry in entries if entry["low_disks"]}),
        "warning_masters": len({entry["id"] for entry in entries if entry["warnings"]}),
        "duplicate_ip_count": len(duplicate_ips),
        "duplicate_mac_count": len(duplicate_macs),
        "disk_free_go": DISK_ALERT_FREE_GO,
        "disk_free_pct": DISK_ALERT_FREE_PCT,
    }


def _script_sha256():
    """Empreinte du script servi, pour verification apres telechargement."""
    path = os.path.join(_DOWNLOADS_DIR, _SCRIPT_ASSET_NAME)
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


@app.route("/")
def index():
    db = get_db()
    _ensure_software_index_ready(db)
    masters = db.execute("""
        SELECT m.*,
            (SELECT COUNT(*) FROM snapshots WHERE master_id = m.id) as snap_count,
            ls.scan_date as last_scan,
            ls.total_software as last_total
        FROM masters m
        LEFT JOIN latest_snapshots ls ON ls.master_id = m.id
        WHERE m.workflow_type = 'inventory'
        ORDER BY m.pc_name
    """).fetchall()
    prep_summary = db.execute(
        """
        SELECT
            COUNT(*) AS prep_masters,
            (SELECT COUNT(*) FROM roadmap_items) AS prep_items,
            (SELECT COUNT(*) FROM roadmap_items WHERE is_done = 0) AS prep_pending
        FROM masters
        WHERE workflow_type = 'preparation'
        """
    ).fetchone()
    return render_template(
        "index.html",
        masters=masters,
        prep_summary=prep_summary,
        script_sha256=_script_sha256(),
        import_report=session.pop("last_import_report", None),
    )


@app.route("/sante")
def fleet_health():
    db = get_db()
    return render_template("health.html", health=_build_fleet_health(db))


@app.route("/reseau")
def network_registry():
    db = get_db()
    return render_template("network_registry.html", health=_build_fleet_health(db))


@app.route("/rapports")
def parc_reports():
    db = get_db()
    versions = _parc_version_rows(db)
    licences = _parc_licence_rows(db)
    importants = _parc_important_rows(db)
    return render_template(
        "parc_reports.html",
        versions=versions,
        licences=licences,
        importants=importants,
        summary=_parc_report_summary(versions, licences, importants),
    )


@app.route("/rapports/export/licences.csv")
def parc_reports_licences_csv():
    db = get_db()
    data = [
        [
            row["master_id"],
            row["pc_name"],
            row["software_name"],
            row["license_type"],
            "non" if row["license_type"] else "oui",
            row["note"],
        ]
        for row in _parc_licence_flag_rows(db)
    ]
    return _csv_response(
        data,
        [
            "master_id",
            "master",
            "logiciel",
            "type_licence",
            "information_manquante",
            "note",
        ],
        "FabInventory-Licences.csv",
    )


@app.route("/rapports/export/importants.csv")
def parc_reports_importants_csv():
    db = get_db()
    data = []
    for software in _parc_important_rows(db):
        for master in software["masters"]:
            # Une ligne par version installee sur le poste, vide si aucune.
            for version in master["versions"] or [""]:
                data.append(
                    [
                        master["id"],
                        master["pc_name"],
                        software["software_name"],
                        version,
                        master["category"],
                        "; ".join(software["notes"]),
                        "oui" if software["licensed"] else "non",
                        "" if software["license_label"] == "—" else software["license_label"],
                    ]
                )
    return _csv_response(
        data,
        [
            "master_id",
            "master",
            "logiciel",
            "version",
            "categorie",
            "note",
            "sous_licence",
            "type_licence",
        ],
        "FabInventory-Logiciels-Importants.csv",
    )


@app.route("/download/master-script")
def download_master_script():
    script_path = os.path.join(_DOWNLOADS_DIR, _SCRIPT_ASSET_NAME)

    if not os.path.isfile(script_path):
        flash("Script d'inventaire introuvable sur le serveur", "error")
        return redirect(url_for("index"))

    return send_from_directory(
        _DOWNLOADS_DIR,
        _SCRIPT_ASSET_NAME,
        as_attachment=True,
        download_name="FabInventory-Inventaire-Master.ps1",
    )


@app.route("/download/master-launcher")
def download_master_launcher():
    launcher_path = os.path.join(_DOWNLOADS_DIR, _LAUNCHER_ASSET_NAME)

    if not os.path.isfile(launcher_path):
        flash("Lanceur d'inventaire introuvable sur le serveur", "error")
        return redirect(url_for("index"))

    return send_from_directory(
        _DOWNLOADS_DIR,
        _LAUNCHER_ASSET_NAME,
        as_attachment=True,
        download_name="FabInventory-Lancer-Inventaire-Master.bat",
    )


@app.route("/download/master-bundle")
def download_master_bundle():
    files_to_pack = [_SCRIPT_ASSET_NAME, _LAUNCHER_ASSET_NAME]

    missing = [
        name
        for name in files_to_pack
        if not os.path.isfile(os.path.join(_DOWNLOADS_DIR, name))
    ]
    if missing:
        flash("Pack d'inventaire incomplet sur le serveur", "error")
        return redirect(url_for("index"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in files_to_pack:
            zf.write(os.path.join(_DOWNLOADS_DIR, name), arcname=name)

    buffer.seek(0)
    return app.response_class(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=FabInventory-Snapshot-Pack.zip"
        },
    )


# ─── IMPORT : IDENTITE, APERCU ET RAPPORT DE FUSION ──────────────────────────
# Le nom du fichier (`pcName`) ne suffit pas a identifier un poste : un fichier
# d'un autre poste, ou d'un poste reimage, ecraserait le dernier scan du mauvais
# master. La serie du dernier scan est donc comparee avant toute ecriture. Le
# fabricant et le domaine sont signales mais ne bloquent pas (poste renomme,
# changement de domaine) ; une serie connue et differente designe une autre
# machine et exige une confirmation explicite « poste reimagine ».

IMPORT_STATUS_LABELS = {
    "new": ("Créé", "success"),
    "update": ("Mis à jour", "primary"),
    "duplicate": ("Ignoré (identique)", "secondary"),
    "conflict": ("Refusé (identité)", "danger"),
    "invalid": ("Refusé (format)", "warning"),
}


def _pending_upload_file(token):
    safe_token = re.sub(r"[^a-zA-Z0-9_-]", "", token)
    return os.path.join(UPLOAD_FOLDER, f"pending_upload_{safe_token}.json")


def _identity_text(value):
    """Comparaison d'identite tolerante : casse, espaces et separateurs ignores."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _last_scan_identity(db, master_id):
    """Identite materielle du dernier scan d'un master, ou None."""
    row = db.execute(
        """
        SELECT scan_date, num_serie, fabricant, domaine
        FROM latest_snapshots
        WHERE master_id = ?
        """,
        (master_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "scan_date": row["scan_date"] or "",
        "num_serie": str(row["num_serie"] or "").strip(),
        "fabricant": str(row["fabricant"] or "").strip(),
        "domaine": str(row["domaine"] or "").strip(),
    }


def _identity_conflicts(last_identity, sys_info):
    """Ecarts entre le dernier scan et le fichier, avec blocage sur la serie.

    Une valeur absente d'un cote (ancien scan sans numero de serie) ne prouve
    rien : elle n'est jamais traitee comme un ecart.
    """
    result = {"blocking": False, "differences": [], "summary": ""}
    if not last_identity:
        return result

    incoming = {
        "num_serie": str(sys_info.get("num_serie") or "").strip(),
        "fabricant": str(sys_info.get("fabricant") or "").strip(),
        "domaine": str(sys_info.get("domaine") or "").strip(),
    }
    labels = {
        "num_serie": "Numero de serie",
        "fabricant": "Fabricant",
        "domaine": "Domaine",
    }
    for key, label in labels.items():
        old_value = last_identity[key]
        new_value = incoming[key]
        if not old_value or not new_value:
            continue
        if _identity_text(old_value) != _identity_text(new_value):
            result["differences"].append(
                {"field": key, "label": label, "old": old_value, "new": new_value}
            )

    serial = next(
        (item for item in result["differences"] if item["field"] == "num_serie"),
        None,
    )
    result["blocking"] = serial is not None
    if serial:
        result["summary"] = f"serie {serial['old']} -> {serial['new']}"
    elif result["differences"]:
        result["summary"] = ", ".join(
            f"{item['label'].lower()} {item['old']} -> {item['new']}"
            for item in result["differences"]
        )
    return result


def _prepare_upload_entry(filename, html_content):
    """Donnees d'un fichier parsees et normalisees, sans aucune ecriture."""
    data = parse_inventory_html(html_content)
    if not data:
        return {
            "filename": filename,
            "status": "invalid",
            "message": "pas de donnees d'inventaire v3 detectees",
            "pc_name": "",
            "software_count": 0,
            "warnings": [],
        }

    pc_name = str(data.get("pcName") or "Inconnu").strip() or "Inconnu"
    raw_software = data.get("software")
    software = raw_software if isinstance(raw_software, list) else []
    scan_date = data.get("date") or datetime.now().strftime("%d/%m/%Y %H:%M")
    sys_info = extract_system_info(html_content, data)
    disks, network = extract_disks_network(html_content, data)
    services = extract_services(data)
    metadata = _scan_metadata(data)
    return {
        "filename": filename,
        "status": "pending",
        "pc_name": pc_name,
        "pc_key": _master_key(pc_name),
        "scan_date": scan_date,
        "scan_date_iso": _parse_scan_date_iso(scan_date)
        or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "software": software,
        "software_count": len(software),
        "sys_info": sys_info,
        "disks": disks,
        "network": network,
        "services": services,
        "metadata": metadata,
        "warnings": metadata.get("warnings") or [],
        "content_hash": _snapshot_content_hash(
            pc_name, scan_date, software, sys_info, disks, network, services
        ),
    }


def _resolve_upload_item(db, entry, index, reimaged=False, batch=None):
    """Statut d'un fichier sur l'etat courant de la base, sans ecriture.

    Source unique de verite, rejouee a l'apercu puis juste avant l'ecriture :
    une base modifiee entre les deux ne peut donc ni contourner la comparaison
    d'identite ni creer un doublon.

    `batch` associe la cle d'un poste a l'empreinte du premier scan du lot :
    le second scan du meme poste vise le master cree par le premier au lieu
    d'annoncer une creation concurrente.
    """
    item = {
        "index": index,
        "filename": entry["filename"],
        "status": entry["status"],
        "message": entry.get("message", ""),
        "pc_name": entry.get("pc_name", ""),
        "scan_date": entry.get("scan_date", ""),
        "software_count": entry.get("software_count", 0),
        "warnings": entry.get("warnings", []),
        "master_id": None,
        "master_name": "",
        "identity": None,
        "reimaged": False,
    }
    if entry["status"] == "invalid":
        return item

    master = db.execute(
        "SELECT id, pc_name FROM masters "
        "WHERE pc_name_key = ? AND workflow_type = 'inventory'",
        (entry["pc_key"],),
    ).fetchone()
    if not master:
        if batch is not None and entry["pc_key"] in batch:
            item["status"] = (
                "duplicate"
                if batch[entry["pc_key"]] == entry["content_hash"]
                else "update"
            )
            item["master_name"] = f"{entry['pc_name']} (créé dans ce lot)"
            return item
        item["status"] = "new"
        item["master_name"] = entry["pc_name"]
        return item

    item["master_id"] = master["id"]
    item["master_name"] = master["pc_name"]
    last_identity = _last_scan_identity(db, master["id"])
    conflicts = _identity_conflicts(last_identity, entry["sys_info"])
    item["identity"] = {"last": last_identity, **conflicts}

    latest = db.execute(
        "SELECT content_hash FROM latest_snapshots WHERE master_id = ?",
        (master["id"],),
    ).fetchone()
    if latest and latest["content_hash"] and latest["content_hash"] == entry["content_hash"]:
        item["status"] = "duplicate"
    elif conflicts["blocking"] and not reimaged:
        item["status"] = "conflict"
        item["message"] = (
            f"identite differente du dernier scan ({conflicts['summary']})"
        )
    else:
        item["status"] = "update"
        item["reimaged"] = conflicts["blocking"]
    return item


def _plan_upload(db, entries, reimaged=()):
    """Plan d'import, sans ecriture, etabli par la resolution unique.

    `reimaged` contient les index de fichiers que l'utilisateur a explicitement
    confirmes comme postes reimagines : leur ecart de serie est alors accepte et
    le fichier est traite comme une mise a jour du master existant.
    """
    reimaged = set(reimaged or ())
    batch = {}
    plan = []
    for index, entry in enumerate(entries):
        item = _resolve_upload_item(
            db, entry, index, reimaged=index in reimaged, batch=batch
        )
        if item["status"] == "new":
            batch[entry["pc_key"]] = entry["content_hash"]
        plan.append(item)
    return plan


def _plan_totals(plan):
    counts = {status: 0 for status in IMPORT_STATUS_LABELS}
    for item in plan:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def _import_upload_plan(db, entries, plan):
    """Execute un plan d'import et retourne les compteurs du rapport de fusion.

    Chaque fichier est re-resolu juste avant l'ecriture : les statuts et les
    compteurs du rapport decrivent ce qui a reellement ete fait, pas l'apercu.
    """
    counts = {
        "created": 0,
        "updated": 0,
        "duplicates": 0,
        "conflicts": 0,
        "invalid": 0,
        "snapshots": 0,
    }
    count_keys = {
        "new": "created",
        "update": "updated",
        "duplicate": "duplicates",
        "conflict": "conflicts",
        "invalid": "invalid",
    }
    for item in plan:
        entry = entries[item["index"]]
        item.update(
            _resolve_upload_item(
                db, entry, item["index"], reimaged=item.get("reimaged", False)
            )
        )
        status = item["status"]
        if status not in ("new", "update"):
            counts[count_keys[status]] += 1
            continue

        if status == "new":
            item["master_id"] = db.execute(
                "INSERT INTO masters (pc_name, pc_name_key, workflow_type) "
                "VALUES (?, ?, 'inventory')",
                (entry["pc_name"], entry["pc_key"]),
            ).lastrowid
            counts["created"] += 1
        else:
            counts["updated"] += 1

        _insert_snapshot(
            db,
            item["master_id"],
            entry["scan_date"],
            entry["scan_date_iso"],
            entry["content_hash"],
            entry["software"],
            entry["sys_info"],
            entry["disks"],
            entry["network"],
            entry["services"],
            entry["metadata"],
        )
        db.commit()
        counts["snapshots"] += 1

    _audit(
        db,
        "import",
        "parc",
        None,
        f"{counts['snapshots']} snapshot(s)",
        (
            f"{counts['created']} master(s) créé(s), {counts['updated']} mis à jour, "
            f"{counts['duplicates']} doublon(s) ignoré(s), "
            f"{counts['conflicts']} refusé(s), {counts['invalid']} illisible(s)"
        ),
    )
    db.commit()
    _invalidate_counts_cache()
    return counts


def _import_report(counts, plan, max_items=10):
    """Rapport de fusion compact, conserve en session pour la page suivante."""
    details = [
        {
            "filename": item["filename"],
            "pc_name": item["pc_name"],
            "status": item["status"],
            "label": IMPORT_STATUS_LABELS.get(item["status"], (item["status"], ""))[0],
            "tone": IMPORT_STATUS_LABELS.get(item["status"], ("", "secondary"))[1],
            "message": item["message"],
        }
        for item in plan[:max_items]
    ]
    return {
        "counts": counts,
        "items": details,
        "total_items": len(plan),
        "truncated": len(plan) > len(details),
    }


def _flash_import_result(plan, counts):
    """Resume lisible d'un import, avec le detail des fichiers refuses."""
    if counts["snapshots"]:
        resume = (
            f"Import terminé : {counts['created']} master(s) créé(s), "
            f"{counts['updated']} mis à jour, "
            f"{counts['duplicates']} identique(s) ignoré(s)"
        )
        if counts["conflicts"]:
            resume += f", {counts['conflicts']} refusé(s) pour identité"
        if counts["invalid"]:
            resume += f", {counts['invalid']} illisible(s)"
        flash(resume, "success")
    elif counts["conflicts"] or counts["invalid"]:
        flash("Aucun inventaire importé : voir le détail des fichiers refusés.", "error")
    elif counts["duplicates"]:
        flash(
            f"{counts['duplicates']} fichier(s) identique(s) déjà importé(s), "
            "aucun doublon créé",
            "info",
        )

    for item in plan:
        if item["status"] == "conflict":
            flash(
                f"Fichier {item['filename']} : {item['message']}. "
                f"Utilisez « Mettre à jour » sur la fiche de {item['master_name']} "
                "s'il s'agit d'un poste réimaginé.",
                "error",
            )
        elif item["status"] == "invalid":
            flash(f"Fichier {item['filename']} : {item['message']}", "warning")


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        confirm_token = (request.form.get("confirm_token") or "").strip()
        if confirm_token:
            return _confirm_upload(confirm_token)

        entries = []
        for f in request.files.getlist("file"):
            if not f.filename or not f.filename.lower().endswith(".html"):
                continue
            entries.append(
                _prepare_upload_entry(
                    f.filename, f.read().decode("utf-8", errors="ignore")
                )
            )

        if not entries:
            flash("Aucun fichier HTML d'inventaire selectionne", "error")
            return redirect(request.url)

        db = get_db()
        if request.form.get("preview") == "1":
            plan = _plan_upload(db, entries)
            token = secrets.token_hex(16)
            try:
                with open(_pending_upload_file(token), "w", encoding="utf-8") as handle:
                    json.dump({"entries": entries}, handle, ensure_ascii=False)
            except OSError:
                flash("Impossible de conserver l'aperçu. Relancez l'import.", "error")
                return redirect(url_for("upload"))
            _cleanup_pending_updates()
            return render_template(
                "upload_preview.html",
                plan=plan,
                totals=_plan_totals(plan),
                conflicts=[item for item in plan if item["status"] == "conflict"],
                confirm_token=token,
                status_labels=IMPORT_STATUS_LABELS,
            )

        plan = _plan_upload(db, entries)
        counts = _import_upload_plan(db, entries, plan)
        session["last_import_report"] = _import_report(counts, plan)
        _flash_import_result(plan, counts)
        return redirect(url_for("index"))

    return render_template("upload.html")


def _confirm_upload(confirm_token):
    """Valide un aperçu : recalcule le plan sur l'etat courant puis importe."""
    pending_path = _pending_upload_file(confirm_token)
    if not os.path.isfile(pending_path):
        flash("Aperçu expiré. Relancez l'analyse des fichiers.", "error")
        return redirect(url_for("upload"))

    try:
        with open(pending_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        flash("Aperçu invalide. Relancez l'analyse des fichiers.", "error")
        return redirect(url_for("upload"))

    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        flash("Aperçu vide ou invalide. Relancez l'analyse des fichiers.", "error")
        return redirect(url_for("upload"))

    reimaged = {
        int(value)
        for value in request.form.getlist("reimaged")
        if str(value).isdigit() and int(value) < len(entries)
    }
    db = get_db()
    plan = _plan_upload(db, entries, reimaged)
    counts = _import_upload_plan(db, entries, plan)

    try:
        os.remove(pending_path)
    except OSError:
        pass

    session["last_import_report"] = _import_report(counts, plan)
    _flash_import_result(plan, counts)
    return redirect(url_for("index"))


@app.route("/master/new", methods=["GET", "POST"])
def master_new():
    db = get_db()
    minimal_pack = db.execute(
        """
        SELECT id, software_name, install_url, note_text, note, is_licensed, license_type
        FROM roadmap_minimal_pack
        ORDER BY software_name
        """
    ).fetchall()

    if request.method == "GET":
        return render_template("master_new.html", minimal_pack=minimal_pack)

    pc_name = request.form.get("pc_name", "").strip()
    label = request.form.get("label", "").strip()
    notes = request.form.get("notes", "").strip()
    use_minimal_pack = request.form.get("use_minimal_pack") == "1"

    if not pc_name:
        flash("Le nom du master est obligatoire", "error")
        return render_template("master_new.html", minimal_pack=minimal_pack)

    pc_key = _master_key(pc_name)
    existing = db.execute(
        "SELECT id FROM masters WHERE pc_name_key = ? AND workflow_type = 'preparation'",
        (pc_key,),
    ).fetchone()
    if existing:
        flash("Un master avec ce nom existe deja", "error")
        return render_template("master_new.html", minimal_pack=minimal_pack)

    cur = db.execute(
        "INSERT INTO masters (pc_name, pc_name_key, label, notes, workflow_type) "
        "VALUES (?, ?, ?, ?, 'preparation')",
        (pc_name, pc_key, label, notes),
    )
    master_id = cur.lastrowid

    custom_items_raw = request.form.getlist("roadmap_software[]")
    custom_urls_raw = request.form.getlist("roadmap_install_url[]")
    custom_notes_raw = request.form.getlist("roadmap_note[]")
    custom_licensed_raw = request.form.getlist("roadmap_is_licensed[]")
    custom_license_type_raw = request.form.getlist("roadmap_license_type[]")
    max_len = max(
        len(custom_items_raw),
        len(custom_urls_raw),
        len(custom_notes_raw),
        len(custom_licensed_raw),
        len(custom_license_type_raw),
    ) if (custom_items_raw or custom_urls_raw or custom_notes_raw or custom_licensed_raw or custom_license_type_raw) else 0
    for idx in range(max_len):
        sw_name = (custom_items_raw[idx] if idx < len(custom_items_raw) else "").strip()
        sw_url = (custom_urls_raw[idx] if idx < len(custom_urls_raw) else "").strip()
        sw_note = (custom_notes_raw[idx] if idx < len(custom_notes_raw) else "").strip()
        sw_licensed = (custom_licensed_raw[idx] if idx < len(custom_licensed_raw) else "0").strip() == "1"
        sw_license_type = (custom_license_type_raw[idx] if idx < len(custom_license_type_raw) else "").strip()
        if not sw_name:
            continue
        db.execute(
            """
            INSERT OR IGNORE INTO roadmap_items
            (master_id, software_name, install_url, note_text, note, is_licensed, license_type, is_done, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'custom')
            """,
            (master_id, sw_name, sw_url, sw_note, sw_note, 1 if sw_licensed else 0, sw_license_type),
        )

    if use_minimal_pack:
        _apply_minimal_pack_to_master(db, master_id)

    _audit(
        db,
        "master_create",
        "master",
        master_id,
        pc_name,
        "Liste de préparation créée",
    )
    db.commit()
    _invalidate_counts_cache()
    flash("Liste de preparation creee pour ce master cible", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/roadmaps")
def roadmaps_index():
    db = get_db()
    masters = db.execute(
        """
        SELECT
            m.id,
            m.pc_name,
            m.label,
            (SELECT COUNT(*) FROM roadmap_items ri WHERE ri.master_id = m.id) AS roadmap_total,
            (SELECT COUNT(*) FROM roadmap_items ri WHERE ri.master_id = m.id AND ri.is_done = 1) AS roadmap_done
        FROM masters m
        WHERE m.workflow_type = 'preparation'
        ORDER BY m.pc_name
        """
    ).fetchall()
    return render_template("roadmaps.html", masters=masters)


@app.route("/master/<int:master_id>")
def master_detail(master_id):
    db = get_db()
    master = db.execute(
        "SELECT * FROM masters WHERE id = ? AND workflow_type = 'inventory'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Master inventorie introuvable", "error")
        return redirect(url_for("index"))

    snapshots = db.execute(
        """
        SELECT s.*, k.scan_key
        FROM snapshots s
        JOIN snapshot_keys k ON k.id = s.id
        WHERE s.master_id = ?
        ORDER BY k.scan_key DESC, s.id DESC
        """,
        (master_id,),
    ).fetchall()

    # Get latest snapshot software. On renormalise la categorie pour que le
    # tableau et l'index utilisent la meme regle, y compris sur les anciens
    # snapshots qui n'ont pas de champ `cat`.
    latest = snapshots[0] if snapshots else None
    latest_script_old = bool(latest) and _is_old_script_version(latest["script_version"])
    latest_warnings = _load_json_list(latest["warnings_json"]) if latest else []
    software = json.loads(latest["software_json"]) if latest else []
    for item in software:
        if isinstance(item, dict):
            item["cat"] = _normalize_software_fields(item)[4]

    # Get flags
    flags = {}
    for row in db.execute("SELECT * FROM software_flags WHERE master_id = ?", (master_id,)).fetchall():
        flags[row["software_name"]] = {
            "important": row["is_important"],
            "note": row["note"],
            "is_licensed": row["is_licensed"],
            "license_type": row["license_type"],
        }

    followups = _active_followups(db, master_id)
    return render_template("master.html", master=master, snapshots=snapshots,
                           software=software, flags=flags, latest=latest,
                           latest_warnings=latest_warnings, followups=followups,
                           followup_counts=_followup_counts(followups),
                           latest_script_old=latest_script_old)


@app.route("/master/<int:master_id>/absences", methods=["GET", "POST"])
def master_followups(master_id):
    """File ponctuelle des absences suivies pour un master."""
    db = get_db()
    master = db.execute(
        "SELECT * FROM masters WHERE id = ? AND workflow_type = 'inventory'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Master inventorie introuvable", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        followup_id = request.form.get("followup_id", type=int)
        status = request.form.get("status", "").strip()
        followup = db.execute(
            "SELECT id, software_name FROM software_followups "
            "WHERE id = ? AND master_id = ? AND status <> 'resolved'",
            (followup_id, master_id),
        ).fetchone()
        if not followup or status not in FOLLOWUP_STATUSES:
            flash("Décision d'absence invalide", "error")
        else:
            _set_followup_status(db, followup["id"], status)
            _audit(
                db,
                "followup",
                "master",
                master_id,
                followup["software_name"],
                f"Décision : {FOLLOWUP_STATUS_LABELS.get(status, status)}",
            )
            db.commit()
            flash("Décision enregistrée", "success")
        return redirect(url_for("master_followups", master_id=master_id))

    followups = _active_followups(db, master_id)
    return render_template(
        "master_followups.html",
        master=master,
        followups=followups,
        counts=_followup_counts(followups),
        status_labels=FOLLOWUP_STATUS_LABELS,
    )


@app.route("/master/<int:master_id>/evolution")
def master_evolution(master_id):
    """Compare deux scans d'un meme poste, sans Nouvelle collecte Windows."""
    db = get_db()
    master = db.execute(
        "SELECT * FROM masters WHERE id = ? AND workflow_type = 'inventory'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Master inventorie introuvable", "error")
        return redirect(url_for("index"))

    snapshots = db.execute(
        """
        SELECT s.*, k.scan_key
        FROM snapshots s
        JOIN snapshot_keys k ON k.id = s.id
        WHERE s.master_id = ?
        ORDER BY k.scan_key DESC, s.id DESC
        """,
        (master_id,),
    ).fetchall()

    by_id = {snapshot["id"]: snapshot for snapshot in snapshots}
    to_id = request.args.get("to", type=int) or (snapshots[0]["id"] if snapshots else None)
    from_id = request.args.get("from", type=int) or (snapshots[1]["id"] if len(snapshots) > 1 else None)
    from_snapshot = by_id.get(from_id)
    to_snapshot = by_id.get(to_id)
    if from_snapshot is None or to_snapshot is None or from_snapshot["id"] == to_snapshot["id"]:
        from_snapshot = snapshots[1] if len(snapshots) > 1 else None
        to_snapshot = snapshots[0] if snapshots else None

    changes = None
    if from_snapshot is not None and to_snapshot is not None:
        changes = _build_inventory_changes(from_snapshot, to_snapshot)

    return render_template(
        "master_evolution.html",
        master=master,
        snapshots=snapshots,
        from_snapshot=from_snapshot,
        to_snapshot=to_snapshot,
        old_meta=_snapshot_meta(from_snapshot),
        new_meta=_snapshot_meta(to_snapshot),
        changes=changes,
    )


@app.route("/master/<int:master_id>/export")
def master_export(master_id):
    """Export master software list as CSV with categories"""
    db = get_db()
    master = db.execute(
        "SELECT * FROM masters WHERE id = ? AND workflow_type = 'inventory'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    # Query software_index for this master with categories
    rows = db.execute("""
        SELECT 
            si.software_name,
            si.software_version,
            si.software_editor,
            si.software_source,
            si.software_category,
            sf.is_important,
            sf.note,
            s.scan_date as scan_date
        FROM software_index si
        LEFT JOIN software_flags sf ON sf.master_id = ? AND sf.software_name = si.software_name
        LEFT JOIN snapshots s ON s.id = si.snapshot_id
        WHERE si.master_id = ?
          AND si.snapshot_id = (
              SELECT id FROM latest_snapshots WHERE master_id = ?
          )
        ORDER BY si.software_name ASC
    """, (master_id, master_id, master_id)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "software_name",
        "software_version",
        "software_editor",
        "software_source",
        "software_category",
        "is_important",
        "note",
        "scan_date",
    ])
    
    for row in rows:
        writer.writerow([
            row["software_name"],
            row["software_version"],
            row["software_editor"],
            row["software_source"],
            row["software_category"],
            "yes" if row["is_important"] else "no",
            row["note"] or "",
            row["scan_date"] or "",
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    output.close()
    return app.response_class(
        csv_bytes,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=FabInventory-{_safe_filename(master['pc_name'])}.csv"
        },
    )

@app.route("/master/<int:master_id>/roadmap")
def master_roadmap(master_id):
    db = get_db()
    master = _get_preparation_master(db, master_id)
    if not master:
        flash("Liste de preparation introuvable", "error")
        return redirect(url_for("roadmaps_index"))

    roadmap_items = db.execute(
        """
        SELECT id, software_name, install_url, note_text, note, is_licensed, license_type, is_done, source
        FROM roadmap_items
        WHERE master_id = ?
        ORDER BY is_done ASC, software_name ASC
        """,
        (master_id,),
    ).fetchall()
    minimal_pack = db.execute(
        """
        SELECT id, software_name, install_url, note_text, note, is_licensed, license_type
        FROM roadmap_minimal_pack
        ORDER BY software_name
        """
    ).fetchall()

    return render_template(
        "roadmap_master.html",
        master=master,
        roadmap_items=roadmap_items,
        minimal_pack=minimal_pack,
    )


@app.route("/master/<int:master_id>/roadmap/print")
def master_roadmap_print(master_id):
    db = get_db()
    master = _get_preparation_master(db, master_id)
    if not master:
        flash("Liste de preparation introuvable", "error")
        return redirect(url_for("roadmaps_index"))

    roadmap_items = db.execute(
        """
        SELECT software_name, install_url, note_text, note, is_licensed, license_type, is_done, source
        FROM roadmap_items
        WHERE master_id = ?
        ORDER BY is_done ASC, software_name ASC
        """,
        (master_id,),
    ).fetchall()

    density = (request.args.get("density") or "compact").strip().lower()
    if density not in ("compact", "ultra"):
        density = "compact"

    return render_template(
        "roadmap_print.html",
        master=master,
        roadmap_items=roadmap_items,
        density=density,
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


@app.route("/master/<int:master_id>/update", methods=["GET", "POST"])
def master_update(master_id):
    db = get_db()
    master = db.execute(
        "SELECT * FROM masters WHERE id = ? AND workflow_type = 'inventory'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Master inventorie introuvable", "error")
        return redirect(url_for("index"))

    latest = db.execute(
        "SELECT * FROM latest_snapshots WHERE master_id = ?",
        (master_id,),
    ).fetchone()

    _cleanup_pending_updates()

    if request.method == "GET":
        return render_template("master_update.html", master=master, latest=latest, step="upload", preview_limit=10)

    preview_limit = _parse_preview_limit(request.form.get("preview_limit"), default_value=10)

    confirm_token = request.form.get("confirm_token", "").strip()
    if confirm_token:
        pending_path = _pending_update_file(confirm_token)
        if not os.path.isfile(pending_path):
            flash("Confirmation expiree. Recommencez la mise a jour.", "error")
            return redirect(url_for("master_update", master_id=master_id))

        try:
            with open(pending_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            flash("Confirmation invalide. Recommencez la mise a jour.", "error")
            return redirect(url_for("master_update", master_id=master_id))

        if int(payload.get("master_id", -1)) != master_id:
            flash("Jeton de confirmation invalide", "error")
            return redirect(url_for("master_update", master_id=master_id))

        software = payload.get("software") or []
        sys_info = payload.get("sys_info") or {}
        disks = payload.get("disks") or []
        network = payload.get("network") or []
        services = payload.get("services") or []
        metadata = _scan_metadata(payload.get("metadata"))
        scan_date = payload.get("scan_date") or datetime.now().strftime("%d/%m/%Y %H:%M")
        scan_date_iso = _parse_scan_date_iso(scan_date) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content_hash = _snapshot_content_hash(master["pc_name"], scan_date, software, sys_info, disks, network, services)

        if latest and latest["content_hash"] and latest["content_hash"] == content_hash:
            try:
                os.remove(pending_path)
            except OSError:
                pass
            flash("Ce fichier est identique au dernier scan : aucun nouveau snapshot cree", "info")
            return redirect(url_for("master_detail", master_id=master_id))

        latest_sw = json.loads(latest["software_json"]) if latest and latest["software_json"] else []
        diff = _build_software_diff(latest_sw, software, preview_limit=preview_limit)
        removed_items = diff["removed_items"]
        saved_decisions = payload.get("decisions") if isinstance(payload.get("decisions"), dict) else {}
        decisions = _parse_followup_decisions(request.form, removed_items, saved_decisions)
        system_changes = _build_system_diff(latest, sys_info)
        # La mise a jour ciblee reste volontairement possible, mais l'ecart
        # d'identite doit etre visible avant confirmation : /upload renvoie
        # justement ici pour un poste reimagine.
        identity = _identity_conflicts(_last_scan_identity(db, master_id), sys_info)

        if request.form.get("preview_only") == "1":
            payload["removed_items"] = removed_items
            payload["decisions"] = decisions
            try:
                with open(pending_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
            except OSError:
                flash("Impossible de conserver les choix pour l'aperçu.", "error")
                return redirect(url_for("master_update", master_id=master_id))
            return render_template(
                "master_update.html",
                master=master,
                latest=latest,
                step="confirm",
                confirm_token=confirm_token,
                incoming_scan_date=scan_date,
                incoming_pc_name=str(payload.get("pc_name_from_file", "") or ""),
                incoming_warnings=metadata.get("warnings") or [],
                diff=diff,
                decisions=decisions,
                system_changes=system_changes,
                identity=identity,
                preview_limit=preview_limit,
            )

        snapshot_id = _insert_snapshot(
            db, master_id, scan_date, scan_date_iso, content_hash,
            software, sys_info, disks, network, services, metadata,
        )
        _apply_followup_decisions(db, master_id, decisions)
        _audit(
            db,
            "master_update",
            "master",
            master_id,
            master["pc_name"],
            f"Scan du {scan_date} : {len(software)} logiciel(s)",
        )
        db.commit()
        _invalidate_counts_cache()

        try:
            os.remove(pending_path)
        except OSError:
            pass

        message = "Master mis a jour: nouveau snapshot enregistre"
        if identity["differences"]:
            message += (
                f". Identite differente du dernier scan ({identity['summary']})"
            )
        flash(message, "warning" if identity["differences"] else "success")
        return redirect(url_for("master_detail", master_id=master_id))

    upload_file = request.files.get("file")
    if not upload_file or not upload_file.filename:
        flash("Selectionnez un fichier HTML d'inventaire", "error")
        return redirect(url_for("master_update", master_id=master_id))

    if not upload_file.filename.lower().endswith(".html"):
        flash("Le fichier doit etre au format .html", "error")
        return redirect(url_for("master_update", master_id=master_id))

    html_content = upload_file.read().decode("utf-8", errors="ignore")
    parsed = parse_inventory_html(html_content)
    if not parsed:
        flash("Format invalide: bloc inventoryData introuvable", "error")
        return redirect(url_for("master_update", master_id=master_id))

    software = parsed.get("software")
    if not isinstance(software, list):
        software = []

    scan_date = parsed.get("date") or datetime.now().strftime("%d/%m/%Y %H:%M")
    pc_name_from_file = str(parsed.get("pcName", "")).strip()
    sys_info = extract_system_info(html_content, parsed)
    disks, network = extract_disks_network(html_content, parsed)
    services = extract_services(parsed)
    metadata = _scan_metadata(parsed)

    latest_sw = json.loads(latest["software_json"]) if latest and latest["software_json"] else []
    diff = _build_software_diff(latest_sw, software, preview_limit=preview_limit)
    removed_items = diff["removed_items"]
    decisions = {item["key"]: "pending" for item in removed_items}
    system_changes = _build_system_diff(latest, sys_info)
    identity = _identity_conflicts(_last_scan_identity(db, master_id), sys_info)

    token = secrets.token_hex(16)
    pending_path = _pending_update_file(token)
    payload = {
        "master_id": master_id,
        "scan_date": scan_date,
        "pc_name_from_file": pc_name_from_file,
        "sys_info": sys_info,
        "software": software,
        "disks": disks,
        "network": network,
        "services": services,
        "metadata": metadata,
        "removed_items": removed_items,
        "decisions": decisions,
    }
    with open(pending_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    return render_template(
        "master_update.html",
        master=master,
        latest=latest,
        step="confirm",
        confirm_token=token,
        incoming_scan_date=scan_date,
        incoming_pc_name=pc_name_from_file,
        incoming_warnings=metadata.get("warnings") or [],
        diff=diff,
        decisions=decisions,
        system_changes=system_changes,
        identity=identity,
        preview_limit=preview_limit,
    )


@app.route("/master/<int:master_id>/edit", methods=["POST"])
def master_edit(master_id):
    db = get_db()
    previous = db.execute(
        "SELECT pc_name FROM masters WHERE id = ?", (master_id,)
    ).fetchone()
    pc_name = request.form.get("pc_name", "").strip()
    label = request.form.get("label", "")
    notes = request.form.get("notes", "")

    if not pc_name:
        flash("Le nom du master est obligatoire", "error")
        return redirect(url_for("master_detail", master_id=master_id))

    pc_key = _master_key(pc_name)
    existing = db.execute(
        "SELECT id FROM masters WHERE pc_name_key = ? AND id != ? AND workflow_type = 'inventory'",
        (pc_key, master_id),
    ).fetchone()
    if existing:
        flash("Un autre master utilise deja ce nom", "error")
        return redirect(url_for("master_detail", master_id=master_id))

    db.execute(
        "UPDATE masters SET pc_name = ?, pc_name_key = ?, label = ?, notes = ? WHERE id = ?",
        (pc_name, pc_key, label, notes, master_id),
    )
    old_name = previous["pc_name"] if previous else ""
    summary = f"{old_name} → {pc_name}" if old_name != pc_name else "Libellé / notes mis à jour"
    _audit(db, "master_edit", "master", master_id, pc_name, summary)
    db.commit()
    flash("Master mis a jour", "success")
    return redirect(url_for("master_detail", master_id=master_id))


# ─── CORBEILLE ────────────────────────────────────────────────────────────────
# Toute suppression de master ou de snapshot archive d'abord les lignes et
# leurs dependances dans `deleted_items` ; la restauration les reinsere avec
# leurs identifiants d'origine (SQLite AUTOINCREMENT ne les reattribue jamais).

DELETED_ITEM_TABLES = (
    "masters",
    "snapshots",
    "software_flags",
    "roadmap_items",
    "software_followups",
)

# Duree de conservation des archives encore restaurables ; les archives deja
# restaurees peuvent etre purgees immediatement.
TRASH_RETENTION_DAYS = 90
_TRASH_PURGE_WHERE = "restored_at IS NOT NULL OR deleted_at < datetime('now', ?)"


def _row_dict(row):
    return {key: row[key] for key in row.keys()} if row is not None else {}


def _insert_archived_row(db, table, row):
    """Reinsere une ligne archivee en conservant ses colonnes et son id."""
    if table not in DELETED_ITEM_TABLES or not row:
        return
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    db.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
        f"VALUES ({placeholders})",
        [row[column] for column in columns],
    )


def _archive_deleted_item(db, kind, label, payload):
    cur = db.execute(
        "INSERT INTO deleted_items (kind, label, payload_json) VALUES (?, ?, ?)",
        (kind, label, json.dumps(payload, ensure_ascii=False)),
    )
    return cur.lastrowid


def _archive_master(db, master_id):
    master = db.execute(
        "SELECT * FROM masters WHERE id = ?", (master_id,)
    ).fetchone()
    if not master:
        return None
    payload = {
        "master": _row_dict(master),
        "snapshots": [
            _row_dict(row)
            for row in db.execute(
                "SELECT * FROM snapshots WHERE master_id = ? ORDER BY id",
                (master_id,),
            ).fetchall()
        ],
        "software_flags": [
            _row_dict(row)
            for row in db.execute(
                "SELECT * FROM software_flags WHERE master_id = ?", (master_id,)
            ).fetchall()
        ],
        "roadmap_items": [
            _row_dict(row)
            for row in db.execute(
                "SELECT * FROM roadmap_items WHERE master_id = ?", (master_id,)
            ).fetchall()
        ],
        "software_followups": [
            _row_dict(row)
            for row in db.execute(
                "SELECT * FROM software_followups WHERE master_id = ?", (master_id,)
            ).fetchall()
        ],
    }
    return _archive_deleted_item(db, "master", master["pc_name"], payload)


def _archive_snapshot(db, snapshot_id):
    snapshot = db.execute(
        "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    if not snapshot:
        return None
    master = db.execute(
        "SELECT pc_name FROM masters WHERE id = ?", (snapshot["master_id"],)
    ).fetchone()
    label = (
        f"{master['pc_name']} — {snapshot['scan_date']}"
        if master
        else f"Scan {snapshot_id}"
    )
    return _archive_deleted_item(db, "snapshot", label, {"snapshot": _row_dict(snapshot)})


def _restore_archived_master(db, payload):
    master = payload.get("master") or {}
    if not master:
        return False, "Archive de master invalide"
    conflict = db.execute(
        "SELECT id FROM masters WHERE pc_name_key = ? AND workflow_type = ?",
        (master.get("pc_name_key", ""), master.get("workflow_type") or "inventory"),
    ).fetchone()
    if conflict:
        return False, "Un master portant ce nom existe deja ; restauration annulee"

    _insert_archived_row(db, "masters", master)
    for snapshot in payload.get("snapshots", []):
        _insert_archived_row(db, "snapshots", snapshot)
        _upsert_snapshot_software_index(
            db,
            snapshot["id"],
            snapshot["master_id"],
            _load_json_list(snapshot.get("software_json")),
        )
    for table in ("software_flags", "roadmap_items", "software_followups"):
        for row in payload.get(table, []):
            _insert_archived_row(db, table, row)
    return True, f"Master {master.get('pc_name', '')} restaure avec son historique"


def _restore_archived_snapshot(db, payload):
    snapshot = payload.get("snapshot") or {}
    if not snapshot:
        return False, "Archive de scan invalide"
    master = db.execute(
        "SELECT pc_name FROM masters WHERE id = ?", (snapshot["master_id"],)
    ).fetchone()
    if not master:
        return False, "Le master de ce scan n'existe plus ; restaurez-le d'abord"

    _insert_archived_row(db, "snapshots", snapshot)
    _upsert_snapshot_software_index(
        db,
        snapshot["id"],
        snapshot["master_id"],
        _load_json_list(snapshot.get("software_json")),
    )
    return True, f"Scan du {snapshot.get('scan_date', '')} restaure sur {master['pc_name']}"


@app.route("/admin/trash")
@staff_required
def admin_trash():
    db = get_db()
    items = db.execute(
        "SELECT id, kind, label, deleted_at FROM deleted_items "
        "WHERE restored_at IS NULL ORDER BY deleted_at DESC, id DESC"
    ).fetchall()
    purgeable = db.execute(
        f"SELECT COUNT(*) AS c FROM deleted_items WHERE {_TRASH_PURGE_WHERE}",
        (f"-{TRASH_RETENTION_DAYS} days",),
    ).fetchone()["c"]
    return render_template(
        "admin/trash.html",
        items=items,
        purgeable=purgeable,
        retention_days=TRASH_RETENTION_DAYS,
        admin_user=_get_admin_user(),
    )


@app.route("/admin/trash/purge", methods=["POST"])
@admin_required
def admin_trash_purge():
    """Supprime definitivement les archives restaurees ou trop anciennes."""
    db = get_db()
    cur = db.execute(
        f"DELETE FROM deleted_items WHERE {_TRASH_PURGE_WHERE}",
        (f"-{TRASH_RETENTION_DAYS} days",),
    )
    purged = cur.rowcount or 0
    _audit(
        db,
        "trash_purge",
        "corbeille",
        None,
        "Corbeille",
        f"{purged} archive(s) supprimée(s) définitivement",
    )
    db.commit()
    flash(
        f"{purged} archive(s) supprimee(s) definitivement",
        "success" if purged else "info",
    )
    return redirect(url_for("admin_trash"))


@app.route("/admin/trash/<int:item_id>/restore", methods=["POST"])
@staff_required
def admin_trash_restore(item_id):
    db = get_db()
    item = db.execute(
        "SELECT * FROM deleted_items WHERE id = ? AND restored_at IS NULL",
        (item_id,),
    ).fetchone()
    if not item:
        flash("Element introuvable dans la corbeille", "error")
        return redirect(url_for("admin_trash"))

    try:
        payload = json.loads(item["payload_json"])
    except (TypeError, json.JSONDecodeError):
        flash("Archive illisible", "error")
        return redirect(url_for("admin_trash"))

    try:
        if item["kind"] == "master":
            restored, message = _restore_archived_master(db, payload)
        elif item["kind"] == "snapshot":
            restored, message = _restore_archived_snapshot(db, payload)
        else:
            restored, message = False, "Type d'archive inconnu"

        if not restored:
            db.rollback()
            flash(message, "error")
            return redirect(url_for("admin_trash"))

        db.execute(
            "UPDATE deleted_items SET restored_at = CURRENT_TIMESTAMP WHERE id = ?",
            (item_id,),
        )
        _audit(
            db,
            "trash_restore",
            item["kind"],
            item_id,
            item["label"],
            "Restauré depuis la corbeille",
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Restauration impossible : conflit de donnees", "error")
        return redirect(url_for("admin_trash"))

    _invalidate_counts_cache()
    flash(message, "success")
    return redirect(url_for("admin_trash"))


@app.route("/master/<int:master_id>/delete", methods=["POST"])
def master_delete(master_id):
    db = get_db()
    master = db.execute(
        "SELECT pc_name FROM masters WHERE id = ?", (master_id,)
    ).fetchone()
    if not _archive_master(db, master_id):
        flash("Master introuvable", "error")
        return redirect(url_for("index"))
    db.execute("DELETE FROM roadmap_items WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM software_flags WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM software_index WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM snapshots WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM masters WHERE id = ?", (master_id,))
    _audit(
        db,
        "master_delete",
        "master",
        master_id,
        master["pc_name"] if master else "",
        "Déplacé dans la corbeille",
    )
    db.commit()
    _invalidate_counts_cache()
    flash("Master deplace dans la corbeille (restauration possible).", "success")
    return redirect(url_for("index"))


@app.route("/master/<int:master_id>/roadmap/add", methods=["POST"])
def roadmap_add_item(master_id):
    db = get_db()
    master = db.execute(
        "SELECT id FROM masters WHERE id = ? AND workflow_type = 'preparation'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Liste de preparation introuvable", "error")
        return redirect(url_for("roadmaps_index"))

    software_name = request.form.get("software_name", "").strip()
    install_url = request.form.get("install_url", "").strip()
    note_text = request.form.get("note", "").strip()
    is_licensed = 1 if request.form.get("is_licensed", "0").strip() == "1" else 0
    license_type = request.form.get("license_type", "").strip()
    if not software_name:
        flash("Le nom du logiciel est obligatoire", "error")
        return redirect(url_for("master_roadmap", master_id=master_id))

    db.execute(
        """
        INSERT OR IGNORE INTO roadmap_items
        (master_id, software_name, install_url, note_text, note, is_licensed, license_type, is_done, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'custom')
        """,
        (master_id, software_name, install_url, note_text, note_text, is_licensed, license_type),
    )
    _audit(db, "roadmap_add", "master", master_id, software_name, "Ajouté à la checklist")
    db.commit()
    flash("Element ajoute a la checklist", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/<int:item_id>/update", methods=["POST"])
def roadmap_update_item(master_id, item_id):
    db = get_db()
    item = db.execute(
        "SELECT id, master_id, software_name FROM roadmap_items WHERE id = ? AND master_id = ?",
        (item_id, master_id),
    ).fetchone()
    if not item:
        flash("Element de checklist introuvable", "error")
        return redirect(url_for("master_roadmap", master_id=master_id))

    software_name = request.form.get("software_name", "").strip()
    install_url = request.form.get("install_url", "").strip()
    note_text = request.form.get("note", "").strip()
    is_licensed = 1 if request.form.get("is_licensed", "0").strip() == "1" else 0
    license_type = request.form.get("license_type", "").strip()
    if not software_name:
        flash("Le nom du logiciel est obligatoire", "error")
        return redirect(url_for("master_roadmap", master_id=master_id))

    db.execute(
        """
        UPDATE roadmap_items
        SET software_name = ?, install_url = ?, note_text = ?, note = ?, is_licensed = ?, license_type = ?
        WHERE id = ? AND master_id = ?
        """,
        (software_name, install_url, note_text, note_text, is_licensed, license_type, item_id, master_id),
    )
    _audit(
        db,
        "roadmap_update",
        "master",
        master_id,
        software_name,
        f"Checklist mise à jour ({item['software_name']} → {software_name})",
    )
    db.commit()
    flash("Element de checklist mis a jour", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/<int:item_id>/toggle", methods=["POST"])
def roadmap_toggle_item(master_id, item_id):
    db = get_db()
    item = db.execute(
        "SELECT id, is_done, software_name FROM roadmap_items WHERE id = ? AND master_id = ?",
        (item_id, master_id),
    ).fetchone()
    if not item:
        flash("Element de checklist introuvable", "error")
        return redirect(url_for("master_roadmap", master_id=master_id))

    new_state = 0 if int(item["is_done"] or 0) == 1 else 1
    db.execute(
        "UPDATE roadmap_items SET is_done = ? WHERE id = ? AND master_id = ?",
        (new_state, item_id, master_id),
    )
    _audit(
        db,
        "roadmap_toggle",
        "master",
        master_id,
        item["software_name"],
        "Coché" if new_state else "Décoché",
    )
    db.commit()
    flash("Checklist mise a jour", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/<int:item_id>/delete", methods=["POST"])
def roadmap_delete_item(master_id, item_id):
    db = get_db()
    item = db.execute(
        "SELECT software_name FROM roadmap_items WHERE id = ? AND master_id = ?",
        (item_id, master_id),
    ).fetchone()
    db.execute("DELETE FROM roadmap_items WHERE id = ? AND master_id = ?", (item_id, master_id))
    _audit(
        db,
        "roadmap_delete",
        "master",
        master_id,
        item["software_name"] if item else "",
        "Retiré de la checklist",
    )
    db.commit()
    flash("Element supprime de la checklist", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/apply-minimal-pack", methods=["POST"])
def roadmap_apply_minimal_pack(master_id):
    db = get_db()
    master = db.execute(
        "SELECT id FROM masters WHERE id = ? AND workflow_type = 'preparation'",
        (master_id,),
    ).fetchone()
    if not master:
        flash("Liste de preparation introuvable", "error")
        return redirect(url_for("roadmaps_index"))

    _apply_minimal_pack_to_master(db, master_id)
    _audit(
        db,
        "roadmap_pack",
        "master",
        master_id,
        "Pack minimal",
        "Pack minimal appliqué",
    )
    db.commit()
    flash("Pack minimal applique au master cible", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/roadmap/minimal-pack")
def minimal_pack_page():
    db = get_db()
    minimal_pack = db.execute(
        """
        SELECT id, software_name, install_url, note_text, note, is_licensed, license_type
        FROM roadmap_minimal_pack
        ORDER BY software_name
        """
    ).fetchall()
    return render_template("minimal_pack.html", minimal_pack=minimal_pack)


@app.route("/roadmap/minimal-pack/add", methods=["POST"])
def minimal_pack_add_item():
    db = get_db()
    software_name = request.form.get("software_name", "").strip()
    install_url = request.form.get("install_url", "").strip()
    note_text = request.form.get("note_text", "").strip()
    is_licensed = 1 if request.form.get("is_licensed", "0").strip() == "1" else 0
    license_type = request.form.get("license_type", "").strip()
    master_id = request.form.get("master_id", "").strip()

    if not software_name:
        flash("Le nom du logiciel du pack minimal est obligatoire", "error")
    else:
        db.execute(
            """
            INSERT OR IGNORE INTO roadmap_minimal_pack
            (software_name, install_url, note_text, note, is_licensed, license_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (software_name, install_url, note_text, note_text, is_licensed, license_type),
        )
        _audit(
            db,
            "pack_add",
            "pack_minimal",
            None,
            software_name,
            "Ajouté au pack minimal",
        )
        db.commit()
        flash("Logiciel ajoute au pack minimal", "success")

    if master_id.isdigit():
        return redirect(url_for("master_roadmap", master_id=int(master_id)))
    return redirect(url_for("minimal_pack_page"))


@app.route("/roadmap/minimal-pack/<int:item_id>/update", methods=["POST"])
def minimal_pack_update_item(item_id):
    db = get_db()
    software_name = request.form.get("software_name", "").strip()
    install_url = request.form.get("install_url", "").strip()
    note_text = request.form.get("note_text", "").strip()
    is_licensed = 1 if request.form.get("is_licensed", "0").strip() == "1" else 0
    license_type = request.form.get("license_type", "").strip()
    master_id = request.form.get("master_id", "").strip()

    if not software_name:
        flash("Le nom du logiciel du pack minimal est obligatoire", "error")
    else:
        db.execute(
            """
            UPDATE roadmap_minimal_pack
            SET software_name = ?, install_url = ?, note_text = ?, note = ?, is_licensed = ?, license_type = ?
            WHERE id = ?
            """,
            (software_name, install_url, note_text, note_text, is_licensed, license_type, item_id),
        )
        _audit(
            db,
            "pack_update",
            "pack_minimal",
            item_id,
            software_name,
            "Entrée du pack minimal mise à jour",
        )
        db.commit()
        flash("Pack minimal mis a jour", "success")

    if master_id.isdigit():
        return redirect(url_for("master_roadmap", master_id=int(master_id)))
    return redirect(url_for("minimal_pack_page"))


@app.route("/roadmap/minimal-pack/<int:item_id>/delete", methods=["POST"])
def minimal_pack_delete_item(item_id):
    db = get_db()
    master_id = request.form.get("master_id", "").strip()
    item = db.execute(
        "SELECT software_name FROM roadmap_minimal_pack WHERE id = ?", (item_id,)
    ).fetchone()
    db.execute("DELETE FROM roadmap_minimal_pack WHERE id = ?", (item_id,))
    _audit(
        db,
        "pack_delete",
        "pack_minimal",
        item_id,
        item["software_name"] if item else "",
        "Retiré du pack minimal",
    )
    db.commit()
    flash("Logiciel supprime du pack minimal", "success")

    if master_id.isdigit():
        return redirect(url_for("master_roadmap", master_id=int(master_id)))
    return redirect(url_for("minimal_pack_page"))


@app.route("/snapshot/<int:snap_id>")
def snapshot_detail(snap_id):
    db = get_db()
    snap = db.execute("SELECT s.*, m.pc_name, m.label FROM snapshots s JOIN masters m ON s.master_id = m.id WHERE s.id = ?", (snap_id,)).fetchone()
    if not snap:
        flash("Snapshot introuvable", "error")
        return redirect(url_for("index"))
    software = _load_json_list(snap["software_json"])
    disks = _load_json_list(snap["disks_json"])
    network = _load_json_list(snap["network_json"])
    services = _load_json_list(snap["services_json"]) if "services_json" in snap.keys() else []
    warnings = _load_json_list(snap["warnings_json"]) if "warnings_json" in snap.keys() else []
    return render_template("snapshot.html", snap=snap, software=software, disks=disks,
                           network=network, services=services, warnings=warnings)


@app.route("/snapshot/<int:snap_id>/delete", methods=["POST"])
def snapshot_delete(snap_id):
    db = get_db()
    snap = db.execute(
        "SELECT s.master_id, s.scan_date, m.pc_name "
        "FROM snapshots s JOIN masters m ON m.id = s.master_id WHERE s.id = ?",
        (snap_id,),
    ).fetchone()
    if snap:
        _archive_snapshot(db, snap_id)
        db.execute("DELETE FROM software_index WHERE snapshot_id = ?", (snap_id,))
        db.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))
        _audit(
            db,
            "snapshot_delete",
            "snapshot",
            snap_id,
            f"{snap['pc_name']} — {snap['scan_date']}",
            "Déplacé dans la corbeille",
        )
        db.commit()
        _invalidate_counts_cache()
        flash("Scan deplace dans la corbeille (restauration possible).", "success")
        return redirect(url_for("master_detail", master_id=snap["master_id"]))
    return redirect(url_for("index"))


@app.route("/compare")
def compare():
    db = get_db()
    # Ne proposer que les masters inventories ayant au moins un scan : une liste
    # de preparation ne doit pas faire croire qu'une comparaison est possible.
    masters = db.execute("""
        SELECT m.id, m.pc_name, m.label, ls.id AS latest_snap_id
        FROM masters m
        JOIN latest_snapshots ls ON ls.master_id = m.id
        WHERE m.workflow_type = 'inventory'
        ORDER BY m.pc_name
    """).fetchall()
    return render_template("compare.html", masters=masters)


@app.route("/search")
def search_multi_master():
    db = get_db()
    query = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "latest").strip().lower()
    sort_by = (request.args.get("sort_by") or "master").strip().lower()
    sort_dir = (request.args.get("sort_dir") or "asc").strip().lower()
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    try:
        per_page = int(request.args.get("per_page", 25))
    except (TypeError, ValueError):
        per_page = 25
    per_page = max(10, min(per_page, 200))

    if scope not in ("latest", "all"):
        scope = "latest"

    allowed_sort_by = {"master", "date", "software", "version", "editor"}
    if sort_by not in allowed_sort_by:
        sort_by = "master"

    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    paging_mode = (request.args.get("paging") or "offset").strip().lower()
    if paging_mode not in ("offset", "keyset"):
        paging_mode = "offset"

    cursor_token = (request.args.get("cursor") or "").strip()

    # Strict SQL clause mapping prevents injection from query params.
    # `si.id` est le tie-breaker du curseur keyset : il doit suivre
    # immediatement la colonne triee pour que la pagination ne saute aucune ligne.
    latest_order_by_map = {
        "master": "m.pc_name {dir}, si.id {dir}",
        "date": "sk.scan_key {dir}, si.id {dir}",
        "software": "si.software_name {dir}, si.id {dir}",
        "version": "si.software_version {dir}, si.id {dir}",
        "editor": "si.software_editor {dir}, si.id {dir}",
    }
    all_order_by_map = {
        "master": "m.pc_name {dir}, si.id {dir}",
        "date": "sk.scan_key {dir}, si.id {dir}",
        "software": "si.software_name {dir}, si.id {dir}",
        "version": "si.software_version {dir}, si.id {dir}",
        "editor": "si.software_editor {dir}, si.id {dir}",
    }

    if scope == "latest":
        order_by_clause = latest_order_by_map[sort_by].format(dir=sort_dir.upper())
    else:
        order_by_clause = all_order_by_map[sort_by].format(dir=sort_dir.upper())

    _ensure_software_index_ready(db)

    total_scanned = 0

    if scope == "latest":
        total_scanned = int(db.execute("SELECT COUNT(*) AS c FROM latest_snapshots").fetchone()["c"])
    else:
        total_scanned = int(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"])

    like_term = f"%{query.lower()}%" if query else None

    latest_join = """
        JOIN latest_snapshots latest
          ON latest.id = si.snapshot_id AND latest.master_id = si.master_id
    """

    def _fetch_search_rows(limit=None, offset=None, cursor=None):
        if not query:
            return []

        sql = [
            """
            SELECT
                si.master_id,
                m.pc_name,
                m.label,
                si.snapshot_id,
                s.scan_date,
                s.created_at AS scan_created_at,
                sk.scan_key AS scan_key,
                si.software_name,
                si.software_version,
                si.software_editor,
                si.software_source,
                si.software_category,
                si.id AS row_id
            FROM software_index si
            JOIN snapshots s ON s.id = si.snapshot_id
            JOIN snapshot_keys sk ON sk.id = s.id
            JOIN masters m ON m.id = si.master_id
            """
        ]
        params = [like_term]

        if scope == "latest":
            sql.append(latest_join)

        sql.append("WHERE si.search_blob LIKE ?")

        if cursor:
            c_value = cursor.get("value")
            c_row_id = int(cursor.get("row_id") or 0)
            op = ">" if sort_dir == "asc" else "<"

            key_column_map = {
                "master": "m.pc_name",
                "date": "sk.scan_key",
                "software": "si.software_name",
                "version": "si.software_version",
                "editor": "si.software_editor",
            }
            key_column = key_column_map[sort_by]
            sql.append(
                f"""
                AND (
                    {key_column} {op} ?
                    OR ({key_column} = ? AND si.id {op} ?)
                )
                """
            )
            params.extend([c_value, c_value, c_row_id])

        sql.append(f"ORDER BY {order_by_clause}")

        if limit is not None:
            sql.append("LIMIT ?")
            params.append(int(limit))
            if offset is not None and cursor is None:
                sql.append("OFFSET ?")
                params.append(int(offset))

        return db.execute("\n".join(sql), tuple(params)).fetchall()

    export_format = (request.args.get("export") or "").strip().lower()
    if export_format == "csv" and query:
        rows = _fetch_search_rows(limit=None, offset=None)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow([
            "master_id",
            "master_name",
            "master_label",
            "snapshot_id",
            "scan_date",
            "software_name",
            "software_version",
            "software_editor",
            "software_source",
            "software_category",
        ])
        for row in rows:
            writer.writerow([
                row["master_id"],
                row["pc_name"],
                row["label"],
                row["snapshot_id"],
                row["scan_date"],
                row["software_name"],
                row["software_version"],
                row["software_editor"],
                row["software_source"],
                row["software_category"],
            ])

        csv_bytes = output.getvalue().encode("utf-8-sig")
        output.close()
        return app.response_class(
            csv_bytes,
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=FabInventory-Recherche-Globale.csv"
            },
        )

    total_results = 0
    if query:
        if scope == "latest":
            total_results = int(
                db.execute(
                    f"""
                    SELECT COUNT(*) AS c
                    FROM software_index si
                    {latest_join}
                    WHERE si.search_blob LIKE ?
                    """,
                    (like_term,),
                ).fetchone()["c"]
            )
        else:
            total_results = int(
                db.execute(
                    "SELECT COUNT(*) AS c FROM software_index si WHERE si.search_blob LIKE ?",
                    (like_term,),
                ).fetchone()["c"]
            )

    total_pages = max(1, (total_results + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    decoded_cursor = None
    if paging_mode == "keyset" and cursor_token:
        try:
            candidate = json.loads(base64.urlsafe_b64decode(cursor_token.encode("ascii")).decode("utf-8"))
            if isinstance(candidate, dict) and candidate.get("value") is not None:
                decoded_cursor = {
                    "value": candidate["value"],
                    "row_id": int(candidate.get("row_id") or 0),
                }
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            # Un curseur abime ne doit jamais produire une erreur 500.
            decoded_cursor = None

    start_idx = (page - 1) * per_page
    if paging_mode == "keyset":
        rows = _fetch_search_rows(limit=per_page + 1, cursor=decoded_cursor)
    else:
        rows = _fetch_search_rows(limit=per_page, offset=start_idx)

    has_more = False
    if paging_mode == "keyset" and len(rows) > per_page:
        has_more = True
        rows = rows[:per_page]

    next_cursor = ""
    if paging_mode == "keyset" and has_more and rows:
        key_map = {
            "master": rows[-1]["pc_name"],
            "date": rows[-1]["scan_key"],
            "software": rows[-1]["software_name"],
            "version": rows[-1]["software_version"],
            "editor": rows[-1]["software_editor"],
        }
        payload = {
            "value": key_map[sort_by],
            "row_id": rows[-1]["row_id"],
        }
        next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    paged_results = [
        {
            "master_id": row["master_id"],
            "master_name": row["pc_name"],
            "master_label": row["label"],
            "snapshot_id": row["snapshot_id"],
            "scan_date": row["scan_date"],
            "name": row["software_name"],
            "version": row["software_version"],
            "editor": row["software_editor"],
            "source": row["software_source"],
            "category": row["software_category"],
        }
        for row in rows
    ]

    return render_template(
        "search.html",
        query=query,
        scope=scope,
        sort_by=sort_by,
        sort_dir=sort_dir,
        results=paged_results,
        total_scanned=total_scanned,
        total_results=total_results,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        paging_mode=paging_mode,
        next_cursor=next_cursor,
    )


@app.route("/api/compare", methods=["POST"])
def api_compare():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Corps JSON invalide"}), 400
    try:
        snap_a_id = int(data.get("a"))
        snap_b_id = int(data.get("b"))
    except (TypeError, ValueError):
        return jsonify({"error": "Identifiants de snapshot invalides"}), 400
    db = get_db()

    snap_a = db.execute("SELECT s.*, m.pc_name FROM snapshots s JOIN masters m ON s.master_id=m.id WHERE s.id=?", (snap_a_id,)).fetchone()
    snap_b = db.execute("SELECT s.*, m.pc_name FROM snapshots s JOIN masters m ON s.master_id=m.id WHERE s.id=?", (snap_b_id,)).fetchone()

    if not snap_a or not snap_b:
        return jsonify({"error": "Snapshot introuvable"}), 404

    sw_a = {s["n"].lower(): s for s in json.loads(snap_a["software_json"])}
    sw_b = {s["n"].lower(): s for s in json.loads(snap_b["software_json"])}

    all_keys = set(list(sw_a.keys()) + list(sw_b.keys()))
    results = []
    for k in sorted(all_keys):
        a = sw_a.get(k)
        b = sw_b.get(k)
        if a and b:
            results.append({"name": a["n"], "vA": a.get("v",""), "vB": b.get("v",""), "status": "common"})
        elif a:
            results.append({"name": a["n"], "vA": a.get("v",""), "vB": "-", "status": "only_a"})
        else:
            results.append({"name": b["n"], "vA": "-", "vB": b.get("v",""), "status": "only_b"})

    stats = {
        "common": sum(1 for r in results if r["status"] == "common"),
        "only_a": sum(1 for r in results if r["status"] == "only_a"),
        "only_b": sum(1 for r in results if r["status"] == "only_b"),
        "name_a": snap_a["pc_name"],
        "name_b": snap_b["pc_name"],
    }
    return jsonify({"results": results, "stats": stats})


@app.route("/api/flag", methods=["POST"])
def api_flag():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Corps JSON invalide"}), 400
    try:
        master_id = int(data.get("master_id"))
        important = int(data.get("important", 0) or 0)
        is_licensed = int(data.get("is_licensed", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "master_id, important et is_licensed doivent etre numeriques"}), 400
    sw_name = str(data.get("name") or "").strip()
    note = str(data.get("note") or "")
    license_type = str(data.get("license_type") or "").strip()
    if not sw_name:
        return jsonify({"error": "Le nom du logiciel est obligatoire"}), 400
    if important not in (0, 1) or is_licensed not in (0, 1):
        return jsonify({"error": "important et is_licensed doivent valoir 0 ou 1"}), 400
    if license_type not in ("", "reseau", "fichier_local", "numero_serie_contrat", "mot_de_passe"):
        return jsonify({"error": "Type de licence invalide"}), 400

    db = get_db()
    master = db.execute(
        "SELECT id FROM masters WHERE id = ? AND workflow_type = 'inventory'",
        (master_id,),
    ).fetchone()
    if not master:
        return jsonify({"error": "Master introuvable"}), 404
    db.execute("""
        INSERT INTO software_flags (master_id, software_name, is_important, note, is_licensed, license_type)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(master_id, software_name)
        DO UPDATE SET is_important=?, note=?, is_licensed=?, license_type=?
    """, (master_id, sw_name, important, note, is_licensed, license_type, important, note, is_licensed, license_type))
    _audit(
        db,
        "flag",
        "master",
        master_id,
        sw_name,
        (
            f"important={'oui' if important else 'non'}, "
            f"licence={'oui' if is_licensed else 'non'}"
            + (f", type {license_type}" if license_type else "")
        ),
    )
    db.commit()
    return jsonify({"ok": True})


# ===== FABSUITE API (inter-apps) =====
@app.route("/api/fabsuite/status")
def fabsuite_status():
    db = get_db()
    count, snaps = _suite_counts(db)
    return jsonify({
        "app": "FabInventory",
        "status": "ok",
        "masters": count,
        "snapshots": snaps,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5590))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG", "0") == "1")
