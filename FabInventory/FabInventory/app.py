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
import secrets
import base64
import io
import csv
import zipfile
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, send_from_directory, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# Source unique de la version : manifeste FabSuite et templates.
APP_VERSION = "2.1.4"

app = Flask(__name__)
app.jinja_env.globals["app_version"] = APP_VERSION
app.secret_key = os.environ.get("SECRET_KEY") or "fabinventory-secret-change-me"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB max upload
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
if os.environ.get("COOKIE_SECURE", "0") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True
# Cache navigateur des fichiers statiques : evite une revalidation a chaque page.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

_COMPRESSIBLE_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml")


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
    """)

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

    # Seed initial unique. Le marqueur evite qu'un admin ou un logiciel du pack
    # supprime volontairement reapparaisse au prochain demarrage.
    seed_done = db.execute(
        "SELECT value FROM app_meta WHERE key = 'initial_seed'"
    ).fetchone()
    if not seed_done:
        existing_admin = db.execute("SELECT id FROM admin_users WHERE username = 'admin'").fetchone()
        if not existing_admin:
            db.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash("admin"))
            )
            db.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) "
                "VALUES ('admin_password_is_default', '1')"
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

    db.commit()
    db.close()

init_db()


# ─── DÉCORATEUR AUTH ADMIN ────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def _get_admin_user():
    """Retourne le nom d'utilisateur admin connecté (pour affichage)."""
    return session.get('admin_username', 'admin')


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
    d['masters_coverage'] = _software_masters_count(db, d['logiciel_nom'])
    return d


def _software_masters_count(db, logiciel_nom):
    """Nombre de masters ayant ce logiciel dans leur dernier scan."""
    if not logiciel_nom:
        return 0
    result = db.execute("""
        SELECT COUNT(DISTINCT si.master_id) AS c
        FROM software_index si
        JOIN latest_snapshots ls ON ls.id = si.snapshot_id
        WHERE LOWER(si.software_name) LIKE LOWER(?)
    """, (f"%{logiciel_nom}%",)).fetchone()
    return result['c'] if result else 0


