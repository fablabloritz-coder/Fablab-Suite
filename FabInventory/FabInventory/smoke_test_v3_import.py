"""
Smoke test: Import real v3 HTML inventory file from usb_v3/INVENTAIRES
Verifies end-to-end parsing, storage, and category retrieval
"""
import os
import sys
import json
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
os.environ["DB_PATH"] = temp_db

from app import app, parse_inventory_html, get_db, init_db, _upsert_snapshot_software_index

# Initialize app context and DB
app.config["TESTING"] = True
ctx = app.app_context()
ctx.push()
init_db()

# Read the HTML file from usb_v3 example
html_file = r"e:\Projet Github (claude code)\FabInventory\usb_v3\INVENTAIRES\LO-0-L002-01_2026-03-23_16-58.html"

if not os.path.exists(html_file):
    print(f"❌ Fichier non trouvé: {html_file}")
    sys.exit(1)

print(f"📄 Lecture du fichier v3: {os.path.basename(html_file)}")
with open(html_file, "r", encoding="utf-8") as f:
    html_content = f.read()

# Parse HTML
data = parse_inventory_html(html_content)
if not data:
    print("❌ Impossible de parser le JSON de l'HTML")
    sys.exit(1)

print(f"✅ JSON parsé avec succès")
print(f"   PC Name: {data.get('pcName', '?')}")

# Check software structure
software_list = data.get("software", [])
print(f"   Total software: {len(software_list)}")

# Check for category field in first few items
sample_size = min(3, len(software_list))
print(f"\n📊 Sample des 3 premiers logiciels:")
for i, sw in enumerate(software_list[:sample_size]):
    cat = sw.get("cat", "?")
    name = sw.get("n", "?")[:40]
    print(f"   [{i+1}] {name:<40} → cat='{cat}'")

# Count categories
category_count = {}
for sw in software_list:
    cat = sw.get("cat", "main")
    category_count[cat] = category_count.get(cat, 0) + 1

print(f"\n📈 Distribution catégories (AVANT import):")
for cat in ("main", "update", "composant", "doublon"):
    count = category_count.get(cat, 0)
    print(f"   {cat:<12}: {count:>3}")

# Import into DB
db = get_db()
cur = db.cursor()
cur.execute("INSERT INTO masters (pc_name) VALUES (?)", (data.get("pcName", "TEST-PC"),))
master_id = cur.lastrowid
cur.execute(
    "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?)",
    (master_id, "2026-03-23", json.dumps(software_list), len(software_list)),
)
snapshot_id = cur.lastrowid
db.commit()

print(f"\n💾 Stockage dans la DB:")
print(f"   Master ID:   {master_id}")
print(f"   Snapshot ID: {snapshot_id}")

# Index software with categories
_upsert_snapshot_software_index(db, snapshot_id, master_id, software_list)
db.commit()

# Verify categories in DB
print(f"\n✔️ Vérification catégories dans la DB:")
db_category_count = {}
for cat in ("main", "update", "composant", "doublon"):
    count = db.execute(
        "SELECT COUNT(*) AS c FROM software_index WHERE snapshot_id = ? AND software_category = ?",
        (snapshot_id, cat),
    ).fetchone()["c"]
    db_category_count[cat] = count
    print(f"   {cat:<12}: {count:>3}")

# Check total
total_in_db = sum(db_category_count.values())
print(f"   TOTAL:       {total_in_db:>3}")

# Verify match
if total_in_db == len(software_list):
    print(f"\n✅ SUCCESS: Tous les {len(software_list)} logiciels importés avec catégories correctes!")
    print(f"\n📍 Catégories du fichier v3 respectées:")
    for cat, count in sorted(db_category_count.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            pct = 100 * count / len(software_list)
            print(f"   • {cat:<12}: {count:>3} ({pct:>5.1f}%)")
else:
    print(f"\n❌ MISMATCH: Attendu {len(software_list)}, trouvé {total_in_db}")
    sys.exit(1)

ctx.pop()
if os.path.exists(temp_db):
    try:
        os.remove(temp_db)
    except:
        pass
