"""
FabInventory - Gestionnaire d'inventaire de masters FOG
Flask + SQLite - Compatible Fablab-Suite
"""

import os
import json
import sqlite3
import re
import time
import secrets
import io
import csv
import zipfile
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, send_from_directory

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fabinventory-secret-change-me")

DB_PATH = os.environ.get("DB_PATH", "/data/fabinventory.db")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "/data/uploads")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ===== DATABASE =====
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS masters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pc_name TEXT NOT NULL,
        label TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_id INTEGER NOT NULL,
        scan_date TEXT NOT NULL,
        os_info TEXT DEFAULT '',
        cpu_info TEXT DEFAULT '',
        ram_go REAL DEFAULT 0,
        fabricant TEXT DEFAULT '',
        num_serie TEXT DEFAULT '',
        domaine TEXT DEFAULT '',
        software_json TEXT DEFAULT '[]',
        disks_json TEXT DEFAULT '[]',
        network_json TEXT DEFAULT '[]',
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
        search_blob TEXT DEFAULT '',
        UNIQUE(snapshot_id, software_name, software_version, software_editor, software_source)
    );
    CREATE INDEX IF NOT EXISTS idx_software_index_snapshot ON software_index(snapshot_id);
    CREATE INDEX IF NOT EXISTS idx_software_index_master ON software_index(master_id);
    CREATE INDEX IF NOT EXISTS idx_software_index_name ON software_index(software_name);
    CREATE INDEX IF NOT EXISTS idx_software_index_blob ON software_index(search_blob);
    CREATE INDEX IF NOT EXISTS idx_snapshots_master_created ON snapshots(master_id, created_at DESC, id DESC);
    CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at DESC, id DESC);

    CREATE TABLE IF NOT EXISTS roadmap_minimal_pack (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        software_name TEXT NOT NULL UNIQUE,
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS roadmap_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        master_id INTEGER NOT NULL,
        software_name TEXT NOT NULL,
        note TEXT DEFAULT '',
        is_done INTEGER DEFAULT 0,
        source TEXT DEFAULT 'custom',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(master_id, software_name),
        FOREIGN KEY (master_id) REFERENCES masters(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_roadmap_items_master ON roadmap_items(master_id);
    """)
    db.commit()
    db.close()

init_db()


def _suite_counts(db):
    masters = db.execute("SELECT COUNT(*) AS c FROM masters").fetchone()["c"]
    snapshots = db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"]
    return int(masters), int(snapshots)


@app.after_request
def add_fabsuite_cors_headers(response):
    """CORS restreint aux endpoints FabSuite uniquement."""
    if request.path.startswith("/api/fabsuite/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/fabsuite/manifest")
def fabsuite_manifest():
    db = get_db()
    masters, snapshots = _suite_counts(db)
    return jsonify({
        "app": "fabinventory",
        "name": "FabInventory",
        "version": "1.0.0",
        "suite_version": "1.0.0",
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


def extract_system_info(html_content):
    """Try to extract system info from HTML structure."""
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


def _build_software_diff(old_software, new_software, preview_limit=10):
    old_map = _normalize_software_map(old_software)
    new_map = _normalize_software_map(new_software)

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added_keys = sorted(new_keys - old_keys)
    removed_keys = sorted(old_keys - new_keys)

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
        "added_examples": [new_map[k].get("n", k) for k in added_keys[:preview_limit]],
        "removed_examples": [old_map[k].get("n", k) for k in removed_keys[:preview_limit]],
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
    return name, version, editor, source


def _upsert_snapshot_software_index(db, snapshot_id, master_id, software_list):
    db.execute("DELETE FROM software_index WHERE snapshot_id = ?", (snapshot_id,))

    rows = []
    for sw in software_list or []:
        if not isinstance(sw, dict):
            continue
        name, version, editor, source = _normalize_software_fields(sw)
        if not name:
            continue
        search_blob = " ".join([name, version, editor, source]).lower()
        rows.append((snapshot_id, master_id, name, version, editor, source, search_blob))

    if rows:
        db.executemany(
            """
            INSERT OR IGNORE INTO software_index (
                snapshot_id, master_id, software_name, software_version, software_editor, software_source, search_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


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
    snap_count = int(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"])
    indexed_snap_count = int(db.execute("SELECT COUNT(DISTINCT snapshot_id) AS c FROM software_index").fetchone()["c"])
    if snap_count > 0 and indexed_snap_count == 0:
        _rebuild_software_index(db)
        db.commit()


def _apply_minimal_pack_to_master(db, master_id):
    pack_items = db.execute(
        "SELECT software_name, note FROM roadmap_minimal_pack ORDER BY software_name"
    ).fetchall()
    for item in pack_items:
        db.execute(
            """
            INSERT OR IGNORE INTO roadmap_items (master_id, software_name, note, is_done, source)
            VALUES (?, ?, ?, 0, 'minimal')
            """,
            (master_id, item["software_name"], item["note"] or ""),
        )


# ===== ROUTES =====

@app.route("/")
def index():
    db = get_db()
    masters = db.execute("""
        SELECT m.*, 
            (SELECT COUNT(*) FROM snapshots WHERE master_id = m.id) as snap_count,
            (SELECT scan_date FROM snapshots WHERE master_id = m.id ORDER BY created_at DESC LIMIT 1) as last_scan,
            (SELECT total_software FROM snapshots WHERE master_id = m.id ORDER BY created_at DESC LIMIT 1) as last_total
        FROM masters m ORDER BY m.pc_name
    """).fetchall()
    return render_template("index.html", masters=masters)


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

        for f in files:
            if not f.filename or not f.filename.endswith(".html"):
                continue

            html_content = f.read().decode("utf-8", errors="ignore")
            data = parse_inventory_html(html_content)

            if not data:
                flash(f"Fichier {f.filename} : pas de donnees d'inventaire v3 detectees", "warning")
                continue

            pc_name = data.get("pcName", "Inconnu")
            scan_date = data.get("date", datetime.now().strftime("%d/%m/%Y %H:%M"))
            software = data.get("software", [])

            sys_info = extract_system_info(html_content)

            db = get_db()

            # Find or create master
            master = db.execute("SELECT id FROM masters WHERE pc_name = ?", (pc_name,)).fetchone()
            if master:
                master_id = master["id"]
            else:
                cur = db.execute("INSERT INTO masters (pc_name) VALUES (?)", (pc_name,))
                master_id = cur.lastrowid

            # Create snapshot
            cur_snap = db.execute("""
                INSERT INTO snapshots (master_id, scan_date, os_info, cpu_info, ram_go, 
                    fabricant, num_serie, domaine, software_json, total_software)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                master_id, scan_date, _compose_os_info(sys_info), sys_info["cpu"],
                sys_info["ram"], sys_info["fabricant"], sys_info["num_serie"],
                sys_info["domaine"], json.dumps(software, ensure_ascii=False),
                len(software)
            ))
            _upsert_snapshot_software_index(db, cur_snap.lastrowid, master_id, software)
            db.commit()
            imported += 1

        if imported:
            flash(f"{imported} inventaire(s) importe(s) avec succes", "success")
        return redirect(url_for("index"))

    return render_template("upload.html")


@app.route("/master/new", methods=["GET", "POST"])
def master_new():
    db = get_db()
    minimal_pack = db.execute(
        "SELECT id, software_name, note FROM roadmap_minimal_pack ORDER BY software_name"
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

    existing = db.execute(
        "SELECT id FROM masters WHERE lower(pc_name) = lower(?)",
        (pc_name,),
    ).fetchone()
    if existing:
        flash("Un master avec ce nom existe deja", "error")
        return render_template("master_new.html", minimal_pack=minimal_pack)

    cur = db.execute(
        "INSERT INTO masters (pc_name, label, notes) VALUES (?, ?, ?)",
        (pc_name, label, notes),
    )
    master_id = cur.lastrowid

    custom_items_raw = request.form.getlist("roadmap_software[]")
    custom_notes_raw = request.form.getlist("roadmap_note[]")
    max_len = max(len(custom_items_raw), len(custom_notes_raw)) if (custom_items_raw or custom_notes_raw) else 0
    for idx in range(max_len):
        sw_name = (custom_items_raw[idx] if idx < len(custom_items_raw) else "").strip()
        sw_note = (custom_notes_raw[idx] if idx < len(custom_notes_raw) else "").strip()
        if not sw_name:
            continue
        db.execute(
            """
            INSERT OR IGNORE INTO roadmap_items (master_id, software_name, note, is_done, source)
            VALUES (?, ?, ?, 0, 'custom')
            """,
            (master_id, sw_name, sw_note),
        )

    if use_minimal_pack:
        _apply_minimal_pack_to_master(db, master_id)

    db.commit()
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
        ORDER BY m.pc_name
        """
    ).fetchall()
    return render_template("roadmaps.html", masters=masters)


@app.route("/master/<int:master_id>")
def master_detail(master_id):
    db = get_db()
    master = db.execute("SELECT * FROM masters WHERE id = ?", (master_id,)).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    snapshots = db.execute(
        "SELECT * FROM snapshots WHERE master_id = ? ORDER BY created_at DESC", (master_id,)
    ).fetchall()

    # Get latest snapshot software
    latest = snapshots[0] if snapshots else None
    software = json.loads(latest["software_json"]) if latest else []

    # Get flags
    flags = {}
    for row in db.execute("SELECT * FROM software_flags WHERE master_id = ?", (master_id,)).fetchall():
        flags[row["software_name"]] = {"important": row["is_important"], "note": row["note"]}

    return render_template("master.html", master=master, snapshots=snapshots,
                           software=software, flags=flags, latest=latest)


@app.route("/master/<int:master_id>/roadmap")
def master_roadmap(master_id):
    db = get_db()
    master = db.execute("SELECT * FROM masters WHERE id = ?", (master_id,)).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    roadmap_items = db.execute(
        """
        SELECT id, software_name, note, is_done, source
        FROM roadmap_items
        WHERE master_id = ?
        ORDER BY is_done ASC, software_name ASC
        """,
        (master_id,),
    ).fetchall()
    minimal_pack = db.execute(
        "SELECT id, software_name, note FROM roadmap_minimal_pack ORDER BY software_name"
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
    master = db.execute("SELECT * FROM masters WHERE id = ?", (master_id,)).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    roadmap_items = db.execute(
        """
        SELECT software_name, note, is_done, source
        FROM roadmap_items
        WHERE master_id = ?
        ORDER BY is_done ASC, software_name ASC
        """,
        (master_id,),
    ).fetchall()

    return render_template(
        "roadmap_print.html",
        master=master,
        roadmap_items=roadmap_items,
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


@app.route("/master/<int:master_id>/update", methods=["GET", "POST"])
def master_update(master_id):
    db = get_db()
    master = db.execute("SELECT * FROM masters WHERE id = ?", (master_id,)).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    latest = db.execute(
        "SELECT * FROM snapshots WHERE master_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
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
        scan_date = payload.get("scan_date") or datetime.now().strftime("%d/%m/%Y %H:%M")

        if request.form.get("preview_only") == "1":
            latest_sw = json.loads(latest["software_json"]) if latest and latest["software_json"] else []
            diff = _build_software_diff(latest_sw, software, preview_limit=preview_limit)
            system_changes = _build_system_diff(latest, sys_info)
            return render_template(
                "master_update.html",
                master=master,
                latest=latest,
                step="confirm",
                confirm_token=confirm_token,
                incoming_scan_date=scan_date,
                incoming_pc_name=str(payload.get("pc_name_from_file", "") or ""),
                diff=diff,
                system_changes=system_changes,
                preview_limit=preview_limit,
            )

        cur_snap = db.execute(
            """
            INSERT INTO snapshots (master_id, scan_date, os_info, cpu_info, ram_go,
                fabricant, num_serie, domaine, software_json, total_software)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                master_id,
                scan_date,
                _compose_os_info(sys_info),
                sys_info.get("cpu", ""),
                float(sys_info.get("ram", 0) or 0),
                sys_info.get("fabricant", ""),
                sys_info.get("num_serie", ""),
                sys_info.get("domaine", ""),
                json.dumps(software, ensure_ascii=False),
                len(software),
            ),
        )
        _upsert_snapshot_software_index(db, cur_snap.lastrowid, master_id, software)
        db.commit()

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
    sys_info = extract_system_info(html_content)

    latest_sw = json.loads(latest["software_json"]) if latest and latest["software_json"] else []
    diff = _build_software_diff(latest_sw, software, preview_limit=preview_limit)
    system_changes = _build_system_diff(latest, sys_info)

    token = secrets.token_hex(16)
    pending_path = _pending_update_file(token)
    payload = {
        "master_id": master_id,
        "scan_date": scan_date,
        "pc_name_from_file": pc_name_from_file,
        "sys_info": sys_info,
        "software": software,
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
        diff=diff,
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

    existing = db.execute(
        "SELECT id FROM masters WHERE lower(pc_name) = lower(?) AND id != ?",
        (pc_name, master_id),
    ).fetchone()
    if existing:
        flash("Un autre master utilise deja ce nom", "error")
        return redirect(url_for("master_detail", master_id=master_id))

    db.execute(
        "UPDATE masters SET pc_name = ?, label = ?, notes = ? WHERE id = ?",
        (pc_name, label, notes, master_id),
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
    flash("Master supprime", "success")
    return redirect(url_for("index"))


@app.route("/master/<int:master_id>/roadmap/add", methods=["POST"])
def roadmap_add_item(master_id):
    db = get_db()
    master = db.execute("SELECT id FROM masters WHERE id = ?", (master_id,)).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    software_name = request.form.get("software_name", "").strip()
    note = request.form.get("note", "").strip()
    if not software_name:
        flash("Le nom du logiciel est obligatoire", "error")
        return redirect(url_for("master_roadmap", master_id=master_id))

    db.execute(
        """
        INSERT OR IGNORE INTO roadmap_items (master_id, software_name, note, is_done, source)
        VALUES (?, ?, ?, 0, 'custom')
        """,
        (master_id, software_name, note),
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
    note = request.form.get("note", "").strip()
    if not software_name:
        flash("Le nom du logiciel est obligatoire", "error")
        return redirect(url_for("master_roadmap", master_id=master_id))

    db.execute(
        "UPDATE roadmap_items SET software_name = ?, note = ? WHERE id = ? AND master_id = ?",
        (software_name, note, item_id, master_id),
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
    master = db.execute("SELECT id FROM masters WHERE id = ?", (master_id,)).fetchone()
    if not master:
        flash("Master introuvable", "error")
        return redirect(url_for("index"))

    _apply_minimal_pack_to_master(db, master_id)
    db.commit()
    flash("Pack minimal applique au master cible", "success")
    return redirect(url_for("master_roadmap", master_id=master_id))


@app.route("/roadmap/minimal-pack")
def minimal_pack_page():
    db = get_db()
    minimal_pack = db.execute(
        "SELECT id, software_name, note FROM roadmap_minimal_pack ORDER BY software_name"
    ).fetchall()
    return render_template("minimal_pack.html", minimal_pack=minimal_pack)


@app.route("/roadmap/minimal-pack/add", methods=["POST"])
def minimal_pack_add_item():
    db = get_db()
    software_name = request.form.get("software_name", "").strip()
    note = request.form.get("note", "").strip()
    master_id = request.form.get("master_id", "").strip()

    if not software_name:
        flash("Le nom du logiciel du pack minimal est obligatoire", "error")
    else:
        db.execute(
            "INSERT OR IGNORE INTO roadmap_minimal_pack (software_name, note) VALUES (?, ?)",
            (software_name, note),
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
    note = request.form.get("note", "").strip()
    master_id = request.form.get("master_id", "").strip()

    if not software_name:
        flash("Le nom du logiciel du pack minimal est obligatoire", "error")
    else:
        db.execute(
            "UPDATE roadmap_minimal_pack SET software_name = ?, note = ? WHERE id = ?",
            (software_name, note, item_id),
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
    software = json.loads(snap["software_json"])
    return render_template("snapshot.html", snap=snap, software=software)


@app.route("/snapshot/<int:snap_id>/delete", methods=["POST"])
def snapshot_delete(snap_id):
    db = get_db()
    snap = db.execute("SELECT master_id FROM snapshots WHERE id = ?", (snap_id,)).fetchone()
    if snap:
        db.execute("DELETE FROM software_index WHERE snapshot_id = ?", (snap_id,))
        db.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))
        db.commit()
        flash("Snapshot supprime", "success")
        return redirect(url_for("master_detail", master_id=snap["master_id"]))
    return redirect(url_for("index"))


@app.route("/compare")
def compare():
    db = get_db()
    masters = db.execute("""
        SELECT m.id, m.pc_name, m.label,
            (SELECT id FROM snapshots WHERE master_id = m.id ORDER BY created_at DESC LIMIT 1) as latest_snap_id
        FROM masters m ORDER BY m.pc_name
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

    # Strict SQL clause mapping prevents injection from query params.
    latest_order_by_map = {
        "master": "m.pc_name {dir}, si.software_name ASC, si.software_version ASC",
        "date": "s.created_at {dir}, s.id {dir}, m.pc_name ASC, si.software_name ASC",
        "software": "si.software_name {dir}, m.pc_name ASC, si.software_version ASC",
        "version": "si.software_version {dir}, si.software_name ASC, m.pc_name ASC",
        "editor": "si.software_editor {dir}, si.software_name ASC, m.pc_name ASC",
    }
    all_order_by_map = {
        "master": "m.pc_name {dir}, s.created_at DESC, s.id DESC, si.software_name ASC",
        "date": "s.created_at {dir}, s.id {dir}, m.pc_name ASC, si.software_name ASC",
        "software": "si.software_name {dir}, m.pc_name ASC, s.created_at DESC",
        "version": "si.software_version {dir}, si.software_name ASC, m.pc_name ASC",
        "editor": "si.software_editor {dir}, si.software_name ASC, m.pc_name ASC",
    }

    if scope == "latest":
        order_by_clause = latest_order_by_map[sort_by].format(dir=sort_dir.upper())
    else:
        order_by_clause = all_order_by_map[sort_by].format(dir=sort_dir.upper())

    _ensure_software_index_ready(db)

    results = []
    total_scanned = 0

    if scope == "latest":
        total_scanned = int(db.execute("SELECT COUNT(DISTINCT master_id) AS c FROM snapshots").fetchone()["c"])
    else:
        total_scanned = int(db.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"])

    if query:
        like_term = f"%{query.lower()}%"
        if scope == "latest":
            rows = db.execute(
                f"""
                SELECT
                    si.master_id,
                    m.pc_name,
                    m.label,
                    si.snapshot_id,
                    s.scan_date,
                    si.software_name,
                    si.software_version,
                    si.software_editor,
                    si.software_source
                FROM software_index si
                JOIN snapshots s ON s.id = si.snapshot_id
                JOIN masters m ON m.id = si.master_id
                WHERE si.search_blob LIKE ?
                  AND si.snapshot_id IN (
                      SELECT id FROM snapshots latest
                      WHERE latest.master_id = si.master_id
                      ORDER BY latest.created_at DESC, latest.id DESC
                      LIMIT 1
                  )
                ORDER BY {order_by_clause}
                """,
                (like_term,),
            ).fetchall()
        else:
            rows = db.execute(
                f"""
                SELECT
                    si.master_id,
                    m.pc_name,
                    m.label,
                    si.snapshot_id,
                    s.scan_date,
                    si.software_name,
                    si.software_version,
                    si.software_editor,
                    si.software_source
                FROM software_index si
                JOIN snapshots s ON s.id = si.snapshot_id
                JOIN masters m ON m.id = si.master_id
                WHERE si.search_blob LIKE ?
                ORDER BY {order_by_clause}
                """,
                (like_term,),
            ).fetchall()

        for row in rows:
            results.append({
                "master_id": row["master_id"],
                "master_name": row["pc_name"],
                "master_label": row["label"],
                "snapshot_id": row["snapshot_id"],
                "scan_date": row["scan_date"],
                "name": row["software_name"],
                "version": row["software_version"],
                "editor": row["software_editor"],
                "source": row["software_source"],
            })

    export_format = (request.args.get("export") or "").strip().lower()
    if export_format == "csv" and query:
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
        ])
        for row in results:
            writer.writerow([
                row.get("master_id", ""),
                row.get("master_name", ""),
                row.get("master_label", ""),
                row.get("snapshot_id", ""),
                row.get("scan_date", ""),
                row.get("name", ""),
                row.get("version", ""),
                row.get("editor", ""),
                row.get("source", ""),
            ])

        csv_bytes = output.getvalue().encode("utf-8-sig")
        output.close()
        return app.response_class(
            csv_bytes,
            mimetype="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=FabInventory-Recherche-Globale.csv"
            },
        )

    total_results = len(results)
    total_pages = max(1, (total_results + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paged_results = results[start_idx:end_idx]

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
    )


@app.route("/api/compare", methods=["POST"])
def api_compare():
    data = request.get_json()
    snap_a_id = data.get("a")
    snap_b_id = data.get("b")
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
    data = request.get_json()
    master_id = data.get("master_id")
    sw_name = data.get("name")
    important = data.get("important", 0)
    note = data.get("note", "")

    db = get_db()
    db.execute("""
        INSERT INTO software_flags (master_id, software_name, is_important, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(master_id, software_name) DO UPDATE SET is_important=?, note=?
    """, (master_id, sw_name, important, note, important, note))
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
