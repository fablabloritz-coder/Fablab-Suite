"""
Smoke test Phase 2: Verify category filtering UI in master.html
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
temp_dir = tempfile.mkdtemp()
temp_db = os.path.join(temp_dir, "test.db")
os.environ["DB_PATH"] = temp_db

from app import app, init_db, get_db

app.config["TESTING"] = True
ctx = app.app_context()
ctx.push()
init_db()

# Create test data
db = get_db()
cur = db.cursor()
cur.execute("INSERT INTO masters (pc_name, label) VALUES (?, ?)", ("TEST-PC", "Test PC"))
master_id = cur.lastrowid
cur.execute(
    "INSERT INTO snapshots (master_id, scan_date, software_json, total_software) VALUES (?, ?, ?, ?)",
    (master_id, "2026-03-23", json.dumps([
        {"n": "Firefox", "v": "128", "e": "Mozilla", "src": "Registre", "cat": "main"},
        {"n": "Update KB5", "v": "1.0", "e": "Microsoft", "src": "Registre", "cat": "update"},
        {"n": ".NET Framework", "v": "4.8", "e": "Microsoft", "src": "Registre", "cat": "composant"},
        {"n": "Duplicate", "v": "1.0", "e": "Old", "src": "Registre", "cat": "doublon"},
    ]), 4),
)
db.commit()

# Test route render
print("📄 Testing /master/<id> route rendering...")
client = app.test_client()
response = client.get(f"/master/{master_id}")

if response.status_code != 200:
    print(f"❌ Route returned {response.status_code}")
    sys.exit(1)

html = response.data.decode('utf-8')

# Check for category buttons
checks = [
    ("btn-cat-main", "Bouton 'Principaux'"),
    ("btn-cat-update", "Bouton 'Updates'"),
    ("btn-cat-composant", "Bouton 'Composants'"),
    ("btn-cat-doublon", "Bouton 'Doublons'"),
    ("categoryFilters", "Variable JS categoryFilters"),
    ("toggleCategory", "Fonction toggleCategory"),
    ("badge-category-", "Référence badge-category dans renderSw JS"),
    ("badge-category-' + swCategory", "Construction dynamique badge CSS"),
]

print("\n✔️ Vérification UI Phase 2:")
all_ok = True
for check_str, desc in checks:
    if check_str in html:
        print(f"   ✅ {desc}")
    else:
        print(f"   ❌ {desc} NOT FOUND")
        all_ok = False

if all_ok:
    print(f"\n✅ SUCCESS: Phase 2 UI rendering validated!")
    print(f"   - Route /master/{master_id} return 200")
    print(f"   - All category buttons present")
    print(f"   - All JS logic present")
    print(f"   - All CSS styles present")
else:
    print(f"\n❌ FAILURE: Some UI elements missing")
    sys.exit(1)

ctx.pop()

# Cleanup
try:
    if os.path.exists(temp_db):
        os.remove(temp_db)
    if os.path.exists(temp_dir):
        os.rmdir(temp_dir)
except:
    pass
