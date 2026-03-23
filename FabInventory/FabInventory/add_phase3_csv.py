#!/usr/bin/env python3
"""
Adds Phase 3 CSV export functionality to FabInventory app.py
1. Adds software_category column to search CSV export
2. Adds new /master/<id>/export endpoint
"""

import re

# Read the original file
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Step 1: Add software_category to search CSV export
# Find the search CSV export section and add the category column
search_csv_pattern = r'(if export_format == "csv" and query:.*?writer\.writerow\(\[(.*?"software_source".*?)\]\))'

# Find the row writing loop and add category
old_headers = '''writer.writerow([
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
            ])'''

new_headers = '''writer.writerow([
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
            ])'''

content = content.replace(old_headers, new_headers)

# Step 2: Add the master_export route after master_detail
master_export_route = '''

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
            s.created_at as scan_date
        FROM software_index si
        LEFT JOIN software_flags sf ON sf.master_id = ? AND sf.software_name = si.software_name
        LEFT JOIN snapshots s ON s.id = si.snapshot_id
        WHERE si.master_id = ?
        ORDER BY si.software_name ASC
    """, (master_id, master_id)).fetchall()

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
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=FabInventory-{master['pc_name']}.csv"
        },
    )
'''

# Find the return render_template line in master_detail and add the route after it
pattern = r'(return render_template\("master\.html", master=master, snapshots=snapshots,\s*software=software, flags=flags, latest=latest\))\s+(@app\.route\("/master/<int:master_id>/roadmap"\))'
replacement = r'\1' + master_export_route + r'\n\2'

content = re.sub(pattern, replacement, content)

# Write the modified file
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Phase 3 CSV export functionality added to app.py")
print("   - Added software_category to search CSV export")
print("   - Added /master/<id>/export endpoint")