def _coverage_for_logiciels(db, logiciels_list):
    """Retourne un dict {logiciel_id: masters_count} pour une liste de logiciels."""
    coverage = {}
    for l in logiciels_list:
        coverage[l['id']] = _software_masters_count(db, l['nom'])
    return coverage


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
        return redirect(url_for('admin_dashboard'))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_db()
        user = db.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_user_id'] = user['id']
            return redirect(url_for('admin_dashboard'))
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
    nb_couverts = 0
    for l in logiciels_actifs:
        if _software_masters_count(db, l['nom']) > 0:
            nb_couverts += 1

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

    cutoff_iso = _stale_cutoff_iso()
    result = []
    for m in masters:
        master = dict(m)
        if m['snapshot_id'] and logiciels_actifs:
            nb_couverts = db.execute("""
                SELECT COUNT(DISTINCT l.id) AS c
                FROM logiciels l
                WHERE l.actif = 1 AND EXISTS (
                    SELECT 1 FROM software_index si
                    WHERE si.snapshot_id = ? AND LOWER(si.software_name) LIKE '%' || LOWER(l.nom) || '%'
                )
            """, (m['snapshot_id'],)).fetchone()['c']
        else:
            nb_couverts = 0
        master['nb_couverts'] = nb_couverts
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
        l['nb_masters'] = _software_masters_count(db, l['nom'])
        l['nb_masters_total'] = nb_masters_total
        result.append(l)

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
            db.commit()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for('admin_settings'))
    return render_template("admin/settings.html", error=error, admin_user=_get_admin_user())


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
        l['nb_masters'] = _software_masters_count(db, l['nom'])
        l['nb_masters_total'] = nb_masters_total
        l['demande_en_cours'] = l['id'] in demandes_en_cours
        result.append(l)

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
        if not name.startswith("pending_update_") or not name.endswith(".json"):
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
        search_blob = " ".join([name, version, editor, source]).lower()
        rows.append((snapshot_id, master_id, name, version, editor, source, category, search_blob))

    if rows:
        db.executemany(
            """
            INSERT OR IGNORE INTO software_index (
                snapshot_id, master_id, software_name, software_version, software_editor, software_source, software_category, search_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        "disk_masters": len({entry["id"] for entry in entries if entry["low_disks"]}),
        "warning_masters": len({entry["id"] for entry in entries if entry["warnings"]}),
        "duplicate_ip_count": len(duplicate_ips),
        "duplicate_mac_count": len(duplicate_macs),
        "disk_free_go": DISK_ALERT_FREE_GO,
        "disk_free_pct": DISK_ALERT_FREE_PCT,
    }


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
    return render_template("index.html", masters=masters, prep_summary=prep_summary)


@app.route("/sante")
def fleet_health():
    db = get_db()
    return render_template("health.html", health=_build_fleet_health(db))


@app.route("/reseau")
def network_registry():
    db = get_db()
    return render_template("network_registry.html", health=_build_fleet_health(db))


@app.route("/download/master-script")
def download_master_script():
    script_dir = os.path.join(app.root_path, "static", "downloads")
    script_name = "inventaire_master.ps1"
    script_path = os.path.join(script_dir, script_name)

    if not os.path.isfile(script_path):
        flash("Script d'inventaire introuvable sur le serveur", "error")
        return redirect(url_for("index"))

    return send_from_directory(
        script_dir,
        script_name,
        as_attachment=True,
        download_name="FabInventory-Inventaire-Master.ps1",
    )


@app.route("/download/master-launcher")
def download_master_launcher():
    script_dir = os.path.join(app.root_path, "static", "downloads")
    launcher_name = "lancer_inventaire_master.bat"
    launcher_path = os.path.join(script_dir, launcher_name)

    if not os.path.isfile(launcher_path):
        flash("Lanceur d'inventaire introuvable sur le serveur", "error")
        return redirect(url_for("index"))

    return send_from_directory(
        script_dir,
        launcher_name,
        as_attachment=True,
        download_name="FabInventory-Lancer-Inventaire-Master.bat",
    )


@app.route("/download/master-bundle")
def download_master_bundle():
    script_dir = os.path.join(app.root_path, "static", "downloads")
    files_to_pack = [
        "inventaire_master.ps1",
        "lancer_inventaire_master.bat",
    ]

    missing = [name for name in files_to_pack if not os.path.isfile(os.path.join(script_dir, name))]
    if missing:
        flash("Pack d'inventaire incomplet sur le serveur", "error")
        return redirect(url_for("index"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in files_to_pack:
            zf.write(os.path.join(script_dir, name), arcname=name)

    buffer.seek(0)
    return app.response_class(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=FabInventory-Snapshot-Pack.zip"
        },
    )


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("Aucun fichier selectionne", "error")
            return redirect(request.url)

        files = request.files.getlist("file")
        imported = 0
        skipped = 0

        for f in files:
            if not f.filename or not f.filename.lower().endswith(".html"):
                continue

            html_content = f.read().decode("utf-8", errors="ignore")
            data = parse_inventory_html(html_content)

            if not data:
                flash(f"Fichier {f.filename} : pas de donnees d'inventaire v3 detectees", "warning")
                continue

            pc_name = str(data.get("pcName") or "Inconnu").strip() or "Inconnu"
            pc_key = _master_key(pc_name)
            scan_date = data.get("date", datetime.now().strftime("%d/%m/%Y %H:%M"))
            software = data.get("software", [])
            if not isinstance(software, list):
                software = []

            sys_info = extract_system_info(html_content, data)
            disks, network = extract_disks_network(html_content, data)
            services = extract_services(data)
            metadata = _scan_metadata(data)

            db = get_db()

            # Find or create master : la cle ignore casse et espaces.
            master = db.execute(
                "SELECT id FROM masters WHERE pc_name_key = ? AND workflow_type = 'inventory'",
                (pc_key,),
            ).fetchone()
            if master:
                master_id = master["id"]
            else:
                cur = db.execute(
                    "INSERT INTO masters (pc_name, pc_name_key, workflow_type) VALUES (?, ?, 'inventory')",
                    (pc_name, pc_key),
                )
                master_id = cur.lastrowid

            content_hash = _snapshot_content_hash(pc_name, scan_date, software, sys_info, disks, network, services)
            latest = db.execute(
                "SELECT content_hash FROM latest_snapshots WHERE master_id = ?", (master_id,)
            ).fetchone()
            if latest and latest["content_hash"] and latest["content_hash"] == content_hash:
                skipped += 1
                continue

            # Create snapshot
            scan_date_iso = _parse_scan_date_iso(scan_date) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _insert_snapshot(
                db, master_id, scan_date, scan_date_iso, content_hash,
                software, sys_info, disks, network, services, metadata,
            )
            db.commit()
            _invalidate_counts_cache()
            imported += 1

        if imported:
            message = f"{imported} inventaire(s) importe(s) avec succes"
            if skipped:
                message += f" ({skipped} doublon(s) identique(s) ignore(s))"
            flash(message, "success")
        elif skipped:
            flash(f"{skipped} fichier(s) identique(s) deja importe(s), aucun doublon cree", "info")
        return redirect(url_for("index"))

    return render_template("upload.html")


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
                           followup_counts=_followup_counts(followups))


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
            "SELECT id FROM software_followups "
            "WHERE id = ? AND master_id = ? AND status <> 'resolved'",
            (followup_id, master_id),
        ).fetchone()
        if not followup or status not in FOLLOWUP_STATUSES:
            flash("Décision d'absence invalide", "error")
        else:
            _set_followup_status(db, followup["id"], status)
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
                preview_limit=preview_limit,
            )

        snapshot_id = _insert_snapshot(
            db, master_id, scan_date, scan_date_iso, content_hash,
            software, sys_info, disks, network, services, metadata,
        )
        _apply_followup_decisions(db, master_id, decisions)
        db.commit()
        _invalidate_counts_cache()

        try:
            os.remove(pending_path)
        except OSError:
            pass

        flash("Master mis a jour: nouveau snapshot enregistre", "success")
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
        preview_limit=preview_limit,
    )


@app.route("/master/<int:master_id>/edit", methods=["POST"])
def master_edit(master_id):
    db = get_db()
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
    db.commit()
    flash("Master mis a jour", "success")
    return redirect(url_for("master_detail", master_id=master_id))


@app.route("/master/<int:master_id>/delete", methods=["POST"])
def master_delete(master_id):
    db = get_db()
    db.execute("DELETE FROM roadmap_items WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM software_flags WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM software_index WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM snapshots WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM masters WHERE id = ?", (master_id,))
    db.commit()
    _invalidate_counts_cache()
    flash("Master supprime", "success")
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
    db.commit()
    flash("Element ajoute a la checklist", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/<int:item_id>/update", methods=["POST"])
def roadmap_update_item(master_id, item_id):
    db = get_db()
    item = db.execute(
        "SELECT id, master_id FROM roadmap_items WHERE id = ? AND master_id = ?",
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
    db.commit()
    flash("Element de checklist mis a jour", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/<int:item_id>/toggle", methods=["POST"])
def roadmap_toggle_item(master_id, item_id):
    db = get_db()
    item = db.execute(
        "SELECT id, is_done FROM roadmap_items WHERE id = ? AND master_id = ?",
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
    db.commit()
    flash("Checklist mise a jour", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/master/<int:master_id>/roadmap/<int:item_id>/delete", methods=["POST"])
def roadmap_delete_item(master_id, item_id):
    db = get_db()
    db.execute("DELETE FROM roadmap_items WHERE id = ? AND master_id = ?", (item_id, master_id))
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
        db.commit()
        flash("Pack minimal mis a jour", "success")

    if master_id.isdigit():
        return redirect(url_for("master_roadmap", master_id=int(master_id)))
    return redirect(url_for("minimal_pack_page"))


@app.route("/roadmap/minimal-pack/<int:item_id>/delete", methods=["POST"])
def minimal_pack_delete_item(item_id):
    db = get_db()
    master_id = request.form.get("master_id", "").strip()
    db.execute("DELETE FROM roadmap_minimal_pack WHERE id = ?", (item_id,))
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
    snap = db.execute("SELECT master_id FROM snapshots WHERE id = ?", (snap_id,)).fetchone()
    if snap:
        db.execute("DELETE FROM software_index WHERE snapshot_id = ?", (snap_id,))
        db.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))
        db.commit()
        _invalidate_counts_cache()
        flash("Snapshot supprime", "success")
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
