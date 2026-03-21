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
    info = {"os": "", "cpu": "", "ram": 0, "fabricant": "", "num_serie": "", "domaine": ""}

    patterns = {
        "os": [r'<span class="label">OS</span>\s*<span>(.*?)</span>', r'"OS":\s*"(.*?)"'],
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


def _build_software_diff(old_software, new_software):
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
        "added_examples": [new_map[k].get("n", k) for k in added_keys[:10]],
        "removed_examples": [old_map[k].get("n", k) for k in removed_keys[:10]],
        "changed_examples": version_changed[:10],
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
            db.execute("""
                INSERT INTO snapshots (master_id, scan_date, os_info, cpu_info, ram_go, 
                    fabricant, num_serie, domaine, software_json, total_software)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                master_id, scan_date, sys_info["os"], sys_info["cpu"],
                sys_info["ram"], sys_info["fabricant"], sys_info["num_serie"],
                sys_info["domaine"], json.dumps(software, ensure_ascii=False),
                len(software)
            ))
            db.commit()
            imported += 1

        if imported:
            flash(f"{imported} inventaire(s) importe(s) avec succes", "success")
        return redirect(url_for("index"))

    return render_template("upload.html")


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
        return render_template("master_update.html", master=master, latest=latest, step="upload")

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

        db.execute(
            """
            INSERT INTO snapshots (master_id, scan_date, os_info, cpu_info, ram_go,
                fabricant, num_serie, domaine, software_json, total_software)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                master_id,
                scan_date,
                sys_info.get("os", ""),
                sys_info.get("cpu", ""),
                float(sys_info.get("ram", 0) or 0),
                sys_info.get("fabricant", ""),
                sys_info.get("num_serie", ""),
                sys_info.get("domaine", ""),
                json.dumps(software, ensure_ascii=False),
                len(software),
            ),
        )
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
    diff = _build_software_diff(latest_sw, software)
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
    db.execute("DELETE FROM software_flags WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM snapshots WHERE master_id = ?", (master_id,))
    db.execute("DELETE FROM masters WHERE id = ?", (master_id,))
    db.commit()
    flash("Master supprime", "success")
    return redirect(url_for("index"))


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
